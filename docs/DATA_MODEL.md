# DATA_MODEL —— 数据模型与字段说明

当前数据库版本：**0002**（0001 建知识库三表，0002 建阶段 2 的五张 AI 表）。
表结构由 `migrations/versions/` 下的迁移定义，
ORM 模型在 `backend/db/models/`，两者必须保持一致
（用 `uv run alembic check` 验证，不一致会报漂移；0002 是手写的，
写完立刻 check 过，实测无漂移）。

> **阶段 2 已收尾，五张 AI 表的字段级说明已补进本文件（见「阶段 2 的五张 AI 表」一节）。**

> 为什么这份说明放这里而不是 `alembic.ini`：Alembic 用**操作系统区域编码**
> （中文 Windows 是 GBK）读 `alembic.ini`，那个文件里写中文会直接
> `UnicodeDecodeError` 崩溃。详见 `DECISIONS.md` D-008。

---

## 总览

```
knowledge (知识单元主记录)
   │  1
   │
   ├──< knowledge_versions (理解的历史版本，追加式，永不覆盖)
   │       每个知识单元有 1..N 个版本，version_number 从 1 递增
   │       knowledge.current_version_id ──┐ 循环外键
   │                                      └──> 指向"当前生效"的那一版
   │
   └──< sources (学习来源与引用)
           一个知识单元可以来自多个来源

knowledge_fts (FTS5 虚拟表，全文检索索引，独立表)
   索引 knowledge.title + knowledge_versions.user_understanding

ai_conversations (AI 对话会话)                 ← 迁移 0002，阶段 2
   │  1
   └──< ai_messages (单条消息，会话内 seq 递增)
           数据库级 ON DELETE CASCADE，清空对话历史靠它

ai_reviews (一次 AI 审查的结果，独立存储)       ← 迁移 0002，阶段 2
   │  指向 knowledge + knowledge_versions（审的是哪一版理解）
   │  1
   └──< ai_review_decisions (用户对其中某一条的采纳/忽略/不同意)

idempotency_keys (幂等键 → 已创建的知识)        ← 迁移 0002，阶段 2
   挡住"确认保存"被重复提交
```

**AI 相关的表全是旁路，没有一条核心路径依赖它们**（约束 C4）。
知识主表和版本表里没有任何指向 AI 表的外键，
所以把五张 AI 表全删掉，知识库照样能用。

**核心设计**：知识单元本身只存"元数据 + 指向当前版本的指针"，
**所有理解正文都在 `knowledge_versions` 里**。
这是"用户原始理解永不被覆盖"（Blueprint 1.5 / 7.1）在数据结构上的保证：
修改理解 = 追加一行新版本，而不是 UPDATE 旧行。旧版本物理上不可能被改掉。

---

## 表：`knowledge`

知识单元主记录。存元数据和"当前是哪一版"的指针，**不存理解正文**。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | UUID 字符串。用 `str(uuid4())` 生成，存字符串而非 BLOB 是为了导出 JSON 时不用额外转换 |
| `title` | VARCHAR(200) | NOT NULL | 知识名称。Blueprint 5.1 的必填字段 |
| `domain` | VARCHAR(100) | 可空 | 所属领域，如"机器学习"、"操作系统"。自由文本，不做外键——个人知识库的领域分类会一直变，建字典表只会增加维护负担 |
| `summary` | TEXT | 可空 | 一句话摘要，列表页展示用 |
| `source_type` | VARCHAR(32) | NOT NULL，默认 `manual` | 这条知识怎么来的。取值：`manual`（手动记录）/ `conversation`（AI 对话中提取，需用户确认）。常量定义在 `backend/db/models/knowledge.py` |
| `status` | VARCHAR(16) | NOT NULL，默认 `active` | `active` / `archived`。**归档而非物理删除**（Blueprint 5.1）：archived 的不出现在默认列表，但数据完整保留 |
| `current_version_id` | VARCHAR(36) | 可空，FK → `knowledge_versions.id` | 指向当前生效的版本。可空是因为插入时版本还不存在（见下方"循环外键"） |
| `created_at` | DATETIME | NOT NULL | UTC，见下方"时间字段" |
| `updated_at` | DATETIME | NOT NULL | UTC，SQLAlchemy `onupdate=utcnow` 自动维护 |

**索引**：

| 索引名 | 列 | 用途 |
|---|---|---|
| `ix_knowledge_domain` | `domain` | 按领域浏览（Blueprint 阶段 4 验收项） |
| `ix_knowledge_status_updated_at` | `status, updated_at` | 默认列表查询是 `WHERE status='active' ORDER BY updated_at DESC`，复合索引正好覆盖 |

### 循环外键（`knowledge` ⇄ `knowledge_versions`）

`knowledge.current_version_id` 引用 `knowledge_versions.id`，
而 `knowledge_versions.knowledge_id` 又引用 `knowledge.id`。两张表互相依赖，
建表时**无论先建哪张，另一张都还不存在**。

解决方案（迁移 0001 里的实际顺序）：

1. 建 `knowledge`，**暂不含** `current_version_id` 的外键约束（列本身要建）
2. 建 `knowledge_versions`，含指向 `knowledge.id` 的外键
3. 回填 `knowledge.current_version_id` 的外键约束

第 3 步在 SQLite 上必须走 **batch 模式**（`op.batch_alter_table`），
因为 SQLite 不支持 `ALTER TABLE ADD CONSTRAINT`。batch 模式的做法是
建新表 → 搬数据 → 删旧表 → 改名。此时表是空的，代价为零。

ORM 侧对应两个配置：

- `ForeignKey(..., use_alter=True)` —— 告诉 Alembic 这条外键要延后创建
- `relationship(..., post_update=True)` —— 告诉 SQLAlchemy 插入时先写 `knowledge`
  拿到 UUID，再回填 `current_version_id`，不要在一条 INSERT 里就要求值

**写代码时的注意点**（测试里踩过）：`Knowledge.id` 的 UUID 是
`default=new_uuid`，**只在 flush 时才生成**。所以

```python
session.add(knowledge)
version = KnowledgeVersion(knowledge_id=knowledge.id, ...)  # ✗ knowledge.id 还是 None
```

会报 `NOT NULL constraint failed`。必须先 `session.flush()`：

```python
session.add(knowledge)
session.flush()                                              # ✓ 现在 knowledge.id 有值了
version = KnowledgeVersion(knowledge_id=knowledge.id, ...)
```

---

## 表：`knowledge_versions`

用户理解的历史版本。**追加式**：新版本永远是新行，旧行不动。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | UUID 字符串 |
| `knowledge_id` | VARCHAR(36) | NOT NULL，FK → `knowledge.id` | 属于哪个知识单元 |
| `version_number` | INTEGER | NOT NULL | 从 1 递增。**与 `knowledge_id` 组成唯一约束** |
| `user_understanding` | TEXT | NOT NULL | **用户原文。任何流程（包括 AI）都不得 UPDATE 这一列。** Blueprint 7.1 的硬验收项 |
| `reasoning` | TEXT | 可空 | 理解依据："我为什么这么认为" |
| `open_questions` | TEXT | 可空 | 尚存疑问。阶段 2 的 AI 审查会重点看这一列 |
| `ai_revision` | TEXT | 可空 | **AI 的修订建议，与用户原文物理隔离。** 存下来但不会自动生效；用户确认采纳后，会**新建一个版本**把采纳结果写进新版本的 `user_understanding`，本列的原始建议保留不动 |
| `change_reason` | VARCHAR(200) | 可空 | 这次为什么改 |
| `created_by` | VARCHAR(16) | NOT NULL，默认 `user` | `user`（用户自己写）/ `ai_confirmed`（用户确认采纳 AI 建议后生成的新版本）。**注意没有 `ai` 这个取值**——AI 不能直接创建版本，这是原则在数据层的体现 |
| `created_at` | DATETIME | NOT NULL | UTC |

**约束与索引**：

| 名称 | 内容 | 为什么重要 |
|---|---|---|
| `uq_knowledge_version_number` | UNIQUE(`knowledge_id`, `version_number`) | 防止并发或重试造成版本号重复。测试用 `IntegrityError` 验证过 |
| `fk_knowledge_versions_knowledge_id` | FK → `knowledge.id` | 防止孤儿版本。配合 `PRAGMA foreign_keys=ON` 才真正生效（SQLite 默认**不**强制外键） |
| `ix_knowledge_versions_knowledge_id` | `knowledge_id` | 查某个知识单元的版本列表 |

### 版本演进的完整例子

```
用户第一次记录"梯度下降"：
  v1  user_understanding = "沿着斜率最大的方向往下走"
      reasoning          = " intuition，没验证过"
      ai_revision        = NULL
      created_by         = "user"

AI 审查后指出遗漏，用户采纳：
  v1  （完全不变，原文永久保留）
  v2  user_understanding = "沿着负梯度方向按学习率迭代更新参数"
      reasoning          = "AI 指出需要明确学习率和参数更新"
      ai_revision        = "建议补充：学习率、迭代更新、负梯度方向"
      change_reason      = "采纳 AI 审查建议"
      created_by         = "ai_confirmed"

  knowledge.current_version_id → v2.id
```

**关键点**：v1 的 `user_understanding` 依然是那句不准确的原始理解，
一个字都没变。这正是 Blueprint 7.1 要的东西——能看到"我当初是怎么想的"，
以及"我是怎么想的改变的"。

测试 `test_original_understanding_survives_new_version` 就是断言这件事。

### 什么时候才创建新版本（已实现：`backend/domain/versioning.py`）

**规则**：`user_understanding`、`reasoning`、`open_questions`
三者**任一实际值发生变化**时，才创建新版本行。

改 `title` / `domain` / `summary` / `sources` 属于整理性工作，
**就地 UPDATE，不建版本**。

理由与已知取舍见 `DECISIONS.md` D-030。两条实现上的硬要求，都已落地并被测试锁住：

1. **必须先比对实际值，不能无脑建版本。** 否则"用户点了保存但什么都没改"
   也会让版本号 +1，制造出假历史。
   实现上把每个字段都 `strip()` 后再比：只在首尾加空格不算改动；
   但**显式传 null 把内容清空算改动**，而"字段根本没传"不算——
   这两者的区别只能靠 Pydantic 的 `model_fields_set` 判断，
   `model_dump()` 分不出来（两者都会变成 `None`）。
2. **`title` 不在版本表里，所以标题的演变历史留不下来。**
   这是用户明确知情并接受的取舍。如果日后要改，
   需要给 `knowledge_versions` 加标题快照列并写迁移。
   阶段 0 时数据库 0 行是最便宜的时机，现在也仍然很便宜——
   真实库里还没有用户数据。

版本号取 `MAX(version_number) + 1`，**不是 `len(versions) + 1`**。
两者在版本号连续时结果一样，一旦出现空洞（将来支持"恢复到旧版本"、
或手工修过数据）就会撞 `(knowledge_id, version_number)` 唯一约束。
`test_number_comes_from_the_maximum_not_the_row_count` 用原生 SQL
人为造出空洞来验证这一点。

没改动时 `PUT` 是幂等的：传 `{}` 返回的 body 与传入前完全一致，版本号不变。
改了理解字段但没写「修改说明」时，自动生成一条，
形如 `修改了：自己的理解、尚存疑问`（字段名用中文，用户看得懂）。

---

## 表：`sources`

学习来源与引用信息。一个知识单元可以有多个来源。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | UUID 字符串 |
| `knowledge_id` | VARCHAR(36) | NOT NULL，FK → `knowledge.id`，索引 | 属于哪个知识单元 |
| `source_type` | VARCHAR(32) | NOT NULL，默认 `other` | 来源类型，**固定词表**：`book` 书 / `article` 文章 / `video` 视频 / `course` 课程 / `podcast` 播客 / `paper` 论文 / `conversation` 对话 / `other` 其他。见下方说明 |
| `title` | VARCHAR(300) | NOT NULL | 来源标题 |
| `url_or_reference` | VARCHAR(1000) | 可空 | 网址或书籍页码等引用位置 |
| `note` | TEXT | 可空，**但 `source_type='other'` 时必填** | 备注。选"其他"时必须写清楚到底是什么来源，否则信息就丢了 |
| `created_at` | DATETIME | NOT NULL | UTC |

> **两套不同的 source_type 词表，不要混用**：
> - `knowledge.source_type` 描述**这条知识怎么进系统的**：
>   `manual`（手动记录）/ `conversation`（AI 对话中提取，需用户确认）
> - `sources.source_type` 描述**资料本身是什么形式**：
>   书 / 文章 / 视频 / 课程 / 播客 / 论文 / 对话 / 其他
>
> 取值约束放在 Pydantic schema 层（`backend/schemas/knowledge.py`），
> 不做数据库级 CHECK 约束，理由见 `DECISIONS.md` D-005 / D-029。
>
> **已落地**：`backend/db/models/source.py` 里 8 个来源类型都有模块级常量
> （`SOURCE_TYPE_BOOK` … `SOURCE_TYPE_OTHER`），默认值不再硬编码字符串；
> `backend/schemas/knowledge.py` 的 `SourceCreate` 上有跨字段校验，
> `source_type='other'` 而 `note` 为空时返回 422，
> 报文里带中文说明「来源类型选择「其他」时，备注必填：请写清楚具体是什么来源」。
> 前端在提交前也会拦住并给出同样的提示（`SourceEditor.tsx::sourceDraftError`），
> 但**后端才是边界**——前端那道只是体验，不是安全保证。

---

## 虚拟表：`knowledge_fts`（FTS5 全文检索）

```sql
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    knowledge_id UNINDEXED,
    title,
    body,
    tokenize='trigram'
);
```

| 列 | 说明 |
|---|---|
| `knowledge_id` | `UNINDEXED`：存着用于回表，但不参与分词检索 |
| `title` | 来自 `knowledge.title` |
| `body` | 来自**当前版本**的 `knowledge_versions.user_understanding` |

### 为什么是独立表而不是"外部内容表 + 触发器" ⚠️

**这一条偏离了最初向用户宣布的方案**，完整理由见 `DECISIONS.md` D-006。

简述：FTS5 的外部内容表（`content='<表名>'`）只能绑定**单一**表，
而这里要索引的内容横跨两张表——`knowledge.title` 和
`knowledge_versions.user_understanding`。所以只能用独立表。

**代价**：同步逻辑必须显式写在 `services/search_service.py` 里，
写入路径漏调同步函数就会索引不一致。

**已落地的保障**：

- `create_knowledge` / `update_knowledge` / `archive_knowledge` /
  `restore_knowledge` 四条写入路径全部调 `sync_index()`
- `TestIndexStaysInSync` 覆盖："改标题后能搜到新标题、搜不到旧标题"、
  连改 3 次后每条知识在 FTS 里**恰好 1 行**、索引正文 == 当前版本、归档后仍在索引里
- `rebuild_index()` 是修复入口：清空索引后重建，能补回丢失的行、
  替换过期正文、去掉重复索引
- **这个 bug 在数据库里看不出来**（版本行、标题列都是对的），
  只有走搜索 API 才暴露，所以测试必须打在 API 层而不只是 service 层

### 影子表

FTS5 会自动创建 5 张影子表：`knowledge_fts_data`、`knowledge_fts_idx`、
`knowledge_fts_content`、`knowledge_fts_docsize`、`knowledge_fts_config`。
它们**不在 ORM metadata 里**，所以 `migrations/env.py` 的 `_include_object`
过滤器按前缀 `knowledge_fts` 排除掉，否则 `alembic check` 会永远认为
这些表是多余的并要求 drop。

### 中文检索：trigram + LIKE 混合策略

`tokenize='trigram'` 是 SQLite 内置分词器里唯一对中文有效的，
但 **MATCH 查询要求至少 3 个字符**，2 字词直接返回空结果。
而「梯度」「复习」「熵」这类 1~2 字词在中文笔记里极其常见。

策略（已在 `backend/services/search_service.py` 实现）：

| 查询长度 | 走哪条路 | 实测性能 |
|---|---|---|
| ≥ 3 字符 | FTS5 `MATCH` | 索引命中，快 |
| 1~2 字符 | `LIKE '%xx%'` | 5000 条数据下 **1.0~1.4ms** |

**关键细节：LIKE 兜底查的是权威表（`knowledge` + `knowledge_versions`），
不是 `knowledge_fts`。** 否则索引一旦失步，短词搜索会跟着一起瞎——
而短词恰恰是中文里最常用的那批。
`test_short_query_is_immune_to_index_damage` 把 `knowledge_fts` 整个清空后断言：
4 字词「梯度下降」搜不到（total 0，符合预期，因为它依赖索引），
但 2 字词「梯度」和单字「挪」**照样能搜到**。

响应里带 `strategy` 字段（`fts` / `like`），前端据此告诉用户
「检索方式：全文索引」还是「关键词匹配（搜索词较短，走关键词匹配）」——
搜索行为不透明，用户会以为是自己的东西没存进去。

`matched_by` 字段（`title` / `body` / `both`）说明命中在哪，
`snippet` 给一段带命中位置的正文预览。

**索引里放什么**：`title` + 当前版本的 `user_understanding`。
刻意**不含** `reasoning` / `open_questions`——搜的是"我写过的理解"，
把依据和疑问也混进正文会让命中变得模糊，
所以只写在「尚存疑问」里的词句搜不到，这是取舍不是漏索引。

完整理由见 `DECISIONS.md` D-007。

---

## 阶段 2 的五张 AI 表（迁移 0002，已实现）

> 第⑩步补的字段级说明。每张表为什么必须存在，写在各自 ORM 模型文件顶部注释里；
> 三张超出 Blueprint 原计划的表的动机见 `DECISIONS.md` 第九节。
> **全部是旁路**：删掉这五张表，知识库核心功能不受任何影响（约束 C4）。

### `ai_conversations`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | String(36) PK | UUID |
| `title` | String(100) NOT NULL | 从第一条用户消息截断而来（D-042：不让 AI 起名）。生成时做空白归一 |
| `created_at` / `updated_at` | UTCDateTime | `updated_at` 每追加一条消息就 bump（服务层手动维护，列表按它倒序） |

索引：`ix_ai_conversations_updated_at`。

### `ai_messages`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | String(36) PK | UUID |
| `conversation_id` | FK → ai_conversations | 数据库级 `ON DELETE CASCADE`：清空对话历史只删这里，不碰知识 |
| `seq` | Integer NOT NULL | 会话内从 0 递增，服务层取 `MAX(seq)+1`；`(conversation_id, seq)` 有唯一约束 |
| `role` | String(16) | `user` / `assistant`，常量在 `db/models/__init__.py` |
| `content` | Text NOT NULL | 原样保存，包括换行——前端 `whitespace-pre-wrap` 显示 |
| `model` | String(100) 可空 | 产生这条回复的模型名；用户消息为 NULL |
| `prompt_tokens` / `completion_tokens` / `duration_ms` | Integer 可空 | **流式回复拿不到精确 token 数时就是 NULL**（OpenAI 兼容流的 usage 需要额外开关，阶段 2 没开）。审查（非流式）三列全有值。别假设它们非空 |
| `error_kind` | String(32) 可空 | **非 NULL = 失败占位**：`content` 是程序写给用户看的那句话，不是 AI 原文；前端用错误样式显示，且不把它当上下文发给 AI。取值词表 `ERROR_KINDS` |
| `created_at` | UTCDateTime | |

### `ai_reviews`

一次 AI 审查 = 一行。审的是哪个版本，靠 `version_id` + 冗余的 `version_number` 语义（响应层计算）。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | String(36) PK | UUID |
| `knowledge_id` | FK → knowledge | 审查归属。采纳路径由此反查知识，调用方传错 ID 会 404 |
| `version_id` | FK → knowledge_versions | **审的是哪一版**。采纳出新版后，这份审查永远指向旧版——审查是历史事实，不随版本推进改写 |
| `model` / `prompt_version` | String | 用的什么模型、哪一版提示词。排查"AI 表现变化"唯一能信的锚点 |
| `result_json` | Text NOT NULL | **校验通过后的** `ReviewResult.model_dump_json()`。五个类别数组定死顺序，处置靠 `(category, item_index)` 定位，所以**写入后永不 UPDATE** |
| `prompt_tokens` / `completion_tokens` / `duration_ms` | Integer 可空 | 非流式，三列全有值 |
| `created_at` | UTCDateTime | |

### `ai_review_decisions`

用户对审查里某一条的处置。**三个取值平等**：`accepted` / `ignored` / `disagreed`，没有默认偏向（C7）。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | String(36) PK | UUID |
| `review_id` | FK → ai_reviews | |
| `category` | String(32) | `correct_points` / `errors` / `omissions` / `follow_up_questions` / `needs_verification`，词表 `REVIEW_CATEGORIES` |
| `item_index` | Integer | 该类别数组里的下标，从 0 开始。**数组顺序就是地址**，所以 `result_json` 不能改 |
| `decision` | String(16) | 见上 |
| `created_at` / `updated_at` | UTCDateTime | 改主意是 UPDATE 这一行，不是插新行；`(review_id, category, item_index)` 唯一 |

### `idempotency_keys`

| 列 | 类型 | 说明 |
|---|---|---|
| `key` | String(64) PK | 前端为每次"确认草稿"生成的 UUID |
| `knowledge_id` | FK → knowledge | 这个键当时创建的那条知识。重放时按它返回**同一条** |
| `created_at` | UTCDateTime | |

已知极窄窗口（D-052）：知识行 commit 与本表 commit 之间进程被杀，重试会多一条记录。单人本地应用权衡后接受。

---

## 跨表约定

### 时间字段：一律 UTC，写库时拒绝 naive datetime

所有 `*_at` 列都用 `UTCDateTime`（`backend/db/base.py` 的 TypeDecorator）：

- **写入**：aware datetime → 归一化成 UTC → 去掉 tzinfo 落库（SQLite 不存时区）
- **读取**：补上 `timezone.utc`，返回 aware datetime
- **写入 naive datetime → 抛 `ValueError`**："拒绝写入不带时区的时间。请使用
  `backend.db.base.utcnow()` 生成 UTC 时间。"

全项目唯一的时间来源是 `utcnow()`，**不要用 `datetime.now()`**。

这道硬约束是为了防住 Blueprint 9.2 的"系统时间变化、跨日和时区转换
不会导致复习日期错乱"。在写库那一刻报错，比等到阶段 3 的复习日期
算错一天再回头查，成本低一个数量级。

**测试注意点**：SQLAlchemy 会把这个 ValueError 包成 `StatementError`。
断言要写：

```python
with pytest.raises(StatementError, match="不带时区") as excinfo:
    ...
assert isinstance(excinfo.value.__cause__, ValueError)
```

### 主键：UUID 字符串

用 `str(uuid4())` 存 VARCHAR(36)，不用自增整数。理由：

- 阶段 5 的结构化导出/导入不会因为 ID 冲突而撞车
- 前端 URL 里的 `/knowledge/:knowledgeId` 用 UUID 不会泄露"你一共有多少条知识"
- 本地应用不需要自增 ID 的紧凑性

### 枚举：字符串常量而非 SQLAlchemy Enum

所有"取值有限"的列都是 `String` + 模块级常量，校验放 Pydantic schema 层。
理由：SQLite 的 Enum 落成 CHECK 约束，而 SQLite **不能 ALTER TABLE 修改 CHECK**，
以后加取值只能整表重建。见 `DECISIONS.md` D-005。

### 删除策略：归档优先

Blueprint 5.1 明确"归档而非物理删除"。`knowledge.status = 'archived'`，
数据完整保留，默认列表不显示。

ORM 里的 `cascade="all, delete-orphan"` 只用于**对象图内部一致性**
（比如替换整个 versions 列表时清理被移除的行），
**不代表业务上允许物理删除知识单元**。

已落地：`knowledge_service.py` 里**没有任何硬删除函数**，
`api/knowledge.py` 的 9 个端点里**没有 DELETE**，
只有 `archive` / `restore`。级联删除只在"替换来源列表"这类
子对象整理时生效，主记录永远不会被业务代码删掉。

---

## 后续阶段的表（尚未创建）

以下是 Blueprint 5.2~5.4 规划、但**当前数据库里还不存在**的表。
列在这里是为了说明字段设计的连贯性，不要误以为已经实现。

> **阶段 2 的五张 AI 表已实现并接入业务**，字段级说明见上文
> 「阶段 2 的五张 AI 表」一节。
> **实际建了 5 张，比原计划多 3 张**：`ai_reviews`、`ai_review_decisions`、
> `idempotency_keys`。原计划只写了 `ai_conversations` / `ai_messages`，
> 但工作流 C 要求审查结果"独立存储且不写进 knowledge_versions 的任何现有行"、
> 逐条采纳/忽略的状态要能扛住刷新，Blueprint 验收项又要求"不会重复提交
> 造成重复记录"，这三件事各需要一张表。理由详见 `DECISIONS.md` 第九节。

| 表 | 阶段 | 关键字段 | 已知风险 |
|---|---|---|---|
| `review_schedules` | 3 | FSRS 状态：`stability`、`difficulty`、`due`、`last_review`、`state` | **见下方警告** |
| `review_sessions` | 3 | 问题、回答、AI 评价、AI 建议评级、**用户最终评级**、耗时 | 用户评级是权威，AI 只是建议 |
| `knowledge_relations` | 4 | 关系类型、解释、依据、置信度、`status`(pending/accepted/rejected) | 图谱只查 accepted |

### ⚠️ 阶段 3 的 FSRS 字段不匹配（提前记录）

`fsrs` 6.3.2 的 `Card` 只有这些字段：

```
card_id, state, step, stability, difficulty, due, last_review
```

而 Blueprint 的 `review_schedules` 还列了
**`reps`、`lapses`、`elapsed_days`、`scheduled_days`**。
这四个字段 FSRS 库**不提供**，必须由 `review_sessions` 的历史记录推算：

- `reps` = 该知识单元的 `review_sessions` 行数
- `lapses` = 其中评级为 `again` 的行数
- `elapsed_days` = 本次 `reviewed_at` − 上次 `reviewed_at`
- `scheduled_days` = 本次 `due` − 上次 `reviewed_at`

另外测试**必须**用 `Scheduler(enable_fuzzing=False)`，
否则 FSRS 会给复习间隔加随机扰动，结果不确定、无法断言。

---

## 数据文件与备份

| 文件 | 说明 |
|---|---|
| `data/app.db` | 主数据库 |
| `data/app.db-wal` | WAL 预写日志，**未落盘的数据在这里** |
| `data/app.db-shm` | WAL 共享内存索引 |
| `data/backups/` | 备份输出目录（阶段 5 实现） |
| `data/env-backups/` | 设置页写 `.env` 前的自动备份，微秒时间戳命名，只留最近 10 份（D-056）。**内含密钥**，已被 Git 忽略 |

数据库位置由 `PKOS_DATA_DIR` 决定，默认 `<项目根>/data`。
测试通过设置 `PKOS_DATA_DIR` 指向 `tmp_path` 实现完全隔离。

### ⚠️ 备份绝不能手动复制 `app.db`

开了 WAL 之后，最近的数据可能还在 `app.db-wal` 里没合并进主文件。
只复制 `app.db` 会拿到**不完整甚至不一致**的库。

正确做法是 `VACUUM INTO`（阶段 5 会包装成命令）：

```sql
VACUUM INTO 'data/backups/app-2026-10-31.db';
```

它会先合并 WAL，再输出一个自洽的独立文件。

**运行时的连接设置**（`backend/db/session.py`）：

| PRAGMA | 值 | 为什么 |
|---|---|---|
| `foreign_keys` | `ON` | SQLite **默认不强制外键**。不开这个，级联删除和 FK 约束形同虚设，孤儿数据静默产生 |
| `journal_mode` | `WAL` | 读写不互相阻塞 |
| `busy_timeout` | `5000` | 遇到锁时等 5 秒而不是立刻抛 `database is locked` |

---

## 迁移操作速查

```bash
uv run alembic upgrade head      # 升到最新
uv run alembic current           # 看当前版本
uv run alembic check             # 检查 ORM 模型与迁移是否漂移
uv run alembic history           # 看迁移链
uv run alembic downgrade base    # 全部回滚（危险，会删表）
```

`alembic.ini` 的 `sqlalchemy.url` 是**故意留空**的，
实际 URL 由 `migrations/env.py` 从 `backend.config` 读取，
保证端口、数据目录、凭据只有一个来源（见 `DECISIONS.md` D-010）。

**新增迁移的约定**：

- revision id 用四位数字递增（`0001`、`0002`…），不用 Alembic 默认的随机哈希——
  单线程开发不需要处理分支合并，顺序号更易读
- 涉及 FTS5 或改约束的迁移**手写**，不用 `--autogenerate`
  （autogenerate 完全不认识 FTS5 虚拟表）
- 改列、加约束一律走 `op.batch_alter_table`（SQLite 限制）
- 写完必须跑 `uv run pytest tests/test_migrations.py`，
  那个文件里有 downgrade→base 再 upgrade→head 的往返测试
