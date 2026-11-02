# ARCHITECTURE —— 系统架构

本地优先的单机应用。**没有微服务、没有消息队列、没有容器编排**——
Blueprint 明确不做这些（见 `ROADMAP.md` 末尾"明确不做的内容"）。
架构的目标是：一个命令能起来，数据在自己硬盘上，出问题能自己查。

---

## 一、部署形态：单端口

```
                        浏览器
                          │
                          │  http://127.0.0.1:8000
                          ▼
        ┌─────────────────────────────────────────┐
        │         FastAPI / uvicorn (:8000)       │
        │                                         │
        │   /api/*          →  API 路由            │
        │   /assets/*       →  StaticFiles        │
        │   /*  (其他全部)   →  SPA 回落 index.html │
        └───────────────────┬─────────────────────┘
                            │ SQLAlchemy
                            ▼
                  data/app.db  (SQLite + WAL)
```

**生产模式**（`.\start.ps1`）：前端已构建成 `frontend/dist`，
由同一个 FastAPI 进程托管。浏览器只访问 8000，**同源，无跨域问题**。
阶段 5 手机访问时只需在防火墙放行这一个端口。

**开发模式**（`.\start.ps1 -Dev`）：

```
   浏览器 ──> Vite dev server (:5173)  ← 热更新
                  │
                  │  /api 代理（vite.config.ts 的 server.proxy）
                  ▼
             FastAPI (:8000)  ← uvicorn --reload
```

Vite 配了 `strictPort: true`：端口被占用时**直接报错**，
而不是悄悄换一个端口让人对着 5173 的空页面发呆。

### SPA 回落的两个必要细节

`backend/main.py::_mount_frontend` 里：

1. **`/api/*` 未匹配到路由 → 返回 JSON 404**，不能回落到 index.html。
   否则前端调错 API 会拿到一个 HTML 页面，`response.json()` 抛出的错
   完全看不出真实原因是路由写错了。

2. **回落前做目录穿越防护**：
   ```python
   candidate = (dist / full_path).resolve()
   if not candidate.is_relative_to(dist) or not candidate.is_file():
       return FileResponse(index)
   ```
   少了 `is_relative_to` 这一行，`/../../.env` 就能读到配置文件。

### 前端未构建时的降级

`frontend/dist` 不存在时，如果 `PKOS_ALLOW_MISSING_FRONTEND=true`（默认），
后端**照常启动**，`/` 返回一段 JSON 告诉用户怎么构建前端。

设计原则：**不能让"前端没构建"表现为"后端起不来"**。
否则用户排查时分不清是哪一环坏了。设为 `false` 则直接抛 `RuntimeError`，
适合部署脚本里用（宁可失败也不要静默降级）。

---

## 二、后端分层

Blueprint 6.3 规定的五层。**依赖只能从上往下，不能反向，不能跨层跳跃。**

```
┌──────────────────────────────────────────────────────┐
│  表现层    frontend/  (React + TypeScript)             │
└───────────────────────┬──────────────────────────────┘
                        │ HTTP /api/*
┌───────────────────────▼──────────────────────────────┐
│  API 层    backend/api/                                │
│  · 路由、依赖注入、HTTP 状态码                            │
│  · 不含业务逻辑                                         │
│  现有：health.py, knowledge.py（9 个端点）               │
└───────────────────────┬──────────────────────────────┘
                        │ 调用
┌───────────────────────▼──────────────────────────────┐
│  应用服务层  backend/services/                          │
│  · 编排领域规则 + 数据库访问，管事务边界                    │
│  · 一个业务动作 = 一个 service 函数 = 一个事务             │
│  现有：knowledge_service.py, search_service.py         │
│        ai/  ← AI 旁路子包（Provider 契约 + 实现 + 假替身）│
└──────────┬────────────────────────────┬──────────────┘
           │ 使用规则                     │ 读写
┌──────────▼─────────────┐  ┌────────────▼─────────────┐
│ 领域层 backend/domain/  │  │ 基础设施 backend/db/       │
│ · 纯业务规则            │  │ · models/  ORM 模型        │
│ · 不 import fastapi    │  │ · session.py 引擎与会话     │
│ · 不 import sqlalchemy │  │ · base.py  UTCDateTime 等  │
│ · 因此可以单独单元测试   │  │ backend/config.py 配置     │
│ 现有：versioning.py     │  │ backend/schemas/ Pydantic │
└────────────────────────┘  └──────────────────────────┘
```

分层不是画着好看的，可以用两个事实验证依赖方向没被破坏：
`backend/domain/versioning.py` 只 import 标准库
（`collections.abc` / `dataclasses` / `typing`），没有 sqlalchemy 也没有 fastapi；
因此 `test_versioning.py` 的 17 个测试不需要数据库、不需要 TestClient，
实测 **0.04 秒**跑完。反过来，一旦有人图省事在 domain 里 import ORM 模型，
这批测试会立刻因为要建库而变慢。

同一件事在 AI 旁路上也成立：`test_ai_provider.py` + `test_ai_provider_http.py`
的 67 个测试用 `httpx.MockTransport` 打假响应，**不联网、不需要 API key、不建库**，
实测 **0.23 秒**跑完。这是 D-038「测试一律用假 Provider」在时间上的兑现——
如果 AI 调用被织进了主干而不是旁路，这批测试不可能这么快。

### 各层职责边界

| 层 | 应该做 | 绝对不做 |
|---|---|---|
| `backend/api/` | 解析请求、调 service、映射 HTTP 状态码 | 写 SQL、写业务判断 |
| `backend/services/` | 编排流程、开事务、调 domain 规则、调 db | 碰 `Request` / `Response` 对象 |
| `backend/domain/` | 纯函数式的业务规则（如"新版本号 = 最大号 + 1"） | `import fastapi`、`import sqlalchemy` |
| `backend/db/` | ORM 定义、引擎、会话、列类型 | 业务判断 |
| `backend/schemas/` | Pydantic 校验、序列化 | 数据库访问 |

**为什么 domain 层要保持纯净**：`domain/versioning.py` 的规则
（追加式版本、原文不可覆盖）是整个产品最核心的不变量。
它不依赖数据库和 Web 框架，就可以用普通 pytest 直接断言，
不需要起 TestClient、不需要建临时库，测试跑得快、失败时报错清楚。

### 请求生命周期（以阶段 1 的"创建知识"为例）

```
POST /api/knowledge
  │
  ├─ api/knowledge.py
  │    · FastAPI 用 schemas/knowledge.py 的 Pydantic 模型校验请求体
  │    · 通过 Depends(get_session) 拿到一个 Session（每请求一个，结束必关闭）
  │    · 调 knowledge_service.create_knowledge(session, data)
  │
  ├─ services/knowledge_service.py
  │    · 开事务
  │    · 调 domain/versioning.py 算版本号
  │    · 建 Knowledge 行 → flush 拿 UUID → 建 KnowledgeVersion 行
  │    · 回填 knowledge.current_version_id
  │    · 调 search_service.sync_index(...)  ← FTS5 独立表必须显式同步
  │    · commit
  │
  └─ 返回 Pydantic 响应模型 → JSON
```

**会话管理**（`backend/db/session.py::get_session`）：

```python
def get_session() -> Generator[Session, None, None]:
    session = open_session()
    try:
        yield session
    finally:
        session.close()      # 请求异常也必须关，否则连接泄漏
```

`sessionmaker` 配了 `expire_on_commit=False`（commit 后仍能读对象属性，
不用额外 refresh）和 `autoflush=False`（flush 时机由 service 显式控制，
避免"读一个属性意外触发写"这种难查的行为）。

### 这两个配置带来的三个坑（都已实测锁住）

`expire_on_commit=False` 让对象在 commit 后仍然"新鲜"，代价是 SQLAlchemy
不会自动帮你刷新已经加载过的状态。**已经实测踩到三次**，
所以当成一类问题看，而不是三件孤例：

1. **`selectinload` 不会刷新已加载的集合** → `_require()` 和 `list_knowledge()`
   必须带 `.execution_options(populate_existing=True)`，否则第二次读同一条知识
   拿到的是内存里的旧版本。删掉它，11 个测试变红。
2. **只赋值外键不会让关系失效** → `knowledge.current_version_id = version.id`
   之后，已经加载过的 `knowledge.current_version` 关系仍指向旧对象，
   紧接着的 `sync_index()` 就会把**旧版本的正文**写进 FTS 索引。
   必须外键和关系**两个都赋值**（`knowledge.current_version = version`）。
   这个 bug 在数据库里看不出来（版本行是对的），只有走搜索 API 才暴露。
3. **同一个 Session 里反复读同一个关系集合，拿到的是陈旧集合** →
   `ai_service._context_messages` 因此走显式 `select`，
   不读 `conversation.messages`。生产路径每请求一个新 session 本来侥幸不踩，
   但把正确性押在"每次都是新 session"上太脆。
   ⚠️ 这条**是变异测试挖出来的**：改坏过滤器后测试全绿，
   才查出去过滤的那批数据根本没进到被过滤的集合里（假通过）。
   修法之外还补了一条正向断言——先证明成功的助手回复**确实在**上下文里，
   再证明失败那条不在。见 `DECISIONS.md` D-051。

---

## 三、配置：单一来源

**所有配置只有一个来源：环境变量 / `.env`，经 `backend/config.py` 的
`Settings` 类解析。** 代码里不散落硬编码的端口、路径、凭据。

```
.env  (PKOS_* 前缀)
   │
   ├─> backend/config.py::Settings        ← 后端唯一入口
   │      ├─> backend/db/session.py       (database_url)
   │      ├─> backend/main.py             (CORS 白名单、dist 路径)
   │      ├─> migrations/env.py           (迁移用同一个 URL)
   │      └─> scripts/doctor.py           (自己解析，见下)
   │
   └─> frontend/vite.config.ts            (process.env.PKOS_PORT / PKOS_FRONTEND_PORT)
```

`alembic.ini` 的 `sqlalchemy.url` **故意留空**，由 `migrations/env.py`
从 `Settings` 读。理由：写两遍迟早不一致，而不一致的表现是
"迁移跑在 A 库，应用读 B 库"，极难排查。见 `DECISIONS.md` D-010。

`migrations/env.py` 里还会调 `settings.ensure_runtime_dirs()`——
目录不存在时 SQLite 无法创建库文件，迁移前必须先确保数据目录就位。

### 完整配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `PKOS_HOST` | `127.0.0.1` | 后端监听地址。**改成 `0.0.0.0` 会让局域网可访问，只在可信网络下做**（Blueprint 9.3） |
| `PKOS_PORT` | `8000` | 后端端口 |
| `PKOS_FRONTEND_PORT` | `5173` | Vite 开发端口，同时决定 CORS 白名单 |
| `PKOS_DATA_DIR` | `<项目根>/data` | 数据目录。测试用它重定向到 `tmp_path` |
| `PKOS_AI_PROVIDER` | 空 | AI 供应商 |
| `PKOS_AI_BASE_URL` | 空 | API 地址 |
| `PKOS_AI_API_KEY` | 空 | **密钥。只在后端，绝不进前端代码、日志或 git** |
| `PKOS_AI_MODEL` | 空 | 模型名 |
| `PKOS_AI_TIMEOUT_SECONDS` | `60` | AI 调用超时 |
| `PKOS_LOG_LEVEL` | `INFO` | 日志级别 |
| `PKOS_ALLOW_MISSING_FRONTEND` | `true` | 前端未构建时是否允许后端启动 |

`Settings` 里两个 validator 值得注意：

- `_blank_to_none`：`.env` 里 `PKOS_DATA_DIR=` 留空时回落到默认目录，
  而不是被 Pydantic 解析成 `Path(".")`（那会把数据库建在项目根目录）
- `_upper`：`log_level` 大小写不敏感

`get_settings()` 用 `@lru_cache`，全进程一个实例。
**测试改环境变量后必须调 `get_settings.cache_clear()`**，
否则读到的是上一个测试的缓存值（conftest.py 的 `isolated_data_dir` 夹具
在 setup 和 teardown 都清了）。

---

## 四、前端结构

```
frontend/src/                     34 个 TS/TSX 文件（tsc -b --listFiles 实测）
├── main.tsx                 应用入口：QueryClientProvider + RouterProvider
├── index.css                Tailwind 4 入口 + CSS 变量主题
├── app/
│   ├── router.tsx           createBrowserRouter，所有路由定义
│   └── navigation.ts        NAV_ITEMS：导航项的单一数据源
├── components/
│   ├── layout/
│   │   ├── AppShell.tsx         响应式外壳（侧栏 / 抽屉）
│   │   └── StagePlaceholder.tsx 未实现页面的诚实占位
│   ├── feedback/
│   │   └── Notice.tsx           Notice / ApiErrorNotice / EmptyState
│   ├── knowledge/
│   │   ├── KnowledgeForm.tsx    新建与编辑共用的表单（含版本预览）
│   │   ├── SourceEditor.tsx     学习来源子表单（「其他」必填备注）
│   │   └── VersionHistory.tsx   历史版本列表，当前版高亮、旧版可展开
│   └── ui/                      基础组件。badge/button/card/scroll-area/separator
│                                来自 shadcn；input/textarea/select/label 是手写的
│                                （shadcn registry 需要联网，本机被 Clash Verge 阻断）
├── lib/
│   ├── api.ts               唯一的 HTTP 出口：拼 URL、zod 校验、422 翻译成中文
│   ├── schemas.ts           zod 契约 + 词表常量 + 中文标签
│   ├── knowledge-queries.ts TanStack Query 的 key 工厂与 hooks
│   ├── format.ts            时间格式化、摘要截断、"有没有实质内容"判断
│   ├── use-debounced-value.ts  搜索框防抖（中文输入法选词时会连续触发）
│   └── utils.ts             cn() 等
└── pages/                   每个路由一个页面组件
```

三个约定值得单独说：

- **页面不直接调 `fetch`**。一律走 `lib/api.ts` + `lib/knowledge-queries.ts`，
  这样缓存失效只有一处（`["knowledge"]` 前缀），`enabled` 条件也集中。
- **`lib/api.ts` 对每个响应做 zod 校验**。跨进程的 JSON 经过网络/代理/缓存，
  不校验的话症状是莫名其妙的 `undefined` 和 `.map is not a function`。
- **词表在前端是第三份拷贝**（后端 model 常量、OpenAPI、前端 schemas.ts）。
  后端权威，漂移由 `test_openapi_documents_the_vocabularies` 兜住，
  前端 zod 校验失败会抛 `ApiError(kind="schema")` 大声报错，不会静默存错数据。

### 路由

`app/router.tsx` 用 `createBrowserRouter`，`AppShell` 作为 layout route：

| 路径 | 页面 | 阶段 | 状态 |
|---|---|---|---|
| `/` | HomePage | 3 | ✅ 有真实数据（调 `/api/health`） |
| `/knowledge` | KnowledgeListPage | 1 | ✅ 列表 / 搜索 / 筛选 / 分页 |
| `/knowledge/new` | KnowledgeEditPage | 1 | ✅ 新建（复用同一个表单组件） |
| `/knowledge/:knowledgeId` | KnowledgeDetailPage | 1 | ✅ 详情 + 来源 + 版本历史 |
| `/knowledge/:knowledgeId/edit` | KnowledgeEditPage | 1 | ✅ 编辑 |
| `/study` | AiStudyRoomPage | 2 | 占位 |
| `/review` | ReviewCenterPage | 3 | 占位 |
| `/graph` | KnowledgeGraphPage | 4 | 占位 |
| `/settings` | SettingsPage | 2 | 占位 |
| `*` | NotFoundPage | — | ✅ 显示实际路径 |

`new` 是静态段、`:knowledgeId` 是动态段，react-router 的路由排序保证静态段优先，
所以 `/knowledge/new` 不会被详情页吃掉。这一点在
`TestRouteOrdering` 里有后端侧的对应断言（`/api/knowledge/domains`、`/search` 同理）。

`KnowledgeEditPage` 在编辑模式下**等数据到位才挂载表单**：表单初始值来自 props，
先挂一个空表单再等数据，用户已经输入的内容会被覆盖掉。

### `navigation.ts` 是导航的单一数据源

侧栏和移动端抽屉**都从 `NAV_ITEMS` 渲染**，不各写一份。
每项是 `{ to, label, description, icon, stage }`。
加一个页面只改这一个数组，两处导航自动同步。

### 数据获取：TanStack Query

`main.tsx` 里的 `QueryClient` 默认配置针对**本地单用户应用**调过：

```ts
queries: {
  refetchOnWindowFocus: false,   // 没有其他协作者，切回窗口不需要重拉
  retry: 1,                      // 本地服务，重试多次只是拖慢错误反馈
  staleTime: 30_000,
}
```

写操作后用 `invalidateQueries` 主动失效，不依赖轮询。

### 响应式与移动端友好（Blueprint 6.5）

`AppShell.tsx` 的实现要点：

- **桌面**（`lg` 以上）：固定侧栏 `w-64`，`sticky`，内含 ScrollArea
- **移动**（`lg` 以下）：sticky 顶栏 + 汉堡按钮 → 抽屉
  （`w-72 max-w-[85vw]`），带遮罩层。遮罩和右上角 X 都是 `<button>`（键盘可达），
  但两者的 `aria-label` 必须区分开——遮罩是「关闭导航（点击空白处）」，
  否则读屏用户听到两个一模一样的「关闭导航」。Esc 也能关
- **三个副作用**：路由变化时自动关抽屉；抽屉打开时锁
  `document.body.style.overflow`，防止背景跟着滚；Esc 键关闭
- **触控目标 ≥ 44px**：导航项 `min-h-11`；**图标按钮一律 `size-11`**。
  shadcn `Button` 的 `icon-sm`/`icon-lg` 只有 28/36px，手指点容易点空，
  所以凡是用到这两个 size 的地方都用 `className="size-11"` 覆盖掉
  （`cn()` 走 tailwind-merge，后面的类胜出）
- **输入框字号 16px**（`text-base md:text-sm`）：不是审美选择，
  iOS Safari 聚焦字号小于 16px 的输入框时会自动放大整个页面
- **零 hover 依赖**：所有交互在触屏上都能完成。
  hover 只用于视觉增强，不作为唯一触发方式
- **键盘可达**：`focus-visible:ring-2`，当前项 `active:bg-muted`
- **列表整行是 `<Link>`**：手机上手指点哪儿都能进详情，不用瞄准小图标

浏览器实测过：首页、知识库列表、新建、详情、编辑、归档/恢复、
搜索（含 2 字中文词）、分页翻页、404 页、深链直接刷新、移动端抽屉导航，
控制台零消息（无 error 无 warning）。

> **实测条件（说清楚量到了什么、没量到什么）**
>
> 内嵌浏览器视口是 **524×605**，低于 Tailwind 的 `md`(768px) 断点，
> 所以上面全流程跑的都是**窄屏布局**，这些是真的量出来的：
> 输入框计算高度 44px、字号 16px；按钮全部 ≥44×44；
> `scrollWidth === clientWidth`（无横向溢出）；抽屉项 50px；分页按钮 44px。
>
> 没量到的：
> - **截图**始终返回 `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`（页面 surface 处于
>   `hidden`），所以全部验证都是结构化的——无障碍树快照 + `getComputedStyle`
>   + `getBoundingClientRect`，没有人眼看过渲染结果
> - MCP 的 `click` 工具派发不了点击（同一原因），改用 `evaluate_script` 里的
>   `el.click()`；`press_key` 的 Esc 也没送达页面，改用派发 `KeyboardEvent` 验证
> - **宽屏（≥768px）布局完全没验证过**，`md:`/`lg:` 分支只是代码推断
>
> 阶段 5 需要真机补一遍。

### 不用假数据伪装功能（Blueprint 10.2）

剩下 4 个未实现页面（AI 学习室、复习中心、知识图谱、设置）用 `StagePlaceholder`
渲染，明确显示：

- 「尚未实现」标题 + `Construction` 图标
- 「这个页面属于阶段 N（阶段名），目前只做到阶段 M。此处不放演示数据，
  以免把未实现的功能伪装成已完成。」
  —— `M` 取自 `app/navigation.ts` 的 `CURRENT_STAGE` 常量，
  不是散落在各页的硬编码，否则每推进一个阶段就会留下一批说谎的文案
- 「计划包含」列表，说明这个页面将来会有什么

知识库三个页面展示的都是真实数据。**空库时不放示例数据**，
而是给三种不同的空状态：搜索没命中 / 归档区是空的 / 一条都还没记，
每种都写清楚下一步该做什么，而不是摆几条假知识让页面看起来热闹。

---

## 五、测试架构

```
tests/                            271 个测试（各文件收集数相加 == 全量运行数）
├── conftest.py               夹具（全部走真实迁移 + 临时目录）
├── test_health.py            /api/health 的语义与隐私（5）
├── test_migrations.py        迁移结构与数据完整性保证（12）
├── test_versioning.py        domain 层版本规则的纯函数测试（17）
├── test_schemas.py           Pydantic 校验与 422 报文（50）
├── test_search_index.py      服务层：转义、索引一致性、索引修复（32）
├── test_knowledge_service.py 服务层：版本号推导、N+1 查询次数（14）
└── test_knowledge_api.py     HTTP 端到端：路由、CRUD、版本、搜索、非回归（141）
```

分层是有意的：domain 的纯函数测试跑得最快也最容易定位，
API 测试只管"穿过整个栈之后行为对不对"，不重复测 domain 的分支。

### 用变异测试验证测试本身

"271 passed" 只说明测试没失败，不说明测试有用。所以往源码里注入 5 个变异，
每个都必须有测试变红：

| 注入的变异 | 结果 |
|---|---|
| 删掉 `sync_index()` 调用 | 14 个测试失败 ✅ |
| 删掉 `populate_existing` 执行选项 | 11 个测试失败 ✅ |
| 删掉"只有理解字段变了才建版本"的判断 | 14 个测试失败 ✅ |
| `next_version_number` 改成 `len(versions) + 1` | **一开始全部通过 ❌** |
| `_like_pattern` 调换转义顺序（先转 `%`/`_` 再转 `\`） | 2 个测试失败 ✅ |

第 4 个是真正的漏洞：用行数而不是最大值推版本号，在版本号连续时结果一样，
一旦出现空洞（恢复到旧版本、手工修数据）就会撞唯一约束。
补了 `test_number_comes_from_the_maximum_not_the_row_count`——
用原生 SQL 人为把 v2 改成 v10，断言下一个版本号是 11 而不是 3。

> 踩过的坑：变异脚本一开始用 `sys.executable` 跑 pytest，
> 那是系统 Python 3.14（没装 pytest），三个变异全部 `rc=1` 且 stdout 为空，
> 看起来像"变异全被抓住"。必须显式用 `.venv/Scripts/python.exe`，
> 并且把 stderr 一起打出来，否则得到的是假结论。

### 查询次数是被锁死的

`test_knowledge_service.py` 挂在**引擎**的 `before_cursor_execute` 上数 SQL 语句
（这是 Connection/Engine 级事件，Session 上没有；Session 的 `do_orm_execute`
看不到 `selectinload` 发出的语句），断言：

- `list_knowledge` 在 limit=1 和 limit=50 下语句数**完全相同**（都是 4）→ 没有 N+1
- 空列表只发 2 条（后两条依赖主查询结果，自动跳过）
- `get_knowledge` 固定 4 条

这既是性能保证，也是文档保证——`knowledge_service.py` 的 docstring 里写的
就是这几个实测数字，改代码不改测试就会红。

### 隔离策略：`PKOS_DATA_DIR` 重定向整个数据目录

```python
@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PKOS_DATA_DIR", str(tmp_path / "test-data"))
    monkeypatch.setenv("PKOS_AI_API_KEY", "")     # 清空，防止读到真实 key
    ...
    get_settings.cache_clear()                     # 必须清缓存
    yield tmp_path / "test-data"
    get_settings.cache_clear()                     # teardown 也清
```

**为什么不用 `PKOS_MIGRATION_DATABASE_URL`**：早期版本有这个变量，
单独重定向迁移的目标库。**已删除**——设置 `PKOS_DATA_DIR` 就能让整个
数据目录（库文件、备份目录）一起搬走，迁移和应用自然落到同一个临时库。
少一个环境变量 = 少一种"迁移和应用指向不同库"的不一致可能。

### 跑真实迁移，不用 `create_all()`

```python
@pytest.fixture
def migrated_database(isolated_data_dir):
    command.upgrade(make_alembic_config(), "head")   # 真实的迁移链，不是 create_all()
    ...
```

**理由**：`Base.metadata.create_all()` 会**跳过所有原生 SQL**，
意味着 FTS5 虚拟表根本不会被创建，循环外键的回填顺序也不会被验证。
用 `create_all()` 的测试全绿，但用户跑 `alembic upgrade head` 可能直接失败
——这是最糟的一种"测试通过"。

### `test_migrations.py` 覆盖的不变量

这些不是"测代码能跑"，而是**测 Blueprint 的硬性产品原则在数据层成立**：

| 测试 | 保证的不变量 |
|---|---|
| FTS5 虚拟表存在且 DDL 含 `fts5` + `trigram` | 中文检索基础在 |
| 5 张影子表存在 | FTS5 建得完整 |
| `alembic current` 在 `ScriptDirectory.get_heads()` 里 | 迁移链没断 |
| `(knowledge_id, version_number)` 唯一 → `IntegrityError` | 版本号不会重复 |
| `PRAGMA foreign_keys == 1`，孤儿版本 → `IntegrityError` | 外键真的在强制 |
| 循环外键可回填， bogus `current_version_id` → `IntegrityError` | 指针不会指向不存在的版本 |
| naive datetime → `StatementError(match="不带时区")` | UTC 硬约束生效 |
| aware datetime 往返后仍带 tzinfo | 读回来不会退化成 naive |
| **`test_original_understanding_survives_new_version`** | **Blueprint 7.1：v1 的原文在 v2 之后一字不变** |
| downgrade→base 无残留，upgrade→head 恢复 4 张表 | 迁移可逆 |
| `utcnow()` 带时区且偏移为 0 | 时间源正确 |

### 反向测试：`unmigrated_client`

`conftest.py` 里有一个**故意不跑迁移**的夹具，用来测降级路径：
数据库表不存在时，`/api/health` 必须仍返回 **HTTP 200** 且
`status: "degraded"`、`database.reachable: false`、`error` 非空。

**理由**：健康检查接口自己 500 的话，用户和监控都拿不到任何信息。
它必须能在"系统坏了"的时候说出"哪里坏了"。

### 隐私测试用哨兵值

`test_health_never_leaks_api_key` 设置
`PKOS_AI_API_KEY=sk-TEST-DO-NOT-LEAK-1234567890` 和
`PKOS_AI_BASE_URL=https://example.invalid/v1`，然后断言：

- `ai_configured is True`（功能正常）
- **哨兵字符串不出现在 `response.text` 里**
- **`example.invalid` 不出现在 `response.text` 里**

用哨兵值而不是"检查某个字段不存在"，是因为它能抓住**任何**泄漏路径
（包括将来有人不小心把整个 settings dump 进响应）。

### pytest 配置要点

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]                    # 本项目不是可安装包，必须显式加根目录
addopts = "-ra --strict-markers"
filterwarnings = [
    "error::DeprecationWarning:backend.*",   # 自己代码的弃用警告升级成错误
]
```

`pythonpath = ["."]` 的必要性：`[tool.uv] package = false`（这是应用不是库），
pytest 默认只把 `tests/` 加进 `sys.path`，`import backend` 会直接
`ImportError`。见 `DECISIONS.md` D-024。

`conftest.py` 还把 `httpx2` / `httpx` / `uvicorn` / `alembic` 的 logger
压到 WARNING，否则测试输出被第三方 INFO 日志淹没。

---

## 六、脚本与启动器

```
scripts/
├── doctor.py     环境自检（只用标准库）
└── setup.py      一键安装（6 步，幂等）
start.ps1         Windows PowerShell 启动器
start.sh          Git Bash / macOS / Linux 启动器
```

### `doctor.py`：12 项检查，只用标准库

| 检查项 | FAIL 条件 |
|---|---|
| 操作系统 | 只是记录，不失败 |
| Python 版本 | < 3.13 |
| uv | 未安装 |
| Node.js | < 22 |
| 后端依赖 | 7 个包任一缺失（用 venv 的 python 检查） |
| 前端依赖 | `node_modules` 缺失 |
| 前端构建产物 | `dist/index.html` 缺失 → **WARN 不是 FAIL** |
| `.env` | 缺失 → WARN（有 `.env.example`） |
| 数据目录可写 | 不可写或磁盘空间不足 |
| 后端端口 8000 | 被占用或落在 Windows 保留段 |
| 前端端口 5173 | 同上 |
| 数据库迁移 | 直接读 SQLite 查 `alembic_version` |
| Git 远程 | 无远程 → **WARN**（本机 GitHub 不可达） |

有 FAIL 则退出码 1。`--json` 模式供 `start.ps1` / `start.sh` 解析。

**为什么只用标准库**：环境自检最需要的时刻恰恰是**依赖还没装好**的时候。
如果 doctor 导入 `backend.config`，那 `uv sync` 之前它自己就 import 失败，
用户连诊断信息都拿不到。代价是 `.env` 要自己解析一遍——值得。

**端口探测的正确写法**（`DECISIONS.md` D-017 详述了为什么）：

```python
def port_state(port, host="127.0.0.1"):
    # 1. 先排除 Windows 保留端口段（netsh 查出来的）
    for start, end in excluded_port_ranges():
        if start <= port <= end:
            return FAIL, f"落在 Windows 保留端口段 {start}-{end} 内"
    # 2. 先 connect：有没有服务在监听
    with socket.socket(...) as probe:
        if probe.connect_ex((host, port)) == 0:
            return FAIL, f"已有服务在监听（{describe_port_owner(port)}）"
    # 3. 再 bind，且【不设 SO_REUSEADDR】
    with socket.socket(...) as sock:
        try:
            sock.bind((host, port))
            return PASS, "空闲，可以绑定"
        except OSError as exc:
            ...
```

`describe_port_owner()` 会调 netstat + tasklist，报出占用进程的
**PID 和进程名**（例如 `PID 34892（python.exe）`），
而不只是说"端口被占用"。

### `setup.py`：6 步幂等安装

```
1. step_doctor         跑 doctor.py，有 FAIL 就中止
2. step_backend_deps   uv sync，验证 .venv/Scripts/python.exe 存在
3. step_env_file       .env.example → .env（仅当 .env 不存在）
4. step_migrate        data/app.db 已存在则先提示备份，
                       再 uv run alembic upgrade head，打印 alembic current
5. step_frontend_deps  npm ci，失败则回落 npm install
6. step_frontend_build npm run build，验证 dist/index.html
```

`fail(message, hint)` 同时给出**问题和建议动作**，不只报错。
子进程统一带 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`。

### 两个启动器的差异

| | `start.ps1` | `start.sh` |
|---|---|---|
| 平台 | Windows PowerShell 5.1+ | Git Bash / macOS / Linux |
| 编码要求 | **必须 UTF-8 带 BOM + CRLF** | **必须 LF，无 BOM** |
| 参数 | `-Dev` `-NoBrowser` `-SkipDoctor` | `--dev` `--no-browser` `--skip-doctor` `-h` |
| 清理 | `finally` 里 `taskkill /PID x /T /F` | `trap` + `is_windows` 分支 |
| npm 解析 | `Get-NpmPath` → **必须是 `npm.cmd`** | 直接 `npm` |

共同流程：
`doctor --json` → 逐项彩色输出（带 hint）→ 有 FAIL 则中止 →
`alembic upgrade head` → （非 Dev 且 dist 缺失时）构建前端 →
拉起服务 → 等端口就绪 → 开浏览器。

**编码要求不是洁癖，是踩过的坑**：
- `start.ps1` 没 BOM → PowerShell 5.1 按 GBK 解码 → 中文吃掉后面的引号 →
  **5 个解析错误，文件完全无法运行**，报错行号指向的代码根本没有语法问题
- `start.sh` 是 CRLF → shebang 变成 `#!/usr/bin/env bash\r` →
  `bad interpreter: No such file or directory`

`.gitattributes` 强制这两条，并且做过真实的
`rm` + `git checkout --` 往返验证（字节完全一致）。

---

## 七、安全与隐私边界

| 边界 | 实现 |
|---|---|
| API Key 不进前端 | 密钥只在 `Settings`，`/api/health` 只返回 `ai_configured: bool`。哨兵值测试验证 |
| API Key 不进日志 | 日志不记录 key；Blueprint 9.1 要求日志也不记不必要的私人知识正文 |
| API Key 不进 git | `.env` 在 `.gitignore` 里，仓库只提交 `.env.example`（无真实值） |
| CORS 只放行本机 | 白名单是 `127.0.0.1:{frontend_port}` 和 `localhost:{frontend_port}`，**不是 `*`**。理由：单端口模式同源，中间件本不生效；开 `*` 会让局域网里任何网页都能读私人知识库 |
| 静态文件防穿越 | `candidate.is_relative_to(dist)` |
| 默认只监听本机 | `PKOS_HOST=127.0.0.1`。改 `0.0.0.0` 需要用户主动做，且 Blueprint 9.3 要求配防火墙与访问控制，不暴露公网 |
| 数据不出本机 | 无云同步代码。阶段 2 的 AI 调用遵循最小必要原则：只发当前知识单元，不发全库 |
| 时间不出错 | `UTCDateTime` 硬拒绝 naive datetime（阶段 3 复习日期正确性的前提） |
| 搜索词不改写查询语义 | `_fts_phrase()` 把整串包成 FTS5 phrase 并把 `"` 双写；`_like_pattern()` 先转义 `\` 再转义 `%`/`_`，配合 `LIKE ... ESCAPE '\'`。11 种注入载荷（`%` `_` `"` `*` `-` `a"b` `a*` `NEAR(a b)` `梯度 OR 熵` `{` `\`）参数化测试过：`%` 不会匹配全库 |
| 前端渲染不产生 XSS | 全部走 React 文本节点，没有 `dangerouslySetInnerHTML`；用户原文用 `whitespace-pre-wrap` 保留换行而不是插 HTML |
| 外链不带 referrer | 学习来源里的外部链接一律 `target="_blank" rel="noreferrer noopener"`，既防 tabnabbing 也不把本机地址泄露给外部站点 |

---

## 八、当前实现状态（诚实版）

后端 4051 行 Python，前端 34 个 TS/TSX 文件，测试 448 个。

| 部分 | 状态 |
|---|---|
| 配置系统 | ✅ 完整 |
| 数据库连接与会话 | ✅ 完整（含 WAL、外键、超时 PRAGMA） |
| 迁移 0001（3 表 + 索引 + FTS5） | ✅ 完整，可 upgrade 可 downgrade |
| 迁移 0002（5 张 AI 表 + 索引 + 级联） | ✅ 完整，可 upgrade 可 downgrade，`alembic check` 无漂移 |
| ORM 模型 | ✅ 8 个模型 + `UTCDateTime` + 词表常量 |
| `/api/health` | ✅ 完整，含降级路径 |
| 前端静态托管 + SPA 回落 | ✅ 完整 |
| 前端外壳、路由、响应式导航 | ✅ 完整 |
| `backend/domain/versioning.py` | ✅ 完整（100 行纯函数，不 import sqlalchemy/fastapi/pydantic） |
| `backend/schemas/knowledge.py` | ✅ 完整（274 行，含 `other` 必填备注、纯标点拒绝等跨字段校验） |
| `backend/schemas/ai.py` | ✅ 完成（171 行 / 35 个测试）：提取草稿 + 审查五分类。**校验比落库松**（D-048），`extra="ignore"`（D-047） |
| `backend/services/knowledge_service.py` | ✅ 完整（432 行，CRUD + 归档/恢复，查询次数固定。追加版本那段已抽成 `append_version()` 供 AI 路径共用，见 D-050） |
| `backend/services/search_service.py` | ✅ 完整（333 行，FTS5 + LIKE 混合检索、索引同步/修复、领域建议、相似标题） |
| `backend/services/ai_service.py` | 🚧 完成但**无调用方**（578 行 / 30 个测试）：三个工作流的编排、`accept_ai_review()` 窄路径、幂等去重、清空对话历史。还没有任何 HTTP 接口调它 |
| `backend/api/knowledge.py` | ✅ 9 个端点：`POST/GET ""`、`GET/PUT "/{id}"`、`archive`、`restore`、`search`、`domains`、`similar` |
| 前端知识库三页 | ✅ 列表（搜索/筛选/分页）、详情（来源 + 版本历史）、新建与编辑共用表单 |
| `backend/services/ai/` | 🚧 阶段 2 进行中（966 行 / 101 个测试）：**契约 + 真实 HTTP 实现 + 假 Provider + 三个工作流的系统提示词都已就位**（`provider.py` 协议、`errors.py` 五类失败、`parsing.py` 解析校验、`openai_compat.py` httpx 实现、`fake.py` 测试替身、`prompts.py` 提示词 + `PROMPT_VERSION`） |
| **AI 集成** | 🚧 后端已经通到"能跑但没人调用"：5 张表已建、Provider 能发请求、提示词与 Schema 就位、`ai_service` 三个工作流可运行（30 个测试验证）。**但没有接口、没有界面**，用户目前完全看不到；`/api/health` 仍恒返回 `ai_configured: false` |
| **复习系统** | ❌ 未实现（阶段 3）。`fsrs` 已装但未接线，两张表未建 |
| **知识图谱** | ❌ 未实现（阶段 4）。`knowledge_relations` 表未建 |
| **备份与导出** | ❌ 未实现（阶段 5）。`data/backups/` 目录会创建但里面永远是空的 |
| 4 个业务页面 | ⬜ 诚实占位，明确标注所属阶段与当前进度 |

**数据库当前 0 行数据**——八张业务表（知识库 3 张 + AI 5 张）全部为空。
阶段 1 与阶段 2 第①步的所有写入都发生在临时目录里，
测试和浏览器实测跑完就删掉了，用户真实的 `data/app.db` 只被迁移改过表结构，
从头到尾没有一行数据。

**这一版能做什么、不能做什么**：
可以完整地「记一条知识 → 改它 → 看到旧版本原文 → 搜回来 → 归档」，
数据落在本机 SQLite，进程被强杀也不丢（WAL 恢复实测过）。
不能做任何 AI 相关的事、不能复习、不能建关联、不能备份导出。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| `README.md` | 安装、启动、故障排查 |
| `docs/PRD.md` | 产品需求与设计原则 |
| `docs/DATA_MODEL.md` | 表结构、字段、约束、FTS5 细节 |
| `docs/AI_WORKFLOWS.md` | AI 工作流设计（阶段 2/3/4） |
| `docs/ROADMAP.md` | 阶段任务与验收标准 |
| `docs/DECISIONS.md` | 技术决策记录，含全部 Windows 陷阱 |
