"""一键安装脚本。

按顺序完成：环境自检 → 后端依赖 → .env → 数据库迁移 → 前端依赖 → 前端构建。
只依赖标准库，用系统自带的 python 就能跑（此时 .venv 还不存在）。

用法：
    python scripts/setup.py

任何一步失败都会立刻停下并给出处理建议，不会带着半残的环境继续往下走。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

# 子进程统一用 UTF-8，避免中文 Windows 的 GBK 控制台把输出变成乱码
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

TOTAL_STEPS = 6


def heading(step: int, title: str) -> None:
    print()
    print("=" * 72)
    print(f"[{step}/{TOTAL_STEPS}] {title}")
    print("=" * 72, flush=True)


def fail(message: str, hint: str | None = None) -> NoReturn:
    print()
    print(f"[XX] 安装中断：{message}")
    if hint:
        print(f"     怎么办：{hint}")
    print()
    print("修好之后重新运行：python scripts/setup.py")
    print("（这个脚本可以重复运行，已经完成的步骤会自动跳过）")
    sys.exit(1)


def stream(cmd: list[str], cwd: Path | None = None) -> int:
    """运行命令并把输出直接打到屏幕上。安装过程可能几分钟，必须让用户看到进度。"""
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        return subprocess.run(cmd, cwd=cwd or PROJECT_ROOT, env=CHILD_ENV).returncode
    except FileNotFoundError:
        print(f"[XX] 找不到命令：{cmd[0]}")
        return 127


def python_exe() -> str:
    return sys.executable


def step_doctor() -> None:
    heading(1, "环境自检")
    result = subprocess.run(
        [python_exe(), str(SCRIPTS_DIR / "doctor.py")],
        cwd=PROJECT_ROOT,
        env=CHILD_ENV,
        capture_output=True,
    )
    print(result.stdout.decode("utf-8", errors="replace"))
    if result.returncode != 0:
        fail(
            "环境自检发现必须先解决的问题（标记为 [XX] 的项）",
            "按上面每一项的“怎么办”处理后重新运行本脚本",
        )
    if result.stderr.strip():
        print(result.stderr.decode("utf-8", errors="replace"))


def step_backend_deps() -> None:
    heading(2, "安装后端 Python 依赖")
    if shutil.which("uv") is None:
        fail(
            "找不到 uv 命令",
            'PowerShell 执行：powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"，'
            "装完关掉当前窗口重新打开，让 PATH 生效",
        )
    # uv sync 会按 uv.lock 精确复现依赖，并自动创建 .venv
    if stream(["uv", "sync"]) != 0:
        fail("uv sync 失败", "看上面的报错；网络问题可重试，或检查是否需要配置 pip 镜像")

    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        venv_py = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_py.exists():
        fail("uv sync 完成但找不到 .venv 里的 Python", "手动检查 .venv 目录是否生成")
    print(f"[OK] 虚拟环境就绪：{venv_py}")


def step_env_file() -> None:
    heading(3, "准备 .env 配置文件")
    env_path = PROJECT_ROOT / ".env"
    example = PROJECT_ROOT / ".env.example"
    if env_path.exists():
        print(f"[OK] .env 已存在，保持原样不覆盖：{env_path}")
        return
    if not example.exists():
        fail("找不到 .env.example，无法生成 .env", "从版本库恢复该文件：git checkout .env.example")
    shutil.copyfile(example, env_path)
    print("[OK] 已从 .env.example 复制生成 .env")
    print("     现在用的是默认端口和空 AI 配置，阶段 2 需要 AI 时再编辑 .env 填入密钥。")
    print("     .env 已被 .gitignore 忽略，不会提交到版本库。")


def step_migrate() -> None:
    heading(4, "执行数据库迁移")
    # 迁移前提示备份：Blueprint 9.2 要求“数据库迁移前提示备份”
    db_path = PROJECT_ROOT / "data" / "app.db"
    if db_path.exists():
        print(f"[!!] 检测到已有数据库：{db_path}")
        print("     迁移会修改表结构。如果里面有重要数据，建议先复制一份这个文件再往下走。")
        print("     首次安装时数据库是空的，直接继续即可。")
    else:
        print("     数据库还不存在，迁移会自动创建（不会覆盖任何已有数据）。")

    if stream(["uv", "run", "alembic", "upgrade", "head"]) != 0:
        fail(
            "数据库迁移失败",
            "运行 uv run alembic current 看当前版本；"
            "如果数据库是新建的且损坏，可删除 data/app.db 后重跑本脚本",
        )

    check = subprocess.run(
        ["uv", "run", "alembic", "current"],
        cwd=PROJECT_ROOT,
        env=CHILD_ENV,
        capture_output=True,
    )
    print("[OK] 当前迁移版本：" + check.stdout.decode("utf-8", errors="replace").strip())


def step_frontend_deps() -> None:
    heading(5, "安装前端依赖")
    frontend = PROJECT_ROOT / "frontend"
    if shutil.which("npm") is None:
        fail("找不到 npm 命令", "安装 Node.js LTS：https://nodejs.org/")

    # 已有 node_modules 时用 npm ci 会先删掉再按 lockfile 重装，保证版本完全一致
    if stream(["npm", "ci"], cwd=frontend) != 0:
        print("[!!] npm ci 失败，退回尝试 npm install")
        if stream(["npm", "install"], cwd=frontend) != 0:
            fail(
                "前端依赖安装失败",
                "网络问题可重试；或配置镜像：npm config set registry https://registry.npmmirror.com",
            )
    print("[OK] 前端依赖安装完成")


def step_frontend_build() -> None:
    heading(6, "构建前端")
    frontend = PROJECT_ROOT / "frontend"
    if stream(["npm", "run", "build"], cwd=frontend) != 0:
        fail(
            "前端构建失败",
            "先单独跑 cd frontend && npx tsc -b 看类型错误；"
            "只想用开发模式的话可以跳过构建，直接运行 start.ps1 -Dev",
        )
    index = frontend / "dist" / "index.html"
    if not index.exists():
        fail("构建命令成功但没有生成 frontend/dist/index.html", "检查 vite.config.ts 的 outDir 配置")
    size_kb = index.stat().st_size / 1024
    print(f"[OK] 构建产物已生成（index.html {size_kb:.1f} KB）")


def step_done() -> None:
    print()
    print("=" * 72)
    print("安装完成")
    print("=" * 72)
    print(f"启动方式（在项目根目录 {PROJECT_ROOT} 打开 PowerShell）：")
    print()
    print("    .\\start.ps1          正常使用：一个端口跑完整应用")
    print("    .\\start.ps1 -Dev     开发模式：改代码自动刷新")
    print()
    print("启动后浏览器会自动打开 http://127.0.0.1:8000")
    print("=" * 72)


def main() -> int:
    print("=" * 72)
    print("Personal Knowledge OS —— 一键安装")
    print(f"项目目录：{PROJECT_ROOT}")
    print("=" * 72)

    step_doctor()
    step_backend_deps()
    step_env_file()
    step_migrate()
    step_frontend_deps()
    step_frontend_build()
    step_done()
    return 0


if __name__ == "__main__":
    sys.exit(main())
