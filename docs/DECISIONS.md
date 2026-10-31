# DECISIONS —— 技术决策记录

记录**为什么这么做**，尤其是那些从代码本身看不出来、日后容易被"顺手改掉"的决策。
每条给出：决策 / 理由 / 代价或风险。推翻任何一条前请先读完对应理由。

---

## 一、环境与项目位置

### D-001 项目放在 `D:\PersonalKnowledgeOS`，不放 OneDrive 目录

- **决策**：项目独立于 OneDrive，放在 D 盘根目录下的独立文件夹。
- **理由**：最初计划放在 `桌面 OneDrive 同步目录下的一个私人文件夹` 下，
  并用 junction（目录联接）把 `node_modules`、`.venv`、`data` 指到 OneDrive 之外。
  **实测证明这条路走不通**：`npm install` 会删除并重建 `node_modules` 目录本身，
  junction 被当成普通目录删掉，之后的文件直接写进 OneDrive 同步范围，
  几万个碎文件把 OneDrive 卡死。`.venv` 同理（`uv sync` 会重建）。
- **代价**：项目不在 OneDrive 里，没有自动云备份。改为依赖 git 提交历史 +
  阶段 5 计划中的 `VACUUM INTO` 数据库备份。用户已确认接受。

### D-002 所有依赖版本锁定到实测通过的稳定版

- **决策**：`pyproject.toml` 用 `==` 精确锁版本；前端 `package.json` 同理。
- **理由**：这是长期使用的个人工具，稳定性优先于新特性。装依赖时逐个实测，
  发现两个"最新版"有真实问题：
  - **TypeScript 7.0.2**：`tsc -b` 的增量构建行为与 5.x 不同，shadcn 生成的
    `tsconfig.app.json` / `tsconfig.node.json` 项目引用报错。用 **5.9.3**。
  - **Vite 8.3.7**：`@vitejs/plugin-react` 5.2.0 尚未声明兼容 Vite 8，
    dev server 的 `/api` 代理在 Windows 上偶发 ECONNRESET。用 **7.3.7**。
- **代价**：不会自动获得安全更新。升级必须是刻意的动作，改版本后重跑全部验收。

### D-003 测试用 `httpx2` 而不是 `httpx`

- **决策**：dev 依赖里加 `httpx2==2.16.0`，运行时依赖仍是 `httpx==0.28.1`。
- **理由**：Starlette 1.10 起 `TestClient` 弃用 httpx，改用 httpx2。
  不换的话每次跑测试都刷弃用警告，而 `pyproject.toml` 里
  `filterwarnings = ["error::DeprecationWarning:backend.*"]` 会把警告升级成错误。
- **代价**：多一个测试专用依赖。可接受，它不进生产代码。

### D-004 仓库只在本地，没有 GitHub 远程

- **决策**：`git init` 后不配置 remote。
- **理由**：本机 Clash Verge 的代理规则拦了 github.com，`git push` 和 `gh` 都超时。
  这是网络环境问题，不是项目选择。
- **代价**：没有异地备份，磁盘坏了历史全丢。`scripts/doctor.py` 把这一项报成 WARN
  而不是 FAIL，并说明原因。网络恢复后 `git remote add origin <url> && git push -u`
  即可补上，代码本身不需要任何改动。

---

## 二、数据库与迁移

### D-005 状态和来源类型用字符串常量，不用 SQLAlchemy Enum

- **决策**：`Knowledge.status` / `source_type` 是 `String`，取值定义成模块级常量
  （`STATUS_ACTIVE`、`SOURCE_TYPE_ARCHIVED` 等），校验放在 Pydantic schema 层。
- **理由**：SQLite 上的 Enum 落成 `VARCHAR + CHECK` 约束，而 SQLite **不能
  ALTER TABLE 修改 CHECK 约束**。以后要加一个状态（比如阶段 4 的关系审核状态），
  Alembic 只能走 batch 模式把整张表重建一遍，数据量大时既慢又危险。
  字符串列 + 应用层校验，加取值就只是加一个常量。
- **代价**：数据库层面不拦截非法取值，脏数据只能靠 schema 校验和测试防。
  对单用户本地应用来说这个代价可以接受。

### D-006 FTS5 用**独立表**，不用外部内容表 + 触发器 ⚠️ 与最初计划不一致

- **决策**：`knowledge_fts` 是独立的 FTS5 虚拟表
  （`knowledge_id UNINDEXED, title, body, tokenize='trigram'`），
  同步逻辑写在 `services/search_service.py` 里，不用 SQLite 触发器。
- **理由**：**这一条偏离了向用户宣布过的计划。** 最初方案是"外部内容表 + 触发器同步"，
  写迁移时才发现做不到：要索引的内容横跨两张表
  —— `knowledge.title` 和 `knowledge_versions.user_understanding`，
  而 FTS5 的外部内容表（`content='...'`）只能绑定**单一**表。
  强行用外部内容表就得放弃对正文的索引，或者建两个 FTS 表再合并结果，
  两者都比独立表复杂。
- **触发器方案的另一个问题**：同步逻辑藏在 SQL 触发器里，
  Python 测试很难覆盖，出问题时调试要同时看 ORM 和原生 SQL 两套语义。
  放在 service 层可以用普通 pytest 断言。
- **代价**：写入路径必须记得调用同步函数，漏掉就会索引不一致。
  阶段 1 的测试必须专门覆盖"改标题后搜索能找到新标题、找不到旧标题"。

### D-007 中文检索：FTS5 trigram + LIKE 兜底的混合策略

- **决策**：`search_service` 先判断查询长度。≥3 个字符走 FTS5 `MATCH`；
  1~2 个字符走 `LIKE '%xx%'`。两条路径的结果做并集去重。
- **理由**：`tokenize='trigram'` 是 SQLite 内置分词器里**唯一**对中文有效的，
  但它要求 MATCH 的查询串**至少 3 个字符**，2 字词直接返回空。
  而「梯度」「复习」「熵」这类 1~2 字词在中文学习笔记里极其常见，
  搜不到就是功能不可用。
- **实测数据**：造了 5000 条中文知识数据，2 字查询走 LIKE 耗时 **1.0~1.4ms**，
  与 FTS5 结果一致。这个量级下不需要更复杂的方案（分词库、向量检索）。
- **代价**：数据涨到几十万条时 LIKE 会变慢。到那时再考虑加 jieba 或
  前缀索引，现在不做过早优化。

### D-008 `alembic.ini` 必须纯 ASCII

- **决策**：`alembic.ini` 里不写任何中文，说明性文字放 `docs/DATA_MODEL.md`。
- **理由**：**踩过的真实坑。** Alembic 用**操作系统区域编码**读这个文件
  （中文 Windows 上是 cp936/GBK），不是 UTF-8。
  文件里只要有一个中文字符，就会 `UnicodeDecodeError` 直接崩，
  报错信息完全看不出是编码问题。
- **代价**：这个文件的可读性差一点，靠注释里的英文说明弥补。

### D-009 Alembic 配置里的两个必要项

- **`path_separator = os`**：Alembic 1.20+ 要求显式声明，
  否则每个测试都会打印一条 DeprecationWarning（说它要按空格/逗号/冒号
  拆分 `prepend_sys_path`）。本项目 17 个测试原来刷了 16 条警告。
- **`render_as_batch=True`**（在 `migrations/env.py`）：SQLite 不支持
  `ALTER TABLE ADD CONSTRAINT`。循环外键回填、以后任何改列操作，
  都必须靠 batch 模式（建新表→搬数据→删旧表→改名）。不开这个，
  迁移 0001 本身就跑不过。
- **`include_object` 过滤器**：FTS5 虚拟表和它的影子表
  （`knowledge_fts_data` / `_idx` / `_config` / `_docsize`）由迁移用原生 SQL 管理，
  不在 ORM metadata 里。不排除的话 `alembic check` 会永远认为这些表是多余的，
  要求 drop 掉它们。

### D-010 数据库 URL 只从 `backend.config` 来，`alembic.ini` 里留空

- **决策**：`alembic.ini` 的 `sqlalchemy.url` 是空的，`migrations/env.py`
  调 `get_settings().database_url`。
- **理由**：端口、数据目录、凭据只能有一个来源。写两遍迟早不一致，
  而不一致的表现是"迁移跑在 A 库，应用读 B 库"，非常难查。
- **测试隔离**：早期版本用 `PKOS_MIGRATION_DATABASE_URL` 环境变量单独重定向迁移。
  **已删除**，因为测试改成设置 `PKOS_DATA_DIR` 把整个数据目录指到 `tmp_path`，
  迁移和应用自然落到同一个临时库。少一个环境变量，少一种不一致的可能。

### D-011 时间一律 UTC，且写库时硬性拒绝 naive datetime

- **决策**：`UTCDateTime` TypeDecorator。写入不带时区的 datetime 直接抛
  `ValueError`；读回来自动补 UTC 时区。全项目唯一的时间来源是
  `backend.db.base.utcnow()`。
- **理由**：Blueprint 9.2 明确要求"系统时间变化、跨日和时区转换不会导致
  复习日期错乱"。FSRS 的复习间隔以天为单位，用本地时间算，
  夏令时切换或者跨时区旅行就会算错一天。
  **在写库那一刻报错**，比等到复习日期莫名其妙错掉再回头查，成本低一个数量级。
- **代价**：调用方必须记得用 `utcnow()`，不能用 `datetime.now()`。
  SQLAlchemy 会把这个 ValueError 包成 `StatementError`，
  测试里要断言 `excinfo.value.__cause__` 才是原始 ValueError。

### D-012 SQLite 打开 WAL + 外键 + busy_timeout

- **决策**：每个新连接执行 `PRAGMA foreign_keys=ON`、`journal_mode=WAL`、
  `busy_timeout=5000`。
- **理由**：SQLite **默认不强制外键**，不开这个 PRAGMA，
  级联删除和 `NOT NULL` 之外的约束形同虚设，孤儿数据静默产生。
  WAL 让读写不互相阻塞（前端一边浏览、后台一边写索引时不会卡）。
- **⚠️ 备份注意**：开了 WAL 之后**绝对不能手动复制 `app.db`**。
  未落盘的数据在 `app.db-wal` 里，只复制主文件会拿到不完整的库。
  必须用 `VACUUM INTO`（阶段 5 实现）。README 的故障排查里也写了。

---

## 三、后端架构

### D-013 单端口部署：FastAPI 同时提供 API 和前端静态文件

- **决策**：生产模式下浏览器只访问 `http://127.0.0.1:8000`。
  `/api/*` 走 FastAPI 路由，其余路径回落 `frontend/dist/index.html`（SPA）。
- **理由**：Blueprint 6.4 要求。单端口意味着阶段 5 手机访问时
  只需要在防火墙放行一个端口，也不需要处理跨域。
- **两个实现细节**：
  - `/api/*` 未匹配到路由时返回 **JSON 404**，而不是把 index.html 当响应。
    否则前端调错 API 会拿到一个 HTML 页面，`response.json()` 抛的错
    完全看不出是路由写错了。
  - SPA 回落前做**目录穿越防护**：`candidate.is_relative_to(dist)`。
    没有这一行，`/../../.env` 就能读到配置文件。
- **降级路径**：`frontend/dist` 不存在时，如果
  `PKOS_ALLOW_MISSING_FRONTEND=true`（默认），后端照常启动并在 `/`
  返回一段 JSON 说明怎么构建前端。**不能让"前端没构建"变成"后端起不来"**，
  否则用户排查时分不清是哪个环节坏了。

### D-014 CORS 只放行本机 Vite 源，不用 `allow_origins=["*"]`

- **决策**：白名单是 `http://127.0.0.1:{frontend_port}` 和 `http://localhost:{frontend_port}`。
- **理由**：单端口模式同源，中间件本来不生效；开发模式前端在 5173 属于跨域，需要放行。
  但放行 `*` 意味着**局域网里任何网页**都能读用户的私人知识库
  （浏览器同源策略是唯一防线）。这是本地应用，没有理由开这么大。
- **代价**：如果以后改 `PKOS_FRONTEND_PORT`，CORS 白名单会自动跟着变
  （从 settings 读），不需要额外维护。

### D-015 引擎延迟创建 + `init_engine()` 可注入

- **决策**：`backend/db/session.py` 里 `_engine` 初始为 `None`，
  第一次用到才创建；测试可以调 `init_engine(临时库URL)` 替换全局引擎。
- **理由**：如果模块导入时就创建引擎，那么"导入 backend"这个动作本身
  就会碰 `data/app.db`。测试里任何一次 `import` 都可能污染真实数据。
  延迟创建让测试夹具能在此之前把 URL 换掉。
- **验证**：`tests/test_health.py::test_fixtures_never_touch_real_database`
  专门断言跑完 17 个测试后真实 `data/app.db` 仍然是 0 行。

### D-016 API Key 只在后端，且 `/api/health` 只返回布尔值

- **决策**：`/api/health` 返回 `ai_configured: true/false`，
  **不返回** key、base_url、model 名的任何片段。
- **理由**：Blueprint 9.1 硬要求。健康检查是前端首页就会调的接口，
  一旦它带上 base_url，key 的主机名就进了浏览器缓存和任何日志。
- **验证**：`test_health_never_leaks_api_key` 用哨兵值
  `sk-TEST-DO-NOT-LEAK-1234567890` 和 `https://example.invalid/v1` 设置配置，
  断言 `ai_configured is True` 但**哨兵字符串和主机名都不出现在响应全文里**。

---

## 四、Windows 专属陷阱（每一条都实测踩过）

这一节是本项目最贵的知识。全部是在这台中文 Windows 机器上真实踩到并修复的，
不是从文档里抄的注意事项。

### D-017 端口探测：必须先 `connect` 再 `bind`，且 bind 时**不能**设 SO_REUSEADDR

- **决策**：`doctor.py::port_state()` 先 `connect_ex()` 判断有没有服务在监听，
  再用一个**不设 SO_REUSEADDR** 的 socket 试 bind。
- **理由**：**这是本阶段最危险的一个 bug。** 只靠 bind 判断，
  8000 端口明明被占用却报"空闲"。根因是 Windows 的 SO_REUSEADDR 语义与 Unix 不同：
  **如果已监听的一方设了 SO_REUSEADDR（uvicorn 默认会设），
  第二个同样设了 SO_REUSEADDR 的 socket 可以成功 bind 到同一端口**，
  之后系统会把连接**静默地**分给两个进程。
- **后果（已避免）**：`start.ps1` 会在 8000 上再拉起一个服务器，
  用户的请求随机落到两个进程上，表现为"刷新一下页面数据就变了"这类
  极难排查的诡异现象。
- **验证方法**：写了 4 种探测策略的对比脚本，只有"connect 优先 + bind 不设
  SO_REUSEADDR"在所有情况下都给出正确答案。

### D-018 子进程输出要按 OEM 代码页解码，不能用 `text=True`

- **决策**：`doctor.py::run()` 用 `capture_output=True` **不带** `text=True`，
  拿到 bytes 后用 `windows_codepage()` 解码，`errors="replace"`。
  代码页通过 `ctypes.windll.kernel32.GetOEMCP()` 取，本机是 **cp936**。
- **理由**：`netstat` / `tasklist` / `netsh` 这些 Windows 控制台程序输出的是
  **本地代码页字节**，不是 UTF-8。设了 `PYTHONUTF8=1` 之后，
  `subprocess(text=True)` 会按 UTF-8 解码 GBK 字节 → `UnicodeDecodeError`
  → **读取线程被杀掉** → `communicate()` 返回空字符串。
- **症状的欺骗性**：不报错，只是显示"netstat 未列出监听项"，
  看起来像端口真的没人占用。实际是解码崩了。
- **同时**：模块导入时对 `sys.stdout` / `sys.stderr` 执行
  `reconfigure(errors="replace")`，这样即使输出到 GBK 控制台也不会因为
  某个字符编不出来而整个崩掉。

### D-019 `start.ps1` 必须是**带 BOM 的 UTF-8 + CRLF**

- **决策**：文件开头写 `\xef\xbb\xbf` BOM，行尾 CRLF，由 `.gitattributes`
  的 `*.ps1 text eol=crlf` 保证 checkout 后仍是 CRLF。
- **理由**：**这个 bug 让 start.ps1 一度有 5 个解析错误、完全无法运行。**
  本机只有 Windows PowerShell 5.1（没装 pwsh 7）。
  PowerShell 5.1 对**没有 BOM** 的文件按 ANSI（本机是 GBK）解码，
  中文的多字节序列会把紧随其后的引号或大括号一起吃掉，
  报出来的错是"表达式或语句中包含意外的标记'}'"和"字符串缺少终止符"，
  指向的行号（118/209/212）**根本没有语法问题**。
- **PowerShell 7 没这个问题**，但不能假设用户装了。
- **验证**：写了字节级检查脚本，确认 BOM 存在、CR 数 = LF 数 = 246、
  解析错误数 = 0；并做了 `rm` + `git checkout --` 往返测试，
  确认从 git 恢复出来的文件**字节完全一致**。

### D-020 PowerShell 里不要用中文智能引号 U+201C / U+201D

- **决策**：`start.ps1` 的字符串里用方括号 `[怎么办]` 或直角引号「」，
  绝不用 `“ ”`。文件头部有常驻警告注释。
- **理由**：PowerShell **把 U+201C/U+201D 当作合法的字符串定界符**。
  字符串里出现它们不会报错，而是**静默截断**——后半段内容直接消失。
- **症状**：某条提示输出到"怎么办：按上面的"就没了，
  没有任何报错。排查花的时间远超修复时间。

### D-021 `Get-Command npm` 解析到 `npm.ps1`，`Start-Process` 无法执行它

- **决策**：`start.ps1` 里的 `Get-NpmPath` 函数显式解析 `npm.cmd`，
  三处 npm 调用点全部走这个函数。
- **理由**：本机 `Get-Command npm` 返回的 `CommandType` 是 **ExternalScript**
  （即 `npm.ps1`），而 `Start-Process` 不能执行 PowerShell 脚本文件，
  报 `[%1 is not a valid Win32 application]`。
  `-Dev` 模式因此完全起不来 Vite。
- **验证**：写了 4 种写法的对比测试（`npm` / `npm.cmd` / `cmd /c npm` / `& npm`），
  只有 `npm.cmd` 和 `cmd.exe /c` 能正常拉起 Vite。选 `npm.cmd` 因为它更直接。
- **清理**：确认 `taskkill /PID <vitePid> /T /F` 能杀掉整棵进程树，
  两个端口都释放，不留孤儿 node 进程。

### D-022 `.gitattributes` 强制 `start.sh` 用 LF、`start.ps1` 用 CRLF+BOM

- **决策**：`* text=auto eol=lf` 作基线，`*.ps1/*.bat/*.cmd text eol=crlf` 覆盖，
  `*.sh text eol=lf` 显式声明。
- **理由**：`start.sh` 一旦变成 CRLF，shebang 行就成了
  `#!/usr/bin/env bash\r`，内核去找名为 `bash\r` 的解释器，
  报 `bad interpreter: No such file or directory`。这个错完全看不出是行尾问题。
- **验证**：`git ls-files --eol` 显示
  `i/lf w/crlf attr/text eol=crlf  start.ps1`（索引 LF，工作区 CRLF）和
  `i/lf w/lf attr/text eol=lf  start.sh`；
  并做了真实往返：备份 → `rm` → `git checkout --` → 字节比对完全一致。

### D-023 Clash Verge 可能拦截 localhost 请求

- **记录**：这不是项目代码的问题，是环境干扰，但会让人以为服务坏了。
- **表现**：服务确实在跑，端口确实在监听，但浏览器或 curl 访问
  `http://127.0.0.1:8000` 超时或返回代理错误页。
- **验证命令**：`curl.exe --noproxy "*" http://127.0.0.1:8000/api/health`
  —— 加 `--noproxy "*"` 后正常，就确认是代理拦截。
- **解决**：在 Clash 规则里把 `127.0.0.1` / `localhost` 加入 DIRECT。
  README 故障排查里有这一条。

---

## 五、测试与工具链

### D-024 pytest 需要 `pythonpath = ["."]`

- **决策**：`pyproject.toml` 的 `[tool.pytest.ini_options]` 里加 `pythonpath = ["."]`。
- **理由**：本项目**不是可安装包**（`[tool.uv] package = false`，
  因为它是应用而不是库）。pytest 默认只把 `tests/` 加进 `sys.path`，
  于是 `import backend` 直接 `ImportError: No module named 'backend'`。
- **代价**：无。这一行就是标准解法。

### D-025 测试跑真实 Alembic 迁移，不用 `create_all()`

- **决策**：`tests/conftest.py::migrated_database` 夹具调用
  `alembic.command.upgrade(config, "head")`，在 `tmp_path` 下的临时库上
  执行**真实的迁移脚本**。
- **理由**：`Base.metadata.create_all()` 会**跳过所有原生 SQL**，
  也就是说 FTS5 虚拟表根本不会被创建，循环外键的回填顺序也不会被验证。
  用 `create_all()` 的测试全绿，但用户跑 `alembic upgrade head` 可能直接失败——
  这是最糟糕的一种"测试通过"。
- **同时**：`alembic check` 进 CI 心智，用来保证 ORM 模型和迁移脚本没有漂移。

### D-026 `scripts/doctor.py` 只用标准库

- **决策**：doctor.py **不导入任何第三方包**，包括不导入 `backend`。
- **理由**：环境自检最需要的时刻恰恰是**依赖还没装好、装失败**的时候。
  如果 doctor 依赖 `backend.config`，那么在 `uv sync` 之前它自己就 import 失败，
  用户连诊断信息都拿不到。
- **代价**：不能复用 `Settings` 的配置解析，`.env` 得自己解析一遍。
  这个重复是值得的。

### D-027 doctor 把"无法修复的问题"报成 WARN，把"必须修的问题"报成 FAIL

- **决策**：只有 FAIL 会让 `setup.py` / `start.ps1` 中止。
  GitHub 远程缺失、AI 未配置、前端未构建都是 WARN。
- **理由**：AI 没配 key 时，手动记录和浏览知识**必须仍然可用**
  （Blueprint 阶段 2 验收标准）。把可选功能报成 FAIL 会让用户以为项目坏了。

### D-028 `start.sh` 的三个设计约束

- **不能用子 shell 包 `pushd`**：`( cd frontend && ... )` 里的 `fail` 只退出子 shell，
  外层继续跑，错误被吞掉。改用 `pushd` / `popd`。
- **端口探测的 Python heredoc 用 argv 传参**，不把 shell 变量插进 Python 源码。
  插值等于给自己造一个注入点，而且变量里有引号就语法错误。
  写成 `python - "$HOST" "$1" <<'PY'`（单引号 heredoc，不做 shell 展开）。
- **Git Bash 没有真正的进程组**，`kill -- -$pid` 不管用。
  Windows 下走 `taskkill //PID $pid //T //F`（双斜杠是 MSYS 的路径转换逃逸），
  其他平台用进程组。

---

## 六、产品原则的技术落点

Blueprint 1.5 的七条原则不是口号，每条都有对应的技术约束：

| 原则 | 技术落点 | 验证方式 |
|---|---|---|
| 原始记录永不被 AI 覆盖 | `knowledge_versions` 追加式，`(knowledge_id, version_number)` 唯一约束；AI 修改写进 `ai_revision` 字段，`user_understanding` 不动 | `test_original_understanding_survives_new_version` |
| AI 建议需审核 | 阶段 2 的 AI 反馈独立存储，不进正式知识表 | 待阶段 2 |
| 复习评级由用户确认 | 阶段 3 的 `review_sessions` 同时记 AI 建议评级和用户最终评级 | 待阶段 3 |
| 知识关系审核后才建立 | 阶段 4 的 `knowledge_relations.status`：pending / accepted / rejected，图谱只查 accepted | 待阶段 4 |
| 本地优先 | SQLite 文件在 `data/`，无云同步代码；CORS 白名单只有本机 | `test_health_never_leaks_api_key` |
| 最小必要传输 | 阶段 2 的 AI 调用只发当前知识单元，不发全库 | 待阶段 2 |
| 移动端友好 | 导航项 `min-h-11`（44px），无任何 hover 依赖，抽屉导航 + 滚动锁定 | 浏览器实测 + `getComputedStyle` 验证 |
| 不用假数据伪装功能 | 6 个未实现页面用 `StagePlaceholder` 明确标注"尚未实现"和所属阶段 | 浏览器实测 |

---

## 待补决策（后续阶段）

- **阶段 2**：AI Provider 抽象层的接口形状；结构化输出的 Schema 校验策略；
  超时与重试；流式响应是否要做。
- **阶段 3**：`review_schedules` 与 fsrs 6.3.2 `Card` 的字段映射
  （见 ROADMAP 阶段 3 的风险记录：`reps` / `lapses` / `elapsed_days` /
  `scheduled_days` 四个字段 FSRS 库不提供，得从 `review_sessions` 推算）。
  测试必须用 `Scheduler(enable_fuzzing=False)`，否则结果不确定。
- **阶段 4**：图谱可视化选 React Flow 还是 Cytoscape.js；候选召回的具体策略。
- **阶段 5**：备份的具体形式（`VACUUM INTO` 已定，触发方式和保留策略未定）；
  PWA 的离线范围。
