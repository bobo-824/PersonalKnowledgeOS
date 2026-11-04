# AI_WORKFLOWS —— AI 工作流设计

> **状态：阶段 2 完成（2026-11-04）。工作流 A/B/C 从界面到数据库全通，
> 已对真实 DeepSeek 全链路联调；七条硬约束有测试锁定（第⑨步变异测试验证过）。**
> 阶段 3（复习提示）和阶段 4（关联判断）仍在设计阶段，代码里没有对应调用。
>
> 实现落点：`backend/services/ai/`（Provider 协议、httpx 实现、假实现、提示词、工厂）、
> `backend/services/ai_service.py`（编排与事务边界）、`backend/api/ai.py`（12 个端点）、
> `backend/schemas/ai.py`（C3 校验边界）、`backend/services/settings_service.py`（写 `.env`）、
> 前端 `frontend/src/lib/ai-api.ts`（SSE 流式）+ `frontend/src/components/ai/`（草稿确认、审查区）。
> 已偏离本文件原设计的地方都记在 `DECISIONS.md`：D-044（失败五类）、D-045（JSON mode 默认关）、
> D-047/D-048（AI 输出的校验松紧）、D-053~D-059（接口层、`.env` 写入与配置校验）。
>
> 本文件记录的是**约束和设计意图**，不是实现细节。
> 继续实现时必须回来更新这个文件，并逐条对照下面的"硬约束"。

---

## 零、硬约束（先读这个）

这七条来自 Blueprint 1.5 的产品原则，**优先级高于任何实现便利性**。
每条都对应一个具体的技术后果，不是口号。

| # | 约束 | 技术后果 |
|---|---|---|
| C1 | **AI 不得覆盖用户原文** | `knowledge_versions.user_understanding` 列**永远不接受来自 AI 路径的 UPDATE**。AI 建议写 `ai_revision` 列；采纳 = 新建版本（`created_by='ai_confirmed'`） |
| C2 | **AI 不能直接创建知识** | `created_by` 枚举里**没有 `ai`**。所有入库路径都必须经过一个用户确认动作 |
| C3 | **AI 输出必须过后端 Schema 校验** | 用 Pydantic 模型接 AI 的结构化输出，校验失败按"AI 不可用"降级，**不能把畸形数据写库** |
| C4 | **AI 服务不可用时核心功能照常** | 手动记录、浏览、搜索、复习列表**不得依赖 AI**。AI 调用不能出现在这些路径上 |
| C5 | **最小必要传输** | 只发当前知识单元的相关字段，**不发全库、不发无关历史版本** |
| C6 | **Key 不出后端** | 前端只拿到 `ai_configured: bool`。日志不记 key，不记不必要的知识正文 |
| C7 | **AI 建议不具权威性** | 用户不采纳时**不弹警告、不标红、不降级体验**。拒绝是一个普通操作 |

### C4 的具体含义

这条决定了架构：**AI 集成必须是旁路，不能是主干。**

```
       ┌─── 核心路径（无 AI 也能跑）──────────────────┐
用户 ──┤  记录知识 / 浏览 / 搜索 / 归档 / 复习列表     │──> SQLite
       └──────────────────────────────────────────────┘

       ┌─── AI 旁路（可选，失败不影响核心）────────────┐
用户 ──┤  对话 / 提取草稿 / 审查建议 / 关联候选        │──> AI Provider
       └───────────────────┬──────────────────────────┘
                           │ 用户确认后
                           ▼
                        才写入 SQLite
```

AI 调用的结果**先进内存里的草稿对象**，用户确认后才落库。
不存在"AI 直接写数据库"的路径。

---

## 一、Provider 抽象层（阶段 2）

### 目标

支持 OpenAI API 及其兼容接口，**不锁定单一供应商**。
用户可以填任何兼容 OpenAI Chat Completions 格式的本地/远程服务
（Ollama、LM Studio、vLLM、各种国产大模型的兼容端点）。

### 接口形状（待实现时定稿）

```
backend/services/ai/
├── provider.py       AiProvider 协议 + ChatMessage + AiResult（校验过的结果带 token/耗时）
├── openai_compat.py  HTTP 实现，用 httpx，支持自定义 base_url
├── parsing.py        解析 + Pydantic 校验，真实实现和假实现共用（D-046）
├── fake.py           可编程假 Provider：全部测试靠它，不联网不需要 key
└── errors.py         AiUnavailable / AiTimeout / AiAuthRejected / AiBadRequest / AiSchemaInvalid
```

关键设计点：

- **`chat()` 返回 `AiResult`，里面装着已校验的 Pydantic 模型**。
  解析和校验在 provider 层完成，调用方拿到的永远是合法结构。
  这样 C3 就落在一处，不会每个工作流各写一遍解析。
  包一层而不是直接返回模型，是因为 `usage` 里的 token 数只在 HTTP 响应里，
  而 `ai_messages` / `ai_reviews` 都有 token 和耗时三列要填（D-046 顺带解决）。
- **超时来自 `PKOS_AI_TIMEOUT_SECONDS`（默认 60 秒）**。
  AI 慢是常态，超时值必须可配。
  **建连超时单独收紧到 10 秒**：主机不可达时不该让"无法连接"这句话等满 60 秒。
- **失败分类要清楚**：网络不通 / 超时 / Key 无效 / **请求被拒** / 返回畸形内容。
  每类给用户的提示不一样，因为**处置不一样**。统一报"AI 出错了"等于没报。
  ⚠️ 比本节原稿多了一类 `AiBadRequest`：模型名写错返回 400、
  base_url 少写 `/v1` 返回 404，这是新手最常见的两个错，
  并进"无法连接"会把排查方向彻底带偏。理由见 **DECISIONS D-044**。
- **不自动重试**（原稿写的是"不自动重试写操作"，实现里**读操作也不重试**）。
  写操作重试会造成重复记录（Blueprint 阶段 2 验收项明确禁止）；
  读操作不重试是因为个人本地应用里，
  「一次失败 + 一句明确报错」比「悄悄重试三次才报错」更好向用户解释。
- **URL 手工拼接，不交给 httpx 的 `base_url`**：
  httpx 拼路径是字节相加，`base_url="https://x/v1"` 配 `/chat/completions`
  会得到 `https://x/v1chat/completions`。这个坑静默发生、只有真机联调才暴露。
- **`response_format` 默认不带**（偏离本节原稿的"优先用 JSON mode"），
  由 `PKOS_AI_JSON_MODE` 控制。理由见 **DECISIONS D-045**。

### 设置页（阶段 2）

`/settings` 需要能配：API 地址、密钥、模型名、超时。

- 密钥**只在后端**读写，前端表单提交后不回显完整值
  （最多显示 `sk-...后四位`）
- 需要一个「测试连接」按钮，让用户在保存前知道配置对不对
- **Key 写进 `.env`（已在 `.gitignore`），不写进数据库**
  —— 数据库文件将来会被导出/备份，key 混在里面会跟着泄漏

---

## 二、工作流 A：自由对话（阶段 2）

页面：`/study`

```
用户提问 ──> AiProvider.chat() ──> 流式或整体返回 ──> 展示
                                      │
                                      └─> 存 ai_messages（独立表）
```

要点：

- 对话记录**独立存储**，不混进 `knowledge_versions`
- 系统提示词要贯彻原则 1：**引导用户自己说清楚理解，而不是直接给答案**
- 「从这段对话提取知识」是一个**显式按钮**，不是自动行为

---

## 三、工作流 B：知识提取（阶段 2）

**这是最容易违反 C1/C2 的地方，必须严格按流程走。**

```
① 用户与 AI 对话
        │
② 用户点「提取为知识」
        │
③ AI 输出结构化草稿（过 Pydantic 校验）
   {
     title, domain, summary,
     user_understanding,      ← 来自【用户说过的话】
     ai_supplements,          ← 来自【AI 补充】，必须分开
     reasoning, open_questions,
     suggested_sources
   }
        │
④ 前端展示草稿，【两个来源视觉上明确区分】
   用户可以逐字段编辑、删除、拒绝
        │
⑤ 用户点「确认保存」
        │
⑥ 才调 knowledge_service.create_knowledge()
   写入 knowledge (source_type='conversation') + knowledge_versions v1
   (created_by='user' —— 因为这是用户确认过的，不是 AI 自己写的)
```

### 关键设计决定

- **草稿阶段不落库**。③④⑤ 之间的数据只在前端/内存里。
  用户中途关掉页面，什么都不留。这既是 C2 的要求，也避免了垃圾数据堆积。
- **`ai_supplements` 和 `user_understanding` 必须是两个字段**。
  合并成一个字段就没法在 UI 上区分来源，原则 1 落空。
- **确认后 `created_by = 'user'`，不是 `'ai_confirmed'`**。
  区分：`ai_confirmed` 专指"对已有知识采纳 AI 修订建议"（工作流 C），
  而这里是从对话新建知识，用户是内容的实际所有者。
  这个语义区别要在阶段 2 实现时再确认一次，写进代码注释。
- **`knowledge.source_type = 'conversation'`**，
  标记这条知识的来源是对话而不是手动录入。列表页可以据此筛选。
- **防重复提交**：按钮在请求期间 disabled，
  且提交携带一个客户端生成的幂等键（Blueprint 阶段 2 验收项）。

---

## 四、工作流 C：知识审查（阶段 2）

**目标**：检验用户是否真的理解，指出错误和遗漏。

```
① 用户在知识详情页点「请 AI 审查我的理解」
        │
② 后端组装请求：只发【当前版本】的相关字段（C5 最小必要）
   · title, domain
   · user_understanding, reasoning, open_questions
   【不发】历史版本、其他知识、来源全文
        │
③ AI 输出结构化审查结果（过 Pydantic 校验）
   {
     correct_points:   [ {point, why} ],      正确点
     errors:           [ {claim, correction, evidence} ],   错误
     omissions:        [ {missing, why_it_matters} ],       遗漏
     follow_up_questions: [ question ],       追问
     needs_verification:  [ {item, how_to_verify} ]   待核实
   }
        │
④ 独立存储（不写进 knowledge_versions 的任何现有行）
        │
⑤ 前端展示，用户对每一条可以：采纳 / 忽略 / 标记不同意
        │
⑥ 用户采纳若干条 → 编辑自己的理解 → 保存
        │
⑦ 新建 knowledge_versions 行：
   · user_understanding = 【用户编辑后的新版本】
   · ai_revision        = 【AI 原始建议，一字不改地留档】
   · change_reason      = "采纳 AI 审查建议"
   · created_by         = 'ai_confirmed'
   · v1..v(n-1) 全部不变
```

### 为什么 `ai_revision` 要留档而不是用完就丢

三个月后用户回看这条知识，会想知道：
"我当初为什么改了这个理解？是 AI 说的还是我自己想到的？AI 当时说了什么？"
只有把 AI 的原始建议存下来，这个成长轨迹才是完整的。
这也是 Blueprint 成长追踪能力的数据基础。

### 审查结果的五分类不能简化

`needs_verification`（待核实）这一类特别重要：
它承认**AI 自己也不确定**。如果只有"对/错"两类，
AI 会被迫在不确定的情况下也给出确定判断，这正是原则 6
（AI 反馈不是绝对权威）要防止的。

---

## 五、工作流 D：对话式复习（阶段 3）

**这是原则 3（主动回忆）和原则 6（用户确认评级）的核心落点。**

```
① 复习中心列出到期知识（按 review_schedules.due，纯数据库查询，无 AI）
        │
② 用户点开一条 → AI 生成一个提问
   【硬约束：不得泄露答案，不得给选项】
        │
③ 用户用自己的话回答（文本输入，不是选择题）
        │
④ AI 评估回答
   ├─ 不完整 → 继续追问（回到 ③）  【不得直接给答案】
   └─ 完整   → 给出评价 + 【建议评级】
        │
⑤ 用户看到 AI 建议评级，然后【自己确认或修改】
   again / hard / good / easy
        │
⑥ 用【用户最终评级】调 FSRS Scheduler，更新 review_schedules
   写入 review_sessions：问题、回答、AI 评价、AI 建议评级、
                        用户最终评级、耗时、下次复习时间
```

### 为什么第 ⑤ 步不能省

如果直接用 AI 建议的评级去更新 FSRS 计划，那么：

- AI 误判 → 复习间隔算错 → 用户要么被过度复习（疲劳），要么真的忘了
- 用户失去对自己学习节奏的控制权，违反原则 6
- **数据上无法区分"AI 觉得我记得"和"我确实记得"**，
  成长追踪就成了 AI 的评价记录而不是用户的学习记录

所以 `review_sessions` 必须**同时**存 AI 建议评级和用户最终评级两列。
两者的差异本身是有价值的数据（AI 是否系统性地高估/低估了这个用户）。

### FSRS 集成注意点

```python
from fsrs import Scheduler
scheduler = Scheduler(enable_fuzzing=False)   # 测试必须关掉抖动
```

- **`enable_fuzzing=False`**：FSRS 默认给复习间隔加随机抖动
  （防止大量卡片同一天到期）。生产环境可以开，**测试必须关**，
  否则断言不确定。
- **字段映射缺口**（已记录在 `ROADMAP.md` 阶段 3 和 `DATA_MODEL.md`）：
  `fsrs` 6.3.2 的 `Card` 只有
  `card_id, state, step, stability, difficulty, due, last_review`，
  而 Blueprint 的 `review_schedules` 还要
  `reps, lapses, elapsed_days, scheduled_days`。
  这四个必须从 `review_sessions` 推算：

  | 字段 | 推算方式 |
  |---|---|
  | `reps` | 该知识单元的 `review_sessions` 行数 |
  | `lapses` | 其中用户最终评级为 `again` 的行数 |
  | `elapsed_days` | 本次 `reviewed_at` − 上次 `reviewed_at` |
  | `scheduled_days` | 本次 `due` − 上次 `reviewed_at` |

- **时区**：FSRS 的 `due` 是 datetime。全链路必须用
  `backend.db.base.utcnow()`，`UTCDateTime` 会在写库时拒绝 naive 值。
  这是 Blueprint 9.2「系统时间变化、跨日和时区转换不会导致复习日期错乱」
  的技术保证。

### 实现落地（阶段 3 第⑤步，2026-11-04）

上图是设计，落到代码后有三处与流程图**刻意不同**，都是省一次请求费或守约束：

1. **评价与建议合成一次调用**。流程图的 ④"评估回答"和"建议评级"没有拆成
   两轮对话——`stream_reply` 追问时按会话 `kind` 自动用 `review_chat`
   提示词（不提前泄露答案、一次只问一个、不给评级），用户觉得有数了就
   主动点"要评级建议"，后端把对话原文 + 当前理解收尾发给 AI，
   用 `review_rate` 提示词要一个 `RatingSuggestion` JSON
   （`suggested_rating` + `evaluation`），过 C3 校验后返回。
2. **建议不落库（C7 的强化）**。`suggest_review_rating` 不写任何表；
   用户提交评级时才把建议作为 `suggested_rating` 存档进 `review_sessions`。
   排期计算只读 `rating`（服务层固定 now 的对照测试 + HTTP 层同秒对比测试锁定）。
3. **开场零残留**。`start_review_chat` 先流式拿到首问，成功后才建会话落库。
   AI 挂了什么都不留——如果留一个空会话壳在历史里，"降级成纯自测"就是在撒谎。

问答原文复用 `ai_conversations`（`kind="review"`）/ `ai_messages`，
后续追问走学习室同一条 SSE 端点——前端不用第二套流式代码。
学习室列表按 `kind="chat"` 过滤，复习对话不混进聊天历史；
`review_sessions.conversation_id` ON DELETE SET NULL：清空对话历史
删问答原文，评级记录保留（评级是用户的数据）。

---

## 六、工作流 E：关联判断（阶段 4）

```
① 用户新增/修改一条知识
        │
② 候选召回（不调 AI，纯本地检索）
   · 关键词重叠、FTS5 全文相似、（可选）向量相似
   · 【只召回少量最相关的，不做全库无差别扫描】
        │
③ 对每一对候选，AI 判断是否真的相关，输出：
   {
     relation_type,    关系类型（前置知识 / 类比 / 对比 / 应用 / 组成 ...）
     explanation,      为什么相关
     evidence,         依据（引用双方的具体内容）
     confidence        置信度
   }
        │
④ 写入 knowledge_relations，status = 'pending'
        │
⑤ 待审核列表：用户 确认 / 拒绝 / 修改类型 / 暂时忽略
        │
⑥ status → 'accepted' 才进入正式图谱
```

### 硬要求

- **正式知识图谱只显示 `accepted` 的关系**（原则 4）
- **被拒绝的候选保留审核记录，不会重复骚扰**
  —— `rejected` 状态要持久保存，不能删掉，否则下次新增知识时
  AI 会把同一对关系再提一遍，用户会疯
- **没有 `explanation` 和 `evidence` 的候选不接受**（Pydantic 必填）。
  用户无法判断一个没有理由的关联，等于把审核变成盲签
- **`confidence` 只用于排序**，不作为自动通过的依据。
  再高的置信度也要用户点一下

### 实现落地（2026-11-04，阶段 4 完成）

1. **召回改成了 3 字滑窗 OR 查询（D-064 的具体形状）**。trigram 的 MATCH
   是精确子串匹配，「梯度下降的直觉」查不到「梯度下降的数学」；
   标题（和理解开头 60 字）取 3 字滑窗、采样封顶 12 个词做 OR，
   共享 ≥3 连字即可互相命中——中文里共享 3 连字基本等于共享一个词。
   2 字以下标题走 LIKE 扫权威表（与主搜索同一条短路规则）。
2. **候选不落库**。召回实时计算，不建队列表也不挂钩子：
   `knowledge_relations` 的 explanation/evidence 是 NOT NULL，
   只有 AI 判断过的东西才配有一行。新知识天然就在候选池里，
   「新增/修改后自动入队」因此不需要任何动作。
3. **防重复骚扰两道锁**：召回侧用 `canonical_pair`（方向无关键）排除
   任何状态的既有对；数据库侧 `uq_knowledge_relations_pair` 兜底并发窗口。
   AI 说 `is_related=false` 的**不落行**——下次召回还会出现，这是有意的：
   召回器改进后同一对值得再判一次，和 rejected 的"永久安静"不同。
4. **批量判断逐对、失败不阻断（D-042）**。前端点按钮前按对数明示请求数；
   后端每对一次请求，单对失败记进报告继续下一对，HTTP 仍 200
   （"部分成功"是正常结果）。方向由 AI 的 `direction` 字段定，
   落库前翻转到真实的 source/target。
5. **六类固定词表 + 自定义类型（D-065）**。词表与状态机在
   `backend/domain/relation.py`（纯函数，无框架依赖）；
   提示词、Pydantic、前端 zod 三处各有一份词表拷贝，测试两头锁死。
   自定义类型干净性（非空、无空白、≤32 字）由 `is_valid_relation_type` 校验。

---

## 七、结构化输出与校验策略（C3）

### 两阶段校验

```
AI 原始返回（字符串）
      │
① 格式解析：能不能解析成 JSON
      │  失败 → AiSchemaInvalid，提示用户重试，不写库
      ▼
② Pydantic 校验：字段齐不齐、类型对不对、枚举值合不合法
      │  失败 → AiSchemaInvalid，把【哪个字段不合法】记进日志（不记正文）
      ▼
   合法的结构化对象 → 交给工作流
```

### 实现建议

- ~~优先用 provider 的 JSON mode / structured output 能力~~
  → **实现成默认关闭**（`PKOS_AI_JSON_MODE=false`，确认后手动开）。
  兼容端点对 `response_format` 的支持参差不齐，贸然无条件带上会让所有请求一起
  400；而在用户填入真实 key 之前无法验证他那家支不支持。见 **DECISIONS D-045**
- 但**不能假设它一定返回合法 JSON**，② 这一步永远要有 ——
  正因为 JSON mode 默认关着，这一条从"兜底"升级成了**唯一保障**
- 校验失败的原始返回**不要存进数据库**（可能包含意外内容），
  只在日志里记字段名和错误类型。
  ⚠️ 落地时有个具体的坑：Pydantic 的 `ValidationError.errors()` 里
  **`input` 字段装着实际收到的值**，那可能就是用户的知识正文。
  所以取字段路径时**只读 `loc`，绝不读 `input`**
  （`parsing._invalid_field_names`，有专门的测试盯着这条）
- ⚠️ **两条本节原稿没写、但实现时必须定的规则**：
  1. AI 输出的模型要 `extra="ignore"`，不能像面向人的接口那样 `forbid`。
     模型多返回一个字段时重试修不好，forbid 会让整条工作流对那家服务商永久不可用。
     见 **D-047**
  2. **草稿校验比落库校验松**：`user_understanding` 在草稿里允许空。
     草稿层就要求非空，等于**逼 AI 编一段理解出来**才能过校验，
     而那段编的话之后会被当成用户自己的东西保存。见 **D-048**

### 提示词管理

系统提示词应该：

- 放在后端，不进前端代码（防止被随手改坏，也便于版本管理）
- 明确写入七条原则里与该工作流相关的部分
  （比如复习工作流的提示词里必须写"不得提前泄露答案"）
- 版本化：提示词改了会影响 AI 行为，将来排查"为什么审查结果变差了"
  需要知道当时用的是哪一版

---

## 八、失败与降级

| 情况 | 异常类 | 用户能做什么 |
|---|---|---|
| 未配置 Key | （不构造 Provider） | `/api/health` 的 `ai_configured: false`；全部手动功能照常，AI 按钮显示"未配置"并链到设置页 |
| 连不上（DNS/代理/对方宕机/连接被重置/**建连超时**） | `AiUnavailable` | 明确报"无法连接 AI 服务，请检查网络或代理"，附 base_url（不含 key） |
| 连上了但响应超时 | `AiTimeout` | 明确报"响应超时"。**重试往往有效**，或去设置页调大超时 |
| Key 被拒（401/403） | `AiAuthRejected` | 明确报"API Key 被拒绝"。去设置页改——**重试永远没用** |
| 请求被拒（其他 4xx，含 400/404） | `AiBadRequest` | 明确报"AI 服务拒绝了这个请求，请检查模型名和 API 地址"，附状态码。**去查设置，不是查网络**（D-044） |
| 返回畸形内容（不是 JSON / 缺字段 / 类型错 / 枚举非法） | `AiSchemaInvalid` | 报"AI 返回内容无法解析"。重试。**不写库**（C3） |
| 模型能力不足 | 表现为反复 `AiSchemaInvalid` | 提示用户换模型 |

⚠️ **建连超时故意归进 `AiUnavailable` 而不是 `AiTimeout`**：
`httpx.ConnectTimeout` 同时继承这两个基类，按"超时"分类会引导用户去做一件
没用的事（调大超时秒数），而真正的问题是连不上。捕获顺序必须先判它。

**核心要求（C4）**：以上任何一种情况，
**手动记录知识、浏览已有知识、搜索、查看版本历史、复习列表，全部照常可用。**

这条要有测试覆盖：把 `PKOS_AI_API_KEY` 设成一个必然失败的值，
断言知识 CRUD 接口全部返回 200。

---

## 九、日志与隐私（C6）

Blueprint 9.1：**日志不得记录完整 API Key 或不必要的私人知识正文。**

具体规则：

| 可以记 | 不可以记 |
|---|---|
| 请求耗时、模型名、token 数 | API Key（任何片段） |
| 校验失败的**字段名**和错误类型 | 用户知识正文 |
| 工作流类型、成功/失败 | AI 返回的完整内容 |
| HTTP 状态码 | 请求头 |

已经落地的部分：`/api/health` 只返回 `ai_configured: bool`，
测试 `test_health_never_leaks_api_key` 用哨兵值
`sk-TEST-DO-NOT-LEAK-1234567890` 和 `https://example.invalid/v1`
断言两者都不出现在响应全文里。

**阶段 2 实现 AI 调用时必须复查一遍日志输出**，
尤其是异常处理里的 `logger.exception()` ——
它默认会把整个 traceback 打出来，而 traceback 里可能包含局部变量。

---

## 十、阶段 2 开工前要确认的事

**2026-10-31 已向用户确认完毕**，结论落在 `DECISIONS.md` 第九节（D-038 ~ D-042）。
下面每条后面标注结果，保留原文以便对照。

1. **默认 Provider**：只实现 OpenAI 兼容协议够不够？
   用户实际打算用哪家服务（决定要不要测特定的兼容端点）
   → **已定（D-038）**：只实现 OpenAI 兼容协议，不写多家适配。
   **用户暂不选定供应商**（"代码写完再说"），所以：
   测试一律用假 Provider，不真调用网络；
   设置页必须能填 base_url / key / model / 超时并带「测试连接」按钮。
   ⚠️ 后果：在用户填入真实 key 之前，阶段 2 **无法做真机联调验收**。
2. **流式响应**：对话要不要做流式（打字机效果）？
   做了体验好但复杂度和错误处理都上升
   → **已定（D-039）**：**只对自由对话做流式**；
   知识提取与审查返回 JSON，必须整体拿到才能校验，不做流式。
3. **向量检索**：阶段 4 的候选召回要不要引入 embedding？
   要的话需要额外的本地模型或 API 调用，与"最小必要传输"原则需要权衡
   → **未确认（D-040）**：agent 建议推迟到阶段 4，用户本轮未表态。
   **阶段 4 开工前必须重新问**，不要当成已确认决策。
4. **对话历史保留多久**：`ai_messages` 会持续增长，
   要不要有清理策略（Blueprint 没规定）
   → **已定（D-041）**：永久保留 + 设置页一个显式的手动「清空对话历史」按钮。
   不自动删（自动删会断掉"知识 ← 对话"的溯源）。
   这是全项目第一个硬删除接口，删的是对话不是知识。
5. **AI 调用是否要花钱的确认**：每次审查/关联判断都是付费 API 调用，
   批量操作前要不要提示预估次数
   → **已定（D-042）**：**批量操作前提示"这次会调用 AI N 次"并等用户确认**。
   没选"硬性上限"方案；若日后误操作仍频繁再补。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| `PRD.md` | 七条产品原则与五项核心能力 |
| `DATA_MODEL.md` | 表结构，含阶段 2/3/4 的待建表与 FSRS 字段缺口 |
| `ROADMAP.md` | 各阶段任务与验收标准 |
| `DECISIONS.md` | 技术决策，含 API Key 边界（D-016）与 UTC 约束（D-011） |
| `ARCHITECTURE.md` | 分层、请求生命周期、安全边界 |
