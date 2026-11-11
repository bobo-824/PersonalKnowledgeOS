"""桌面启动器的单元测试。

只测纯逻辑和编排，不真起服务、不真弹窗：notify / Popen / urlopen 全部替身。
真实链路（双击 → 窗口 → 关窗停服务）在 README 验收记录里有人工实测。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import desktop_launcher as dl


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b'{"app_version": "0.1.0"}'):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeProcess:
    """够用的 Popen 替身：poll/terminate/kill/wait。"""

    def __init__(
        self,
        alive: bool = True,
        dies_on_terminate: bool = True,
        hang: bool = False,
    ):
        self.pid = 4321
        self._alive = alive
        self._dies_on_terminate = dies_on_terminate
        self._hang = hang
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        if self._dies_on_terminate:
            self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout: float | None = None) -> int:
        if self._hang:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        return 0


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把启动器会写会读的所有路径都搬进临时目录，测试不碰真实项目状态。"""
    monkeypatch.setattr(dl, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(dl, "LAUNCHER_LOG", tmp_path / "logs" / "launcher.log")
    monkeypatch.setattr(dl, "SERVER_LOG", tmp_path / "logs" / "server.log")
    monkeypatch.setattr(dl, "LOCK_FILE", tmp_path / "logs" / "launcher.lock")
    monkeypatch.setattr(dl, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(dl, "VENV_PYTHON", tmp_path / "python.exe")
    monkeypatch.setattr(dl, "FRONTEND_INDEX", tmp_path / "index.html")
    return tmp_path


# -------------------------------------------------- .env 与地址


def test_parse_env_skips_comments_blanks_and_quote_wrapping() -> None:
    values = dl.parse_env(
        '# 注释\n\nPKOS_PORT = "8123" \nPKOS_HOST=0.0.0.0\n没有等号的一行\n'
    )
    assert values == {"PKOS_PORT": "8123", "PKOS_HOST": "0.0.0.0"}


def test_read_endpoint_defaults_when_env_missing(sandbox: Path) -> None:
    assert dl.read_endpoint() == ("127.0.0.1", 8000)


def test_read_endpoint_follows_env_file(sandbox: Path) -> None:
    dl.ENV_FILE.write_text("PKOS_HOST=192.168.1.5\nPKOS_PORT=8123\n", encoding="utf-8")
    assert dl.read_endpoint() == ("192.168.1.5", 8123)


def test_read_endpoint_ignores_garbage_port_and_lets_server_report_it(
    sandbox: Path,
) -> None:
    # 端口不合法时启动器不猜：服务自己会用中文报出来，启动器只负责把日志尾巴递给用户。
    dl.ENV_FILE.write_text("PKOS_PORT=八千\n", encoding="utf-8")
    assert dl.read_endpoint() == ("127.0.0.1", 8000)


def test_read_endpoint_survives_gbk_env_file(sandbox: Path) -> None:
    dl.ENV_FILE.write_bytes("PKOS_PORT=8123\n# 中文注释".encode("gbk"))
    assert dl.read_endpoint() == ("127.0.0.1", 8000)


@pytest.mark.parametrize("host", ["", "0.0.0.0", "::"])
def test_wildcard_bind_still_opens_loopback(host: str) -> None:
    assert dl.window_host(host) == "127.0.0.1"


def test_lan_bind_is_not_rewritten_to_loopback() -> None:
    assert dl.window_host("192.168.1.5") == "192.168.1.5"


# -------------------------------------------------- 就绪判定


def test_ready_requires_our_own_health_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    assert dl.app_is_ready("http://127.0.0.1:8000/") is True


def test_foreign_service_on_the_port_is_not_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    # 端口上蹲着别的程序时不能误判成「服务已经在跑」，否则关窗不会停它、窗口也是陌生页面。
    monkeypatch.setattr(
        dl.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body=b'{"hello": "world"}')
    )
    assert dl.app_is_ready("http://127.0.0.1:8000/") is False


def test_non_200_and_broken_body_are_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dl.urllib.request, "urlopen", lambda *a, **k: FakeResponse(status=503)
    )
    assert dl.app_is_ready("http://x/") is False
    monkeypatch.setattr(
        dl.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body=b"<html>")
    )
    assert dl.app_is_ready("http://x/") is False


def test_connection_refused_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*a: object, **k: object) -> FakeResponse:
        raise OSError("connection refused")

    monkeypatch.setattr(dl.urllib.request, "urlopen", refuse)
    assert dl.app_is_ready("http://x/") is False


def test_wait_gives_up_early_once_our_child_died(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl, "app_is_ready", lambda *a, **k: False)
    monkeypatch.setattr(dl.time, "sleep", lambda *_: None)
    dead = FakeProcess(alive=False)
    assert dl.wait_until_ready("http://x/", 30.0, dead) is False


def test_wait_returns_true_when_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dl, "app_is_ready", lambda *a, **k: True)
    assert dl.wait_until_ready("http://x/", 5.0) is True


# -------------------------------------------------- 单实例锁与日志


def test_second_claim_is_refused_until_first_handle_closes(sandbox: Path) -> None:
    first = dl.claim_lifecycle()
    assert first is not None
    assert dl.claim_lifecycle() is None
    first.close()
    again = dl.claim_lifecycle()
    assert again is not None
    again.close()


def test_logs_are_trimmed_instead_of_growing_forever(sandbox: Path) -> None:
    dl.LOG_DIR.mkdir()
    dl.SERVER_LOG.write_bytes(b"x" * (dl.LOG_TRIM_AT_BYTES + 10))
    dl.trim_old_logs()
    assert dl.SERVER_LOG.stat().st_size == dl.LOG_KEEP_BYTES


def test_small_logs_are_left_alone(sandbox: Path) -> None:
    dl.LOG_DIR.mkdir()
    dl.SERVER_LOG.write_bytes(b"hello")
    dl.trim_old_logs()
    assert dl.SERVER_LOG.read_bytes() == b"hello"


# -------------------------------------------------- 停服务


def test_stop_server_is_a_noop_for_an_already_dead_process() -> None:
    process = FakeProcess(alive=False)
    dl.stop_server(process)
    assert process.terminated is False


def test_stop_server_terminates_then_waits() -> None:
    process = FakeProcess()
    dl.stop_server(process)
    assert process.terminated is True
    assert process.killed is False


def test_stop_server_kills_when_terminate_is_ignored() -> None:
    process = FakeProcess(dies_on_terminate=False, hang=True)
    dl.stop_server(process)
    assert process.terminated is True
    assert process.killed is True


# -------------------------------------------------- 浏览器窗口


def test_browser_candidates_dedup_and_skip_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "absent"))
    assert dl.browser_candidates() == [str(chrome)]


def test_app_window_uses_private_profile_and_app_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        calls.append(args)
        return FakeProcess()

    monkeypatch.setattr(dl, "browser_candidates", lambda: ["chrome.exe"])
    monkeypatch.setattr(dl.subprocess, "Popen", fake_popen)
    window, browser = dl.open_app_window("http://127.0.0.1:8000/")
    assert window is not None and browser == "chrome.exe"
    assert "--app=http://127.0.0.1:8000/" in calls[0]
    assert f"--user-data-dir={dl.WINDOW_PROFILE}" in calls[0]


def test_app_window_falls_through_browsers_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*a: object, **k: object) -> FakeProcess:
        raise OSError("cannot start")

    monkeypatch.setattr(dl, "browser_candidates", lambda: ["a.exe", "b.exe"])
    monkeypatch.setattr(dl.subprocess, "Popen", refuse)
    monkeypatch.setattr(dl, "log", lambda *_: None)
    assert dl.open_app_window("http://x/") == (None, None)


# -------------------------------------------------- main 的几条分支


@pytest.fixture
def guarded(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """main 可以跑，但弹窗换成记录，路径都在沙箱里。"""
    boxes: list[tuple[str, str]] = []
    monkeypatch.setattr(dl, "notify", lambda text, title, *a: boxes.append((text, title)))
    dl.VENV_PYTHON.write_bytes(b"")
    dl.FRONTEND_INDEX.write_bytes(b"")
    return boxes


def test_missing_venv_says_run_setup_instead_of_crashing(
    guarded: list, sandbox: Path
) -> None:
    dl.VENV_PYTHON.unlink()
    assert dl.main() == 1
    assert "setup.py" in guarded[0][0]


def test_missing_frontend_build_says_run_start_instead_of_json_page(
    guarded: list, sandbox: Path
) -> None:
    dl.FRONTEND_INDEX.unlink()
    assert dl.main() == 1
    assert "start.ps1" in guarded[0][0]


def test_reused_service_is_never_stopped_on_window_close(
    guarded: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "app_is_ready", lambda *a, **k: True)
    monkeypatch.setattr(dl, "claim_lifecycle", lambda: object())
    window = FakeProcess()
    monkeypatch.setattr(dl, "open_app_window", lambda url: (window, "chrome.exe"))
    stopped: list[object] = []
    monkeypatch.setattr(dl, "stop_server", stopped.append)
    assert dl.main() == 0
    assert stopped == []
    assert window.terminated is False


def test_migration_failure_never_starts_the_server(
    guarded: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "app_is_ready", lambda *a, **k: False)
    monkeypatch.setattr(dl, "claim_lifecycle", lambda: object())
    monkeypatch.setattr(dl, "run_migrations", lambda: (False, "alembic 报错了"))
    started: list[object] = []
    monkeypatch.setattr(dl, "start_server", lambda *a: started.append(a))
    assert dl.main() == 1
    assert started == []
    assert "alembic 报错了" in guarded[0][0]


def test_owner_stops_the_service_it_started_when_window_closes(
    guarded: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "app_is_ready", lambda *a, **k: False)
    monkeypatch.setattr(dl, "claim_lifecycle", lambda: object())
    monkeypatch.setattr(dl, "run_migrations", lambda: (True, ""))
    server = FakeProcess()
    monkeypatch.setattr(dl, "start_server", lambda *a: server)
    monkeypatch.setattr(dl, "wait_until_ready", lambda *a, **k: True)
    window = FakeProcess()
    monkeypatch.setattr(dl, "open_app_window", lambda url: (window, "chrome.exe"))
    assert dl.main() == 0
    assert server.terminated is True


def test_failed_start_recycles_the_child_and_shows_the_log_tail(
    guarded: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "app_is_ready", lambda *a, **k: False)
    monkeypatch.setattr(dl, "claim_lifecycle", lambda: object())
    monkeypatch.setattr(dl, "run_migrations", lambda: (True, ""))
    server = FakeProcess()
    monkeypatch.setattr(dl, "start_server", lambda *a: server)
    monkeypatch.setattr(dl, "wait_until_ready", lambda *a, **k: False)
    dl.LOG_DIR.mkdir(exist_ok=True)
    dl.SERVER_LOG.write_text("端口被占用", encoding="utf-8")
    assert dl.main() == 1
    assert server.terminated is True
    assert "端口被占用" in guarded[0][0]


def test_no_browser_falls_back_to_a_tab_and_says_so(
    guarded: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "app_is_ready", lambda *a, **k: True)
    monkeypatch.setattr(dl, "claim_lifecycle", lambda: object())
    monkeypatch.setattr(dl, "open_app_window", lambda url: (None, None))
    opened: list[str] = []
    monkeypatch.setattr(dl.webbrowser, "open", opened.append)
    assert dl.main() == 0
    assert opened == ["http://127.0.0.1:8000/"]
    assert "普通标签页" in guarded[0][0]


def test_second_click_waits_for_the_other_launcher_then_only_opens_window(
    guarded: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "claim_lifecycle", lambda: None)
    waits: list[float] = []
    monkeypatch.setattr(
        dl, "wait_until_ready", lambda url, seconds, *a: waits.append(seconds) or True
    )
    window = FakeProcess()
    monkeypatch.setattr(dl, "open_app_window", lambda url: (window, "chrome.exe"))
    stopped: list[object] = []
    monkeypatch.setattr(dl, "stop_server", stopped.append)
    assert dl.main() == 0
    assert waits == [dl.SERVER_START_TIMEOUT]
    assert stopped == []


def test_second_click_reports_when_the_other_launcher_never_gets_ready(
    guarded: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dl, "claim_lifecycle", lambda: None)
    monkeypatch.setattr(dl, "wait_until_ready", lambda *a, **k: False)
    assert dl.main() == 1
    assert "再点一次" in guarded[0][0]


# -------------------------------------------------- 子进程环境


def test_child_env_forces_utf8_without_dropping_inherited_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PKOS_AI_MODEL", "some-model")
    env = dl.child_env()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PKOS_AI_MODEL"] == "some-model"


def test_server_log_tail_reads_the_end_and_survives_missing_file(
    sandbox: Path,
) -> None:
    assert "读不到" in dl.server_log_tail()
    dl.LOG_DIR.mkdir()
    dl.SERVER_LOG.write_text("a" * 5000 + "最后一行", encoding="utf-8")
    tail = dl.server_log_tail(limit=20)
    assert tail.endswith("最后一行")
    assert len(tail) <= 20