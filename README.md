# Personal Knowledge OS

个人知识管理与长期学习系统。**本地优先**，数据全部存在你自己电脑上。

核心理念一句话：**记录自己的理解 → 让 AI 检验和修正 → 通过科学复习巩固记忆 → 再把知识连成网络。**

这不是普通的笔记软件。它的核心资产不是你收藏了多少文章，而是**你对知识形成的理解**，以及这些理解如何随时间变化、修正和相互连接。

---

## 现在能做什么（重要）

项目按阶段开发，**当前只完成了阶段 0：项目骨架**。

| 状态 | 内容 |
|---|---|
| ✅ 已可用 | 本地数据库与迁移、配置管理、环境自检、一键安装、一键启动、响应式界面外壳、首页真实自检 |
| ⬜ 尚未实现 | 知识的增删改查（阶段 1）、AI 对话与审查（阶段 2）、复习与 FSRS（阶段 3）、知识图谱（阶段 4） |

界面上除了首页，其他页面都是**明确标注「尚未实现」的占位页**，不会用假数据冒充功能。完整路线图见 [docs/ROADMAP.md](docs/ROADMAP.md)。

---

## 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10/11 | 也支持 Linux / macOS |
| Python | 3.13 或更新 | 本项目在 3.14 上开发验证 |
| uv | 任意较新版本 | Python 依赖管理器，负责建虚拟环境 |
| Node.js | 22 或更新 | 前端构建需要 |
| 磁盘空间 | 1 GB 以上 | 依赖 + 数据库 |

不确定自己装没装？直接跑自检，它会逐项告诉你缺什么、怎么补：

```powershell
python scripts/doctor.py
```

---

## 安装

在项目根目录（`D:\PersonalKnowledgeOS`）打开 PowerShell，执行一条命令：

```powershell
python scripts\setup.py
```

它会依次完成：环境自检 → 安装后端依赖 → 生成 `.env` → 建数据库并迁移 → 安装前端依赖 → 构建前端。

**这个脚本可以重复运行**，已经完成的步骤会自动跳过。任何一步失败都会停下来并告诉你怎么办。

> 如果提示脚本被禁止运行，用这条命令绕过（只对当前这次生效，不改动系统设置）：
> ```powershell
> powershell -ExecutionPolicy Bypass -File scripts\setup.py
> ```
> 实际上 `setup.py` 是 Python 脚本，一般不受执行策略限制；受限制的是下面的 `start.ps1`。

---

## 启动

### PowerShell（推荐）

```powershell
.\start.ps1
```

浏览器会自动打开 <http://127.0.0.1:8000>。**一个端口跑完整个应用**——页面和 API 由同一个进程提供，不需要另外开前端服务。

如果提示「禁止运行脚本」，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

或者一次性放开当前用户的限制（只需做一次）：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Git Bash / Linux / macOS

```bash
./start.sh
```

### 开发模式（改代码自动刷新）

```powershell
.\start.ps1 -Dev        # PowerShell
./start.sh --dev        # Git Bash
```

开发模式会额外启动 Vite（端口 5173），浏览器访问 <http://127.0.0.1:5173>。改前端代码自动刷新，改后端代码自动重启。

### 其他参数

| 参数 | 作用 |
|---|---|
| `-NoBrowser` / `--no-browser` | 启动但不自动打开浏览器 |
| `-SkipDoctor` / `--skip-doctor` | 跳过启动前的环境自检（更快，但出问题时报错更难懂） |
| `--help` | 显示帮助（仅 `start.sh`） |

停止服务：在窗口里按 `Ctrl+C`。子进程会被一并清理，不会留下孤儿进程继续占着端口。

---

## 目录结构

```
PersonalKnowledgeOS/
├── start.ps1              Windows PowerShell 启动脚本
├── start.sh               Git Bash / Linux / macOS 启动脚本
├── pyproject.toml         Python 依赖与版本锁定
├── uv.lock                依赖精确版本（保证换台机器装出来一样）
├── alembic.ini            数据库迁移配置
├── .env.example           配置模板（复制成 .env 后修改）
│
├── backend/               后端（FastAPI）
│   ├── config.py          所有配置的唯一来源：端口、数据目录、AI 密钥
│   ├── main.py            应用入口，同时托管 API 和前端页面
│   ├── api/               接口层
│   ├── db/                数据库：ORM 模型、会话、列类型
│   └── ...                services/ domain/ 在后续阶段补齐
│
├── frontend/              前端（React + TypeScript + Vite）
│   └── src/
│       ├── app/           路由与导航配置
│       ├── components/    通用组件（含 shadcn/ui）
│       ├── pages/         六个页面
│       └── lib/           工具函数
│
├── migrations/            数据库迁移脚本（版本受控，不要手改已提交的迁移）
├── scripts/
│   ├── doctor.py          环境自检
│   └── setup.py           一键安装
├── tests/                 pytest 测试
├── docs/                  项目文档
└── data/                  你的数据（数据库、备份）——已被 Git 忽略，绝不提交
```

---

## 常用命令

在项目根目录执行：

| 目的 | 命令 |
|---|---|
| 环境自检 | `python scripts/doctor.py` |
| 跑测试 | `uv run pytest` |
| 跑测试（带覆盖率详情） | `uv run pytest -v` |
| 数据库迁移到最新 | `uv run alembic upgrade head` |
| 看当前迁移版本 | `uv run alembic current` |
| 检查模型与迁移是否一致 | `uv run alembic check` |
| 生成新迁移 | `uv run alembic revision --autogenerate -m "说明"` |
| 前端类型检查 | `cd frontend && npx tsc -b` |
| 前端构建 | `cd frontend && npm run build` |
| 前端开发服务器 | `cd frontend && npm run dev` |

---

## 配置说明

所有配置都通过环境变量或 `.env` 文件读取，前缀统一是 `PKOS_`。**代码里没有硬编码的端口或路径。**

复制模板生成自己的配置：

```powershell
Copy-Item .env.example .env
```

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PKOS_HOST` | `127.0.0.1` | 监听地址。只本机访问就用默认值 |
| `PKOS_PORT` | `8000` | 后端端口（单端口模式下也是页面端口） |
| `PKOS_FRONTEND_PORT` | `5173` | 仅开发模式用到的 Vite 端口 |
| `PKOS_DATA_DIR` | 项目内 `data/` | 数据库和备份存放位置。留空即用默认 |
| `PKOS_AI_PROVIDER` | 空 | 阶段 2 才需要 |
| `PKOS_AI_BASE_URL` | 空 | 阶段 2 才需要 |
| `PKOS_AI_API_KEY` | 空 | **密钥。只放在这里，绝不要写进前端代码** |
| `PKOS_AI_MODEL` | 空 | 阶段 2 才需要 |
| `PKOS_AI_TIMEOUT_SECONDS` | `60` | AI 请求超时 |
| `PKOS_LOG_LEVEL` | `INFO` | 日志级别 |
| `PKOS_ALLOW_MISSING_FRONTEND` | `true` | 前端没构建时是否仍允许只启动 API |

`.env` 已被 `.gitignore` 忽略，不会被提交。

---

## 数据与备份

- 数据库文件：`data/app.db`（SQLite 单文件）
- 备份目录：`data/backups/`
- 数据库启用了 WAL 模式，所以同目录下还会看到 `app.db-wal` 和 `app.db-shm`

**不要直接复制 `app.db` 来备份。** WAL 模式下最新数据可能还没写进主文件，直接复制会拿到不完整的库。正确做法是用 SQLite 的 `VACUUM INTO`（备份功能会在阶段 5 提供界面和命令）。

`data/` 整个目录已被 Git 忽略，你的知识数据不会被提交到版本库。

---

## 隐私与安全

- **数据默认只在本机。** 知识、版本、复习记录、对话都存在你电脑的 SQLite 里。
- **只有调用 AI 功能时才会向外部发送数据**，且遵循最小必要原则——不会默认上传整个知识库。
- **API Key 只从后端环境变量读取**，不出现在前端代码、接口响应、日志或版本库中。首页的自检接口只返回「配没配」，不返回密钥内容。
- 服务默认只监听 `127.0.0.1`，不暴露到局域网或公网。

---

## 故障排查

### 1. 端口被占用

自检会直接告诉你是谁占的：

```
[XX]  后端端口 8000   已有服务在监听（PID 53328（python.exe））
       ↳ 怎么办：关掉占用进程，或在 .env 里设置 PKOS_PORT 换一个端口
```

结束占用进程：

```powershell
taskkill /PID 53328 /F
```

换端口：编辑 `.env` 里的 `PKOS_PORT`，前端会自动跟随，不用改代码。

### 2. 端口空闲却还是绑定失败

Windows 上 Hyper-V / WSL 会**保留**一大片端口，落在保留段里绑定时会报「拒绝访问」。自检会识别这种情况：

```
[XX]  后端端口 8000   落在 Windows 保留端口段 50000-50059 内，绑定会被系统拒绝
```

查看系统保留了哪些端口：

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

解决办法是换一个不在保留段里的端口。

### 3. 代理软件拦截 localhost（Clash / Verge 等）

**症状**：服务明明启动了，浏览器却打不开 <http://127.0.0.1:8000>，或者报连接被重置。

**原因**：代理软件把本机回环地址也走了代理。

**解决办法**（任选其一）：
- 在代理软件里把 `127.0.0.1`、`localhost` 加入绕过列表（bypass / 直连规则）
- 临时关闭系统代理
- 关闭代理软件的「TUN 模式 / 增强模式」

命令行快速验证是不是代理的问题：

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8000/api/health
```

如果加了 `--noproxy "*"` 就通了，那确定是代理拦截。

### 4. 中文输出变成乱码

启动脚本已经设置了 `PYTHONUTF8=1`，正常情况下不会乱码。如果你是**直接**运行某个 Python 脚本看到乱码，先设置：

```powershell
$env:PYTHONUTF8 = "1"
chcp 65001
```

### 5. PowerShell 提示禁止运行脚本

见上面「启动」一节的 `Set-ExecutionPolicy` 说明。

### 6. `npm ci` 很慢或失败

配置国内镜像：

```powershell
npm config set registry https://registry.npmmirror.com
```

### 7. 数据库损坏或迁移失败

先备份现有文件，再决定：

```powershell
Copy-Item data\app.db data\app.db.broken-backup
uv run alembic current          # 看当前在哪个版本
uv run alembic upgrade head     # 尝试继续迁移
```

如果数据库是新建的、里面没有数据，最简单的办法是删掉 `data/app.db*` 三个文件后重新运行 `python scripts\setup.py`。**里面有数据时不要这么做。**

### 8. 前端页面空白

- 确认构建产物存在：`frontend/dist/index.html`
- 确认访问的是后端端口（单端口模式 8000），而不是 Vite 端口
- 打开浏览器开发者工具（F12）看 Console 报错
- 访问 <http://127.0.0.1:8000/api/health> 看后端自检结果

---

## 开发约定

- **依赖版本是锁定的**，升级需要明确理由并记录到 [docs/DECISIONS.md](docs/DECISIONS.md)。
- **每完成一个阶段**：跑测试、类型检查、构建，更新 README 和开发记录，再开始下一阶段。
- **不要手改已经提交的迁移文件**，要改就新建一个迁移。
- `start.ps1` 必须是**带 BOM 的 UTF-8**，`start.sh` 必须是 **LF 换行**——`.gitattributes` 已经强制了这一点，不要绕过它。

更多细节：

| 文档 | 内容 |
|---|---|
| [docs/PRD.md](docs/PRD.md) | 产品需求与范围边界 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构分层、模块边界、数据流 |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | 数据表、字段、关系与迁移说明 |
| [docs/AI_WORKFLOWS.md](docs/AI_WORKFLOWS.md) | AI 提示词、输入输出 Schema、安全边界 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 阶段、任务、验收标准、已知问题 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 重要技术决策及理由 |
