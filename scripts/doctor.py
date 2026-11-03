"""环境自检脚本。

只依赖标准库，因此在 `uv sync` 之前也能运行——环境坏掉的时候正是最需要它的时候。

用法：
    python scripts/doctor.py            # 完整自检
    python scripts/doctor.py --json     # 机器可读输出（供 setup.py / start.ps1 调用）

退出码：0 = 没有 FAIL；1 = 至少一项 FAIL。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 控制台可能是 GBK(cp936)。errors="replace" 保证遇到编不出的字符时降级而不是崩溃。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

MARKER = {PASS: "[OK]", WARN: "[!!]", FAIL: "[XX]"}

MIN_PYTHON = (3, 13)
MIN_NODE = 22

# 后端运行必需的包。版本以 pyproject.toml 锁定为准，这里只检查“能不能导入”。
REQUIRED_BACKEND_PACKAGES = [
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("sqlalchemy", "sqlalchemy"),
    ("alembic", "alembic"),
    ("pydantic", "pydantic"),
    ("pydantic_settings", "pydantic-settings"),
    ("fsrs", "fsrs"),
]


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        }


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, hint: str | None = None) -> None:
        self.results.append(CheckResult(name, status, detail, hint))

    @property
    def failed(self) -> bool:
        return any(r.status == FAIL for r in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": not self.failed,
            "counts": {
                PASS: sum(1 for r in self.results if r.status == PASS),
                WARN: sum(1 for r in self.results if r.status == WARN),
                FAIL: sum(1 for r in self.results if r.status == FAIL),
            },
            "results": [r.to_dict() for r in self.results],
        }


def windows_codepage() -> str:
    """Windows 控制台程序（netstat / tasklist / netsh）输出的是本地代码页字节。

    启动脚本会设置 PYTHONUTF8=1，此时 locale 编码变成 utf-8，
    如果让 subprocess 用 text=True 自动解码，读到 GBK 字节就会抛 UnicodeDecodeError
    并且把读线程干掉，结果 netstat 输出变成空串、查不到占用进程。
    所以这里显式按字节抓取，再用真实代码页解码。
    """
    if platform.system() != "Windows":
        return "utf-8"
    try:
        import ctypes

        codepage = ctypes.windll.kernel32.GetOEMCP()
        return f"cp{codepage}" if codepage else "utf-8"
    except Exception:
        return "utf-8"


OUTPUT_ENCODING = windows_codepage()


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    """运行外部命令，返回 (退出码, 合并输出)。命令不存在时返回 (127, ...)。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
            # Windows 上不要让子命令弹窗
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        return 127, f"找不到命令：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（{timeout}s）：{' '.join(cmd)}"

    # errors="replace"：代码页判断错时最多是乱码，不会崩溃。
    # 我们从这些命令里只取 PID 和进程名（都是 ASCII），乱码不影响结果。
    parts = [
        (proc.stdout or b"").decode(OUTPUT_ENCODING, errors="replace"),
        (proc.stderr or b"").decode(OUTPUT_ENCODING, errors="replace"),
    ]
    return proc.returncode, "".join(parts).strip()


def venv_python() -> Path | None:
    """项目虚拟环境里的 Python。Windows 在 Scripts/，其他平台在 bin/。"""
    for candidate in (
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        PROJECT_ROOT / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return candidate
    return None


def parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


# --------------------------------------------------------------------------
# 各项检查
# --------------------------------------------------------------------------


def check_platform(report: Report) -> None:
    report.add(
        "操作系统",
        PASS,
        f"{platform.system()} {platform.release()} ({platform.machine()})",
    )


def check_python(report: Report) -> None:
    version = sys.version_info
    text = platform.python_version()
    if (version.major, version.minor) >= MIN_PYTHON:
        report.add("Python 版本", PASS, f"{text}（{sys.executable}）")
    else:
        report.add(
            "Python 版本",
            FAIL,
            f"{text}，低于要求的 {'.'.join(map(str, MIN_PYTHON))}",
            "安装 Python 3.13 或更新版本：https://www.python.org/downloads/",
        )


def check_uv(report: Report) -> None:
    code, output = run(["uv", "--version"], timeout=20)
    if code == 0:
        report.add("uv（Python 包管理器）", PASS, output.splitlines()[0] if output else "已安装")
    elif code == 127:
        report.add(
            "uv（Python 包管理器）",
            FAIL,
            "未安装或不在 PATH 中",
            "PowerShell 执行：powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"",
        )
    else:
        report.add("uv（Python 包管理器）", WARN, f"无法获取版本：{output[:200]}")


def check_node(report: Report) -> None:
    code, output = run(["node", "--version"], timeout=20)
    if code != 0:
        report.add(
            "Node.js",
            FAIL,
            "未安装或不在 PATH 中",
            "安装 Node.js LTS：https://nodejs.org/",
        )
        return
    version = parse_version(output)
    if version and version[0] >= MIN_NODE:
        report.add("Node.js", PASS, output.strip())
    else:
        report.add(
            "Node.js",
            FAIL,
            f"{output.strip()}，低于要求的 v{MIN_NODE}",
            f"安装 Node.js {MIN_NODE} 或更新版本：https://nodejs.org/",
        )


def check_backend_deps(report: Report) -> None:
    python = venv_python()
    if python is None:
        report.add(
            "后端依赖",
            FAIL,
            "找不到项目虚拟环境 .venv",
            "在项目根目录执行：uv sync",
        )
        return

    program = (
        "import importlib,sys\n"
        "mods=%r\n"
        "missing=[]\n"
        "for name,pkg in mods:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except Exception as exc:\n"
        "        missing.append(f'{pkg}: {type(exc).__name__}')\n"
        "print('MISSING=' + ','.join(missing))\n" % (REQUIRED_BACKEND_PACKAGES,)
    )
    code, output = run([str(python), "-c", program], timeout=120)
    if code != 0:
        report.add("后端依赖", FAIL, f"检查失败：{output[:300]}", "在项目根目录执行：uv sync")
        return

    match = re.search(r"MISSING=(.*)", output)
    missing = [item for item in (match.group(1).split(",") if match else []) if item]
    if missing:
        report.add(
            "后端依赖",
            FAIL,
            f"缺少 {len(missing)} 个包：{'；'.join(missing)}",
            "在项目根目录执行：uv sync",
        )
    else:
        report.add(
            "后端依赖",
            PASS,
            f"{len(REQUIRED_BACKEND_PACKAGES)} 个核心包全部可导入",
        )


def check_frontend_deps(report: Report) -> None:
    node_modules = PROJECT_ROOT / "frontend" / "node_modules"
    lockfile = PROJECT_ROOT / "frontend" / "package-lock.json"
    if not node_modules.is_dir():
        report.add(
            "前端依赖",
            FAIL,
            "frontend/node_modules 不存在",
            "执行：cd frontend && npm ci",
        )
        return
    if not lockfile.exists():
        report.add("前端依赖", WARN, "已安装，但缺少 package-lock.json（无法保证版本可复现）")
        return
    count = sum(1 for _ in node_modules.iterdir())
    report.add("前端依赖", PASS, f"已安装（node_modules 下 {count} 个条目）")


def check_frontend_build(report: Report) -> None:
    index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if index.exists():
        report.add("前端构建产物", PASS, str(index.relative_to(PROJECT_ROOT)))
    else:
        report.add(
            "前端构建产物",
            WARN,
            "frontend/dist 尚未构建",
            "执行：cd frontend && npm run build。开发模式（start.ps1 -Dev）不需要构建。",
        )


def read_env_text() -> tuple[str | None, str | None]:
    """读 .env 的文本。返回 (文本, 错误说明)，两者只有一个不是 None。

    刻意**严格**按 UTF-8 解码而不是 errors="replace"：本项目的 .env 里全是中文注释，
    而 Windows 的 Notepad「另存为」默认用 ANSI(GBK)。那种文件会让后端在读配置时
    直接抛 UnicodeDecodeError，进程根本起不来——自检必须在这一项上说实话，
    replace 掉的话这里就永远看不出问题。
    """
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        return None, None
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return env.read_text(encoding=encoding), None
        except UnicodeDecodeError as exc:
            problem = f"{encoding} 解码失败（第 {exc.start} 字节起：{exc.reason}）"
        except OSError as exc:
            problem = f"读不出来：{type(exc).__name__}: {exc}"
            break
    return None, problem


def parse_env_values(text: str) -> dict[str, str]:
    """取 `KEY=value` 的成对值。注释行和 `export ` 前缀都认。

    这里只是自检用的最小解析，不追求和 python-dotenv 一模一样：
    本文件必须在 `uv sync` 之前也能跑，所以不能依赖任何第三方库。
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def check_env_file(report: Report) -> None:
    env = PROJECT_ROOT / ".env"
    example = PROJECT_ROOT / ".env.example"
    text, error = read_env_text()

    if error is not None:
        report.add(
            ".env 配置文件",
            FAIL,
            f"存在但读不出文本：{error}",
            "用编辑器把 .env 另存为 UTF-8 编码（VS Code 右下角点编码名 → Save with encoding → UTF-8）；"
            "或者删掉它，让它按 .env.example 的默认值重新生成",
        )
        return
    if text is not None:
        report.add(".env 配置文件", PASS, "已存在且可按 UTF-8 读取（该文件被 Git 忽略，不会提交）")
    elif example.exists():
        report.add(
            ".env 配置文件",
            WARN,
            "不存在，将使用代码内默认值",
            "如需自定义端口或填写 AI 密钥：复制 .env.example 为 .env 后修改",
        )
    else:
        report.add(".env 配置文件", WARN, ".env 与 .env.example 都不存在")


def check_ai_config(report: Report) -> None:
    """AI 配置的完整性。⚠️ 全部只报 WARN，不报 FAIL（DECISIONS D-027）。

    理由：没配 AI 是这个产品的**合法状态**——阶段 2 的硬约束 C4 就是
    "不配 AI，记录、检索、复习这些核心功能必须照常可用"。
    把它报成 FAIL 会让人以为环境坏了。
    """
    text, error = read_env_text()
    if error is not None:
        # 编码问题已经在 check_env_file 里报 FAIL，这里不重复
        return
    values = parse_env_values(text) if text else {}

    base_url = values.get("PKOS_AI_BASE_URL", "")
    model = values.get("PKOS_AI_MODEL", "")
    api_key = values.get("PKOS_AI_API_KEY", "")

    if not base_url and not model and not api_key:
        report.add(
            "AI 服务配置",
            WARN,
            "未配置（记录、检索、复习不受影响，AI 检验与提取暂不可用）",
            "启动后打开 设置 页填写并保存，或直接编辑 .env 里的 PKOS_AI_BASE_URL / PKOS_AI_MODEL / PKOS_AI_API_KEY",
        )
        return

    missing = [
        name
        for name, value in (("PKOS_AI_BASE_URL", base_url), ("PKOS_AI_MODEL", model))
        if not value
    ]
    if missing:
        report.add(
            "AI 服务配置",
            WARN,
            f"配了一半，还缺 {', '.join(missing)}",
            "发请求需要 API 地址和模型名两项都齐；密钥只有本地端点（Ollama 等）可以不填",
        )
        return

    detail = f"地址 {base_url}，模型 {model}，密钥{'已填' if api_key else '未填'}"
    if not api_key and not _is_local_endpoint(base_url):
        report.add(
            "AI 服务配置",
            WARN,
            detail + "：远程服务一般都需要密钥",
            "在设置页填入 PKOS_AI_API_KEY；只有本地 Ollama / LM Studio 这类端点可以不填",
        )
        return
    if base_url.rstrip("/").endswith("/v1") or "/v1/" in base_url:
        report.add("AI 服务配置", PASS, detail + "（地址写法正确，含 /v1）")
        return
    report.add(
        "AI 服务配置",
        WARN,
        detail + "：地址结尾没有 /v1",
        "OpenAI 兼容端点的地址通常要填到版本路径为止，例如 https://api.deepseek.com/v1。"
        "少了 /v1 会得到「请求被拒绝」，那不是密钥错了",
    )


def _is_local_endpoint(base_url: str) -> bool:
    return "://127." in base_url or "://localhost" in base_url or "://0.0.0.0" in base_url


def check_env_var_shadowing(report: Report) -> None:
    """真实环境变量优先于 .env。这一条专门说给"改了却没生效"的人听。

    用户可能为了别的项目在系统环境变量里配过 PKOS_AI_*，
    那时后设置页写进 .env 的值**不会生效**——
    不说清楚，症状看起来就是"这个程序有 bug"。
    """
    shadowed = [key for key in os.environ if key.startswith("PKOS_AI_")]
    if not shadowed:
        return
    report.add(
        "AI 配置来源",
        WARN,
        f"进程环境里有 {len(shadowed)} 个 PKOS_AI_* 变量：{', '.join(sorted(shadowed))}",
        "环境变量优先级高于 .env。设置页保存的这些项不会生效，"
        "要改就改环境变量（系统设置 → 环境变量），或者删掉它们再用设置页",
    )


def check_data_dir(report: Report) -> None:
    """数据目录可写性。路径从 .env 的 PKOS_DATA_DIR 读取，不硬编码。"""
    data_dir = PROJECT_ROOT / "data"
    env = PROJECT_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("PKOS_DATA_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    data_dir = Path(value).expanduser()
                break

    probe = data_dir / ".doctor_write_probe"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        report.add(
            "数据目录可写",
            FAIL,
            f"{data_dir} 不可写：{type(exc).__name__}: {exc}",
            "检查目录权限，或在 .env 里把 PKOS_DATA_DIR 改到一个可写位置",
        )
        return

    usage = shutil.disk_usage(data_dir)
    free_gb = usage.free / (1024**3)
    status = PASS if free_gb >= 1.0 else WARN
    report.add(
        "数据目录可写",
        status,
        f"{data_dir}（所在磁盘剩余 {free_gb:.1f} GB）",
        None if free_gb >= 1.0 else "磁盘剩余空间不足 1 GB，建议清理后再使用",
    )


def excluded_port_ranges() -> list[tuple[int, int]]:
    """Windows 保留端口段（Hyper-V / WSL 会占用一大片端口，绑定时报“拒绝访问”）。"""
    if platform.system() != "Windows":
        return []
    code, output = run(
        ["netsh", "interface", "ipv4", "show", "excludedportrange", "protocol=tcp"],
        timeout=30,
    )
    if code != 0:
        return []
    ranges: list[tuple[int, int]] = []
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s*", line)
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            # 过滤掉表头里的无关数字行
            if 0 < start <= 65535 and start <= end <= 65535:
                ranges.append((start, end))
    return ranges


def port_state(port: int, host: str = "127.0.0.1") -> tuple[str, str]:
    """返回 (状态, 说明)。

    必须先 connect 再 bind：Windows 上如果监听方设了 SO_REUSEADDR（uvicorn 默认会设），
    第二个同样设了 SO_REUSEADDR 的 socket 可以成功 bind 到同一端口，
    只靠 bind 判断会把“已被占用”误报成“空闲”。真发生的话启动脚本会拉起第二个服务，
    系统再把请求随机分给两个进程，症状极难排查。
    """
    for start, end in excluded_port_ranges():
        if start <= port <= end:
            return FAIL, f"落在 Windows 保留端口段 {start}-{end} 内，绑定会被系统拒绝"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((host, port)) == 0:
            return FAIL, f"已有服务在监听（{describe_port_owner(port)}）"

    bind_error: str | None = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return PASS, "空闲，可以绑定"
        except OSError as exc:
            bind_error = f"{exc.errno} {exc.strerror or exc}"

    return FAIL, f"无法绑定（{bind_error}）{describe_port_owner(port)}"


def describe_port_owner(port: int) -> str:
    """用 netstat + tasklist 查出占用端口的进程，给用户一个可操作的信息。"""
    code, output = run(["netstat", "-ano"], timeout=30)
    if code != 0:
        return "无法确定占用进程"
    for line in output.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            pid = line.split()[-1]
            task_code, task_out = run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=20
            )
            name = task_out.split('"')[1] if task_code == 0 and '"' in task_out else ""
            return f"PID {pid}（{name}）" if name else f"PID {pid}"
    return "netstat 未列出监听项"


def check_ports(report: Report) -> None:
    """端口从 .env 读取，默认 8000 / 5173，与 .env.example 一致。"""
    backend_port, frontend_port = 8000, 5173
    env = PROJECT_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("PKOS_PORT="):
                backend_port = int(line.split("=", 1)[1] or 8000)
            elif line.startswith("PKOS_FRONTEND_PORT="):
                frontend_port = int(line.split("=", 1)[1] or 5173)

    status, detail = port_state(backend_port)
    report.add(
        f"后端端口 {backend_port}",
        status,
        detail,
        None
        if status == PASS
        else "关掉占用进程，或在 .env 里设置 PKOS_PORT 换一个端口（改完前端会自动跟随）",
    )

    status, detail = port_state(frontend_port)
    report.add(
        f"前端开发端口 {frontend_port}",
        # 单端口模式下前端端口用不到，只有 -Dev 模式才需要，所以占用只算警告
        WARN if status == FAIL else PASS,
        detail + "（仅开发模式需要：start.ps1 -Dev 或 ./start.sh --dev）",
        None if status == PASS else "在 .env 里设置 PKOS_FRONTEND_PORT 换一个端口",
    )


def check_migrations(report: Report) -> None:
    """直接读 SQLite，避免依赖 alembic 是否已安装。"""
    versions_dir = PROJECT_ROOT / "migrations" / "versions"
    head = None
    if versions_dir.is_dir():
        revisions = []
        for path in sorted(versions_dir.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
            if match:
                revisions.append(match.group(1))
        head = revisions[-1] if revisions else None

    if head is None:
        report.add("数据库迁移", WARN, "migrations/versions 下没有找到任何迁移脚本")
        return

    data_dir = PROJECT_ROOT / "data"
    db_path = data_dir / "app.db"
    env = PROJECT_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("PKOS_DATA_DIR="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    db_path = Path(value).expanduser() / "app.db"
                break

    if not db_path.exists():
        report.add(
            "数据库迁移",
            WARN,
            f"数据库文件还不存在（{db_path.name}），最新迁移版本 {head}",
            "首次启动时会自动执行迁移，或手动运行：uv run alembic upgrade head",
        )
        return

    import sqlite3

    try:
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error as exc:
        report.add(
            "数据库迁移",
            FAIL,
            f"无法读取 {db_path}：{type(exc).__name__}: {exc}",
            "数据库文件可能已损坏。先备份，再考虑删除后重新迁移（会丢失数据）。",
        )
        return

    current = row[0] if row else None
    if current == head:
        report.add("数据库迁移", PASS, f"已在最新版本 {current}")
    else:
        report.add(
            "数据库迁移",
            WARN,
            f"当前 {current or '（空）'}，最新 {head}",
            "执行：uv run alembic upgrade head",
        )


def check_remote(report: Report) -> None:
    code, output = run(["git", "remote", "-v"], timeout=20)
    if code != 0:
        report.add("Git 远程仓库", WARN, "无法读取 git remote")
        return
    if output.strip():
        report.add("Git 远程仓库", PASS, output.splitlines()[0])
    else:
        report.add(
            "Git 远程仓库",
            WARN,
            "未配置远程仓库，提交只保存在本机",
            "如需异地备份，自行添加远程仓库：git remote add origin <地址>",
        )


def build_report() -> Report:
    report = Report()
    check_platform(report)
    check_python(report)
    check_uv(report)
    check_node(report)
    check_backend_deps(report)
    check_frontend_deps(report)
    check_frontend_build(report)
    check_env_file(report)
    check_ai_config(report)
    check_env_var_shadowing(report)
    check_data_dir(report)
    check_ports(report)
    check_migrations(report)
    check_remote(report)
    return report


def print_report(report: Report) -> None:
    width = max(len(r.name) for r in report.results)
    print("=" * 72)
    print("Personal Knowledge OS —— 环境自检")
    print(f"项目目录：{PROJECT_ROOT}")
    print("=" * 72)
    for result in report.results:
        print(f"{MARKER[result.status]}  {result.name.ljust(width)}  {result.detail}")
        if result.hint:
            print(f"{'':{width + 6}}↳ 怎么办：{result.hint}")

    counts = report.to_dict()["counts"]
    print("-" * 72)
    print(
        f"合计：{counts[PASS]} 项通过，{counts[WARN]} 项警告，{counts[FAIL]} 项失败"
    )
    if report.failed:
        print("存在必须先解决的问题（[XX]），按上面的“怎么办”处理后重新自检。")
    elif counts[WARN]:
        print("没有阻塞问题，警告项不影响启动，可以按需处理。")
    else:
        print("环境完全就绪。")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="Personal Knowledge OS 环境自检")
    parser.add_argument("--json", action="store_true", help="输出 JSON，供脚本调用")
    args = parser.parse_args()

    report = build_report()
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
