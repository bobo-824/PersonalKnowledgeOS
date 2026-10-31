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
│  现有：health.py                                        │
└───────────────────────┬──────────────────────────────┘
                        │ 调用
┌───────────────────────▼──────────────────────────────┐
│  应用服务层  backend/services/                          │
│  · 编排领域规则 + 数据库访问，管事务边界                    │
│  · 一个业务动作 = 一个 service 函数 = 一个事务             │
│  阶段1：knowledge_service.py, search_service.py        │
└──────────┬────────────────────────────┬──────────────┘
           │ 使用规则                     │ 读写
┌──────────▼─────────────┐  ┌────────────▼─────────────┐
│ 领域层 backend/domain/  │  │ 基础设施 backend/db/       │
│ · 纯业务规则            │  │ · models/  ORM 模型        │
│ · 不 import fastapi    │  │ · session.py 引擎与会话     │
│ · 不 import sqlalchemy │  │ · base.py  UTCDateTime 等  │
│ · 因此可以单独单元测试   │  │ backend/config.py 配置     │
│ 阶段1：versioning.py    │  │ backend/schemas/ Pydantic │
└────────────────────────┘  └──────────────────────────┘
```

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
frontend/src/
├── main.tsx                 应用入口：QueryClientProvider + RouterProvider
├── index.css                Tailwind 4 入口 + CSS 变量主题
├── app/
│   ├── router.tsx           createBrowserRouter，所有路由定义
│   └── navigation.ts        NAV_ITEMS：导航项的单一数据源
├── components/
│   ├── layout/
│   │   ├── AppShell.tsx       响应式外壳（侧栏 / 抽屉）
│   │   └── StagePlaceholder.tsx  未实现页面的诚实占位
│   └── ui/                  shadcn 生成的基础组件
├── lib/
│   └── utils.ts             cn() 等
└── pages/                   每个路由一个页面组件
```

### 路由

`app/router.tsx` 用 `createBrowserRouter`，`AppShell` 作为 layout route：

| 路径 | 页面 | 阶段 | 状态 |
|---|---|---|---|
| `/` | HomePage | 3 | ✅ 有真实数据（调 `/api/health`） |
| `/knowledge` | KnowledgeListPage | 1 | 占位 |
| `/knowledge/:knowledgeId` | KnowledgeDetailPage | 1 | 占位（显示真实 id） |
| `/study` | AiStudyRoomPage | 2 | 占位 |
| `/review` | ReviewCenterPage | 3 | 占位 |
| `/graph` | KnowledgeGraphPage | 4 | 占位 |
| `/settings` | SettingsPage | 2 | 占位 |
| `*` | NotFoundPage | — | ✅ 显示实际路径 |

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
  （`w-72 max-w-[85vw]`），带遮罩层，遮罩本身是
  `<button aria-label="关闭导航">` 而不是 div（键盘可达）
- **两个副作用**：路由变化时自动关抽屉；抽屉打开时锁
  `document.body.style.overflow`，防止背景跟着滚
- **触控目标 ≥ 44px**：导航项 `min-h-11`
- **零 hover 依赖**：所有交互在触屏上都能完成。
  hover 只用于视觉增强，不作为唯一触发方式
- **键盘可达**：`focus-visible:ring-2`，当前项 `active:bg-muted`

浏览器实测过：首页、知识详情深链（`/knowledge/<uuid>` 直接刷新）、
404 页、移动端抽屉导航全部正常，控制台零报错。

> 实测限制：`browser-use` 的截图和点击功能在本会话报
> `NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`（视口 0×0），
> 所以是用 `take_snapshot` + `evaluate_script` 直接驱动 React 事件处理器验证的，
> 并用 `getComputedStyle` 确认了 44px 最小高度和 Tailwind 类生效。
> **CSS 断点在真实视口下的表现没有完整验证过**，阶段 5 需要人工在手机上过一遍。

### 不用假数据伪装功能（Blueprint 10.2）

6 个未实现页面全部用 `StagePlaceholder` 渲染，明确显示：

- 「尚未实现」标题 + `Construction` 图标
- 「这个页面属于阶段 N。当前是阶段 0（项目骨架），此处不放演示数据，
  以免把未实现的功能伪装成已完成。」
- 「计划包含」列表，说明这个页面将来会有什么

只有 `HomePage` 展示真实数据（`/api/health` 的返回值）。

---

## 五、测试架构

```
tests/
├── conftest.py          夹具（全部走真实迁移 + 临时目录）
├── test_health.py       /api/health 的语义与隐私（5 个）
└── test_migrations.py   迁移结构与数据完整性保证（12 个）
```

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
    command.upgrade(make_alembic_config(), "head")   # 真实的 0001 迁移
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

---

## 八、当前实现状态（诚实版）

| 部分 | 状态 |
|---|---|
| 配置系统 | ✅ 完整 |
| 数据库连接与会话 | ✅ 完整（含 WAL、外键、超时 PRAGMA） |
| 迁移 0001（3 表 + 索引 + FTS5） | ✅ 完整，可 upgrade 可 downgrade |
| ORM 模型 | ✅ 3 个模型 + `UTCDateTime` |
| `/api/health` | ✅ 完整，含降级路径 |
| 前端静态托管 + SPA 回落 | ✅ 完整 |
| 前端外壳、路由、响应式导航 | ✅ 完整 |
| **业务 API（知识 CRUD、搜索）** | ❌ **未实现**（阶段 1） |
| **`backend/domain/`、`services/`、`schemas/`** | ❌ **空包**，只有 `__init__.py` |
| **AI 集成** | ❌ 未实现（阶段 2） |
| **复习系统** | ❌ 未实现（阶段 3） |
| **知识图谱** | ❌ 未实现（阶段 4） |
| **备份与导出** | ❌ 未实现（阶段 5） |
| 6 个业务页面 | ⬜ 诚实占位，明确标注所属阶段 |

**数据库当前 0 行数据**——骨架跑通了，但还没有任何业务写入路径。

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
