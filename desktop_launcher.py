"""桌面入口：双击图标 → 后台起服务 → 弹出独立应用窗口 → 关窗即停。

用 pythonw.exe 跑，所以全程没有黑窗口。服务是 .venv 里的 uvicorn，输出进
logs/server.log；窗口是 Chrome/Edge 的 --app 模式，配一份**独立的浏览器配置**——
独立配置才让这个窗口进程可等待：共用日常配置时，启动会被转交给已经在跑的浏览器，
Popen 立刻返回，就没有东西可以盯着「用户关窗了」这件事。

失败一律用消息框把原因和下一步说清楚，细节追加到 logs/launcher.log。
只管 Windows；macOS / Linux 继续用 ./start.sh。
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

APP_TITLE = "个人知识库"
HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / ".env"
VENV_PYTHON = HERE / ".venv" / "Scripts" / "python.exe"
FRONTEND_INDEX = HERE / "frontend" / "dist" / "index.html"
LOG_DIR = HERE / "logs"
LAUNCHER_LOG = LOG_DIR / "launcher.log"
SERVER_LOG = LOG_DIR / "server.log"
LOCK_FILE = LOG_DIR / "launcher.lock"

# 浏览器配置是几十兆不断变化的缓存，放本地 AppData 而不是项目目录里：
# 项目目录可能被同步盘或备份盯着，缓存一直变就会没完没了地触发同步。
WINDOW_PROFILE = (
    Path(os.environ.get("LOCALAPPDATA") or HERE) / "pkos-desktop-app" / "browser-profile"
)

CREATE_NO_WINDOW = 0x08000000
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_TOPMOST = 0x40000
LOG_KEEP_BYTES = 256 * 1024
LOG_TRIM_AT_BYTES = 512 * 1024
# 比纯对话服务宽裕：这里启动前还要跑迁移，首次启动还要做一次自动备份。
SERVER_START_TIMEOUT = 60.0
MIGRATION_TIMEOUT = 180.0


def parse_env(text: str) -> dict[str, str]:
    """.env 的极简解析，只服务启动器自己要用的两个键。

    其余配置不在这儿读：服务自己会通过 backend/config.py 读同一份文件，
    两处都解析全套规则只会多一个不一致的来源。
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def read_endpoint() -> tuple[str, int]:
    """监听地址与端口，默认值和 backend/config.py 保持一致。

    .env 读不出来或值不合法时不在这里报错：让服务自己去报，
    backend/config.py 对「GBK 另存为」「智能引号」这些事故有专门的中文提示。
    """
    host, port = "127.0.0.1", 8000
    try:
        values = parse_env(ENV_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return host, port
    if values.get("PKOS_HOST"):
        host = values["PKOS_HOST"]
    if values.get("PKOS_PORT"):
        try:
            port = int(values["PKOS_PORT"])
        except ValueError:
            pass
    return host, port


def window_host(host: str) -> str:
    # 通配符绑定也回答 loopback，但绑定到某块局域网网卡就绝不回答 loopback，
    # 所以窗口要打开的地址必须跟着服务实际的监听方式走。
    return "127.0.0.1" if host in ("", "0.0.0.0", "::") else host


def log(message: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with open(LAUNCHER_LOG, "a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def trim_old_logs() -> None:
    for path in (LAUNCHER_LOG, SERVER_LOG):
        try:
            if path.stat().st_size > LOG_TRIM_AT_BYTES:
                path.write_bytes(path.read_bytes()[-LOG_KEEP_BYTES:])
        except OSError:
            pass


def notify(text: str, title: str, flags: int = MB_ICONWARNING) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, title, flags | MB_TOPMOST)


def app_is_ready(url: str, timeout: float = 1.0) -> bool:
    """/api/health 回了 200 且确实是本应用，才算就绪。

    只看端口通不通不够：端口上可能蹲着别的程序，那样窗口会打开一个陌生页面，
    而且启动器会误判成「服务已经在跑」，关窗时也不会去停它。
    """
    try:
        with urllib.request.urlopen(f"{url}api/health", timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and "app_version" in payload


def wait_until_ready(
    url: str, deadline_seconds: float, process: subprocess.Popen | None = None
) -> bool:
    """轮询到服务就绪；自己起的那个子进程要是已经死了，就提前放弃。

    没有这个进程检查，一个起不来的服务（比如 .env 里的端口不合法）
    会让这次点击干等满整个超时时间。
    """
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if app_is_ready(url):
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.25)
    return False


def claim_lifecycle() -> object | None:
    """独占锁住一个字节，两次点击不会抢同一个服务。

    句柄必须由调用方一直拿着：Windows 在句柄被回收或进程死掉时就释放锁。
    """
    import msvcrt

    LOG_DIR.mkdir(exist_ok=True)
    handle = open(LOCK_FILE, "r+b") if LOCK_FILE.exists() else open(LOCK_FILE, "w+b")
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def child_env() -> dict[str, str]:
    # 中文 Windows 的控制台是 GBK，子进程统一 UTF-8，日志里的中文才不会变乱码。
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_migrations() -> tuple[bool, str]:
    """先把数据库结构升到最新。失败就别起服务了——起来了也是满屏报错。"""
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-m", "alembic", "upgrade", "head"],
            cwd=str(HERE),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=MIGRATION_TIMEOUT,
            env=child_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def start_server(host: str, port: int) -> subprocess.Popen:
    # 用 .venv 的解释器而不是再套一层 `uv run`：少一个包装进程。
    # .venv 的 python.exe 本身是 uv 的跳板，会再派生一个真正的解释器子进程；
    # 实测 terminate 跳板会连带带走那个子进程，所以这里不需要 taskkill /T。
    LOG_DIR.mkdir(exist_ok=True)
    with open(SERVER_LOG, "ab") as output:
        return subprocess.Popen(
            [
                str(VENV_PYTHON), "-m", "uvicorn", "backend.main:app",
                "--host", host, "--port", str(port),
            ],
            cwd=str(HERE),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
            env=child_env(),
        )


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    # 直接 terminate 是可以的：应用的 lifespan 没有关闭时要做的收尾，
    # SQLite 又是 WAL 模式，进程被硬停也能在下次打开时自行恢复。
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def server_log_tail(limit: int = 900) -> str:
    try:
        text = SERVER_LOG.read_bytes()[-4000:].decode("utf-8", "replace")
    except OSError:
        return "日志文件读不到。"
    return text.strip()[-limit:] or "（服务没有留下任何输出）"


def browser_candidates() -> list[str]:
    install_roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    relative = (
        os.path.join("Google", "Chrome", "Application", "chrome.exe"),
        os.path.join("Microsoft", "Edge", "Application", "msedge.exe"),
    )
    found: list[str] = []
    for item in relative:
        for root in install_roots:
            if not root:
                continue
            path = Path(root) / item
            if path.is_file() and str(path) not in found:
                found.append(str(path))
    return found


def open_app_window(url: str) -> tuple[subprocess.Popen | None, str | None]:
    """向浏览器要一个独立窗口，并且留住进程句柄。"""
    # 每次启动给地址加一个时间戳查询参数：app 用的是独立浏览器配置，里面可能缓存着
    # **旧版 index.html**（旧版还没加 no-cache 时被启发式存下）。不加参数时浏览器直接
    # 吃那份旧缓存、不重新校验，于是永远去拉已被重建删掉的旧哈希 chunk → 详情页 404
    # 死屏（D-079）。换个 ?v= 让每次启动都是一个没缓存过的新地址，强制取新壳；
    # 其余资源是内容哈希命名，本来就该按新名取，不受影响。
    app_url = f"{url}?v={int(time.time())}"
    for browser in browser_candidates():
        arguments = [
            browser,
            f"--app={app_url}",
            f"--user-data-dir={WINDOW_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--window-size=1440,900",
        ]
        try:
            return subprocess.Popen(arguments, cwd=str(HERE)), browser
        except OSError:
            log(f"无法用 {browser} 打开应用窗口，继续试下一个")
    return None, None


def main() -> int:
    trim_old_logs()
    host, port = read_endpoint()
    url = f"http://{window_host(host)}:{port}/"
    log(f"点击图标，目标地址 {url}")

    if not VENV_PYTHON.is_file():
        notify(
            "本项目还没安装好依赖，服务起不来。\n\n"
            f"请在项目目录运行一次安装脚本：\n    python scripts\\setup.py\n\n"
            f"项目目录：{HERE}",
            f"{APP_TITLE} 还没安装",
        )
        return 1
    if not FRONTEND_INDEX.is_file():
        notify(
            "前端页面还没构建，现在打开只会看到一段接口提示文字。\n\n"
            "请在项目目录运行一次（会自动构建，只影响首次启动）：\n"
            "    powershell -ExecutionPolicy Bypass -File .\\start.ps1\n\n"
            "构建完成后再双击桌面图标。",
            f"{APP_TITLE} 前端还没构建",
        )
        return 1

    lock = claim_lifecycle()
    owned = None
    if lock is None:
        log("已有启动器在管这个服务，本次只负责打开窗口")
        if not wait_until_ready(url, SERVER_START_TIMEOUT):
            notify(
                "上一次点击还在启动服务，这一次没能等到它就绪。\n\n"
                "请再点一次桌面图标；如果反复这样，请把 logs\\launcher.log 发给我。",
                f"{APP_TITLE} 暂时没起来",
            )
            return 1
    elif app_is_ready(url):
        # 可能是手动 .\start.ps1 起的，也可能是上一个启动器没来得及收尾。
        # 复用它胜过抢端口，但**不是我们起的进程，关窗时就不能去停它**。
        log("端口上已经有本应用在跑，直接复用；关窗口时不会去动那个进程")
    else:
        migrated, detail = run_migrations()
        if not migrated:
            log("数据库迁移失败，未启动服务")
            notify(
                "数据库结构升级失败，服务没有启动。下面是它自己给出的原因：\n\n"
                f"{detail.strip()[-900:]}\n\n"
                "可以在项目目录运行 .\\start.ps1 看完整自检信息。",
                f"{APP_TITLE} 启动失败",
            )
            return 1
        owned = start_server(host, port)
        log(f"服务进程 pid={owned.pid}")
        if not wait_until_ready(url, SERVER_START_TIMEOUT, owned):
            stop_server(owned)
            log("服务启动失败，已回收进程")
            notify(
                "服务没能开始监听，下面是它自己给出的原因和解决办法：\n\n"
                f"{server_log_tail()}\n\n"
                f"完整日志：{SERVER_LOG}",
                f"{APP_TITLE} 启动失败",
            )
            return 1

    window, browser = open_app_window(url)
    if window is None:
        webbrowser.open(url)
        log("没找到 Chrome 或 Edge，已退回普通浏览器标签页")
        notify(
            f"这台电脑上找不到 Chrome 或 Edge，已用默认浏览器的普通标签页打开 {url}。\n\n"
            "这种方式没法在关标签页时自动停止服务。想停掉它，在 PowerShell 里运行 "
            "README「桌面图标」一节给出的那条停止命令。",
            f"{APP_TITLE} 已用普通标签页打开",
            MB_ICONINFORMATION,
        )
        return 0

    log(f"应用窗口 pid={window.pid}（{browser}）")
    if owned is None:
        # 要么服务归另一个启动器管，要么这次启动被转交给了已经开着的应用窗口——
        # 两种情况下真正的主人都在别处，这次点击的进程退出时什么都不该改。
        return 0

    window.wait()
    log("应用窗口已关闭，停止服务")
    stop_server(owned)
    return 0


if __name__ == "__main__":
    # 只捕 Exception：sys.exit 抛的是 SystemExit，在这儿吞掉它会让每次正常退出
    # 都弹一个错误框，而且进程会停在那个已被关掉的对话框上不退。
    try:
        exit_code = main()
    except KeyboardInterrupt:
        exit_code = 130
    except Exception:
        detail = traceback.format_exc()
        try:
            log(detail)
            notify(
                f"启动器自己出了错，详情见 logs\\launcher.log：\n\n{detail[-900:]}",
                f"{APP_TITLE} 启动失败",
            )
        except Exception:
            pass
        exit_code = 1
    sys.exit(exit_code)