"""读写 AI 配置：把设置页填的值写进 `.env`（阶段 2 开工前用户选的"方案 A"）。

## 为什么是"行级替换"，不是"整份重写"

`.env` 里除了 `PKOS_AI_*` 还有端口、数据目录、日志级别，而且**这个文件是用户自己的**：
他可能加了注释、调整过顺序、留了自己的备忘。只替换那 6 行、其余原样保留，
是改动最小的做法。整份重排会让用户下次打开文件找不到自己写的注释。

## 三条不能省的写法

1. **先备份再改**。这是本项目唯一会覆盖用户手工编辑的文件的操作。
2. **原子替换**：先写同目录临时文件、`fsync`，再 `os.replace`。
   Windows 上同卷 `os.replace` 是原子的，不会出现"读到空了一半的 .env"。
3. **备份落在 `data/` 下面**。备份含密钥，`.gitignore` 里有 `data/`；
   根目录那种 `.env.bak` 虽然恰好也被 `.env.*` 匹配到，
   但含密钥的文件不该依赖"恰好被忽略"。

## 改完需不需要重启

不需要。Provider 是每次请求按当前 settings 新建的（见 `services/ai/factory.py`），
所以写完 `.env` 后清掉 `get_settings` 的缓存，下一个请求就用新值。
但**真实环境变量优先于 .env**（pydantic-settings 的规则），
所以如果 `PKOS_AI_BASE_URL` 是以进程环境变量的形式存在的（start.ps1 里 set 过、
或系统环境变量里配过），写文件不生效——这个必须如实告诉用户，
于是有了 `AiConfigView.shadowed_keys`。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from backend.config import Settings, get_settings, reset_settings_cache
from backend.schemas.ai_api import AiConfigUpdateResult, AiConfigView
from backend.services.ai.factory import is_configured

logger = logging.getLogger(__name__)

# settings 字段名 → .env 里的环境变量名。
# 用能照着改的那个名字和用户说话：他去打开的是 .env 文件，不是 Python 源码。
AI_ENV_KEYS: dict[str, str] = {
    "provider": "PKOS_AI_PROVIDER",
    "base_url": "PKOS_AI_BASE_URL",
    "api_key": "PKOS_AI_API_KEY",
    "model": "PKOS_AI_MODEL",
    "timeout_seconds": "PKOS_AI_TIMEOUT_SECONDS",
    "json_mode": "PKOS_AI_JSON_MODE",
}

# 备份保留份数。每次保存设置留一份，不设上限会把密钥的旧副本越攒越多，
# 而每一个副本都是"泄露面"。10 份够排查"昨天还好的"这种问题。
BACKUP_KEEP = 10

_BACKUP_SUFFIX = ".env"

# `KEY=value` / `export KEY=value` 两种写法都认，注释行不认。
_LINE_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


class DotenvUnreadable(Exception):
    """现有 `.env` 读不出文本。**不做任何写入**就抛出来。

    真实场景：Notepad 把带中文注释的文件"另存为"时默认用 ANSI(GBK)，
    而 `.env.example` 里全是中文注释，所以这个分支不是假想的。
    此时若猜一个编码读进来、再按 UTF-8 写回去，用户的注释会整片变成乱码，
    而且是**默默坏掉**——宁可停下来让人处理。
    """

    def __init__(self, path: Path, encoding_error: str) -> None:
        self.path = path
        self.encoding_error = encoding_error
        super().__init__(
            f"{path} 不是 UTF-8 文本（{encoding_error}）。"
            "为了不弄坏里面的中文注释，我没有改写这个文件。"
            "请用编辑器把它另存为 UTF-8 后重试，或者手动把这几行改掉。"
        )


# ================================================================ 读


def get_ai_config(settings: Settings) -> AiConfigView:
    """当前的 AI 配置。**永不含完整密钥**（Blueprint 9.1 / 硬约束 C6）。"""
    return AiConfigView(
        provider=settings.ai_provider,
        base_url=settings.ai_base_url,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        json_mode=settings.ai_json_mode,
        api_key_set=bool(settings.ai_api_key),
        api_key_hint=_mask_key(settings.ai_api_key),
        configured=is_configured(settings),
        hint=_base_url_hint(settings.ai_base_url),
        shadowed_keys=_shadowed_keys(tuple(AI_ENV_KEYS.values())),
    )


# ================================================================ 写


def update_ai_config(
    settings: Settings,
    changes: Mapping[str, object],
    *,
    env_path: Path | None = None,
    backup_dir: Path | None = None,
) -> AiConfigUpdateResult:
    """把 `changes` 里的项写进 `.env`，其余内容逐字保留。

    `changes` 的键是 `AI_ENV_KEYS` 的字段名（provider/base_url/...），
    **调用方只传用户真正改过的项**——这里不再判断"要不要改"，
    传了就写。所以空 dict 会原样返回、不碰文件。

    `env_path` / `backup_dir` 只给测试用（生产走 settings 里的路径）。
    """
    target = env_path or settings.dotenv_path
    backups = backup_dir or settings.dotenv_backup_dir

    if not changes:
        return AiConfigUpdateResult(
            config=get_ai_config(settings),
            changed_keys=[],
            backup_file=None,
            takes_effect_now=True,
            message="没有需要保存的修改，配置文件未改动。",
        )

    updates = {AI_ENV_KEYS[field]: render_value(value) for field, value in changes.items()}
    shadowed = _shadowed_keys(tuple(updates))

    original = _read_text(target)
    new_text, changed = merge_ai_lines(original, updates)

    backup_file: str | None = None
    if original is not None:
        backup_file = str(_backup(target, backups))
    _write_atomic(target, new_text)
    _rotate_backups(backups)

    # 让下一个请求读到新值：Provider 每次请求新建，所以不需要重启进程。
    reset_settings_cache()

    logger.info("已写入 %s：%s", target.name, ", ".join(changed))
    return AiConfigUpdateResult(
        config=get_ai_config(get_settings()),
        changed_keys=list(changed),
        backup_file=backup_file,
        takes_effect_now=not shadowed,
        message=_result_message(changed, shadowed, original is None),
    )


def merge_ai_lines(text: str | None, updates: Mapping[str, str]) -> tuple[str, tuple[str, ...]]:
    """行级替换：命中 `updates` 的行换值，其余行原样保留，没出现过的键追加到末尾。

    ⚠️ **同名键出现多次时全部换成新值**。dotenv 的规则是后出现的覆盖前面的，
    只改第一条的话，文件里那条陈旧的重复行会赢，界面却显示"已保存新值"——
    这种"看着改成功了其实没改"最难排查。
    刻意**不删**多余的行：删行是在动用户写的东西，
    而留一行的代价只是文件里多一条冗余设置。

    换行符跟随原文件（原文件用 CRLF 就继续用 CRLF）：
    `splitlines()` + `"\n".join()` 会把整个文件的行尾都换掉，
    那等于"只改 6 行"却重写了用户的全部文件，diff 也没法看。
    """
    lines = text.splitlines() if text else []
    newline = "\r\n" if text and "\r\n" in text else "\n"
    written: set[str] = set()
    merged: list[str] = []

    for line in lines:
        key = _line_key(line)
        if key is not None and key in updates:
            merged.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            merged.append(line)

    changed = [key for key in updates if key in written]
    appended = [key for key in updates if key not in written]
    merged.extend(f"{key}={updates[key]}" for key in appended)

    body = newline.join(merged)
    if body and not body.endswith(newline):
        body += newline
    return body, tuple((*changed, *appended))


# ================================================================ 内部


def _line_key(line: str) -> str | None:
    """这一行是在设置某个键吗？是则返回键名，否则返回 None。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _LINE_PATTERN.match(line)
    return match.group(1) if match else None


def render_value(value: object) -> str:
    """渲染成 dotenv 认的右值。

    字符串一律加双引号并转义：值里可能有空格（备注式路径）、`#`（会被当注释）、
    甚至引号本身。bool 写成 true/false，因为 pydantic-settings 解析 bool
    只认这两个（写 Python 的 True/False 会变成校验错误）。

    ⚠️ 下面两个 isinstance 的**顺序不能换**：Python 里 `True` 就是 `1`，
    先判 int 会把 true 写成 1，下一次启动直接校验失败。
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _read_text(path: Path) -> str | None:
    """读出现有内容；文件不存在返回 None。"""
    if not path.exists():
        return None
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            error = f"{encoding} 解码失败：{exc.reason}（第 {exc.start} 字节起）"
    raise DotenvUnreadable(path, error)


def _write_atomic(path: Path, text: str) -> None:
    """同目录临时文件 + `os.replace`，避免出现半个文件的中间状态。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _backup(path: Path, backup_dir: Path) -> Path:
    """把改写前的内容存一份，返回备份路径。

    备份名带上时间戳和 PID：`Path(".env").stem` 是 `.env`（不是空串），
    所以文件名形如 `.env-20261103T071530Z_1234.bak`，
    和 `_rotate_backups` 的 `.env-*.bak` 匹配规则是一对，改动时两边要一起改。
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{path.stem}-{_timestamp()}_{os.getpid()}.bak"
    target.write_bytes(path.read_bytes())
    return target


def _timestamp() -> str:
    """备份名用的时间戳，**精确到微秒**。

    只到秒的话，同一秒内保存两次会得到同一个文件名，后一次把前一次的备份
    **静默覆盖**掉——备份的存在意义正好被抹掉。测试里连续写 13 次实测到过。
    微秒也够用不着 uuid，而且这个格式仍然按字典序等于按时间排序，
    `_rotate_backups` 靠的就是这一点。
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def _rotate_backups(backup_dir: Path) -> None:
    """只留最新 `BACKUP_KEEP` 份。删的必须是本模块自己写的备份，
    所以按固定后缀过滤，不碰目录里的其他任何东西。"""
    backups = sorted(backup_dir.glob(f"{_BACKUP_SUFFIX}-*.bak"))
    for stale in backups[:-BACKUP_KEEP] if len(backups) > BACKUP_KEEP else []:
        stale.unlink(missing_ok=True)


def _mask_key(api_key: str) -> str | None:
    """只留末 4 位，形如 `…abcd`。

    **为什么留 4 位**：用户常要确认"存进去的到底是哪一个 key"，
    光有"已设置"不够用。末 4 位不足以拼出可用密钥，
    而这个服务默认只听 127.0.0.1（Blueprint 6.4 单端口 + 9.2 不暴露公网）。
    **为什么短 key 干脆不显示**：长度不足 8 时末 4 位占了大半，等于直接泄露。
    """
    if not api_key:
        return None
    if len(api_key) < 8:
        return "…（已设置，内容太短不显示）"
    return f"…{api_key[-4:]}"


def _base_url_hint(base_url: str) -> str | None:
    """把 D-044 那个坑（base_url 少了 `/v1`）提前说出来。

    这不是猜测式校验：`AiBadRequest` 的**高频成因**就是它，
    而在设置页提示一句，比让用户对着"服务端拒绝了这个请求"猜半天划算。
    """
    cleaned = base_url.strip().rstrip("/")
    if not cleaned:
        return None
    if cleaned.endswith("/v1") or "/v1/" in cleaned:
        return None
    return (
        "API 地址通常要填到版本路径为止，例如 https://api.deepseek.com/v1。"
        "现在的地址结尾没有 /v1，如果测试连接报「请求被拒绝」，先补上 /v1 再试。"
    )


def _shadowed_keys(names: tuple[str, ...]) -> list[str]:
    """列出被**真实环境变量**盖住的键名。

    pydantic-settings 里进程环境变量优先于 .env 文件，
    所以这些键写文件不生效，必须让用户知道（否则界面显示新值、请求发旧值）。
    """
    return [name for name in names if name in os.environ]


def _result_message(changed: tuple[str, ...], shadowed: list[str], created: bool) -> str:
    saved = f"已保存 {len(changed)} 项到 .env" + ("（新建了该文件）" if created else "")
    if not shadowed:
        return saved
    names = "、".join(shadowed)
    return (
        f"{saved}。但 {names} 已经由系统环境变量设置过，"
        "而环境变量优先级更高：这些项要改环境变量才生效。"
    )
