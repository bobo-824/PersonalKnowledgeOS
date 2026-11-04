"""数据库备份（阶段 5，D-068）。

用 SQLite 的 `VACUUM INTO` 而不是复制文件：WAL 模式下最新数据可能还
没写进 app.db 主文件，直接复制会拿到不完整的库——`VACUUM INTO` 在
同一个连接上读到一个一致的快照，正确处理未落盘的 WAL 内容。

策略（D-068）：启动时自动备份一份 + 设置页手动按钮，目录里只保留
最近 7 份。**没有界面内的"恢复"**：恢复等于整库替换文件，
做成按钮的风险收益完全不成立（D-068），文档教手动替换路径。

备份失败的定位是"提醒，不拦路"：启动自动备份失败只记日志，
应用照常起来——备份坏了不该把人挡在自己的知识库外面。
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.config import Settings

logger = logging.getLogger(__name__)

# 保留份数。多留没用（单人本地库），少留怕连续几天都在坏数据上启动。
KEEP_COUNT = 7

_BACKUP_PREFIX = "pkos-backup-"
_BACKUP_SUFFIX = ".db"


class BackupSourceMissing(RuntimeError):
    """数据库文件还不存在（比如首次启动还没跑迁移）。"""


@dataclass(frozen=True)
class BackupInfo:
    """一份备份的对外描述。`path` 不给前端——前端只需要文件名。"""

    filename: str
    size_bytes: int
    created_at: datetime


def backup_dir(settings: Settings) -> Path:
    directory = settings.backup_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def create_backup(settings: Settings) -> BackupInfo:
    """做一份新备份，然后清理到只留最近 KEEP_COUNT 份。"""
    source = settings.database_path
    if not source.exists():
        raise BackupSourceMissing(f"数据库文件不存在：{source}")

    # VACUUM INTO 的目标文件必须事先不存在。时间戳精确到秒，
    # 启动备份和手动备份可能落在同一秒——撞名就顺延一个序号
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = backup_dir(settings)
    destination = directory / f"{_BACKUP_PREFIX}{stamp}{_BACKUP_SUFFIX}"
    counter = 0
    while destination.exists():
        counter += 1
        destination = directory / f"{_BACKUP_PREFIX}{stamp}-{counter}{_BACKUP_SUFFIX}"

    connection = sqlite3.connect(str(source))
    try:
        connection.execute("VACUUM INTO ?", (str(destination),))
    finally:
        connection.close()

    _prune_old_backups(settings)
    info = _describe(destination)
    logger.info("已创建备份 %s（%d 字节）", info.filename, info.size_bytes)
    return info


def list_backups(settings: Settings) -> list[BackupInfo]:
    """现有备份，新的在前。"""
    return [_describe(path) for path in reversed(_all_backup_files(settings))]


def _backup_sort_key(path: Path) -> tuple[str, int]:
    """从文件名解析出 (时间戳, 撞名序号)。完全确定性，不依赖
    文件系统时间戳的精度——同秒创建的两份也能排对。"""
    stem = path.name[len(_BACKUP_PREFIX) : -len(_BACKUP_SUFFIX)]
    if "-" in stem:
        stamp, _, counter = stem.rpartition("-")
        return stamp, int(counter)
    return stem, 0


def _all_backup_files(settings: Settings) -> list[Path]:
    """全部备份文件，旧的在前。"""
    return sorted(
        backup_dir(settings).glob(f"{_BACKUP_PREFIX}*{_BACKUP_SUFFIX}"),
        key=_backup_sort_key,
    )


def _describe(path: Path) -> BackupInfo:
    return BackupInfo(
        filename=path.name,
        size_bytes=path.stat().st_size,
        created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
    )


def _prune_old_backups(settings: Settings) -> None:
    """按创建时间排序，留下最新的 KEEP_COUNT 份。"""
    backups = _all_backup_files(settings)
    for stale in backups[:-KEEP_COUNT]:
        stale.unlink()
        logger.info("清理过期备份 %s", stale.name)


def auto_backup_on_startup(settings: Settings) -> None:
    """启动时的自动备份。失败只警告，绝不阻塞启动（D-068）。"""
    try:
        create_backup(settings)
    except Exception as exc:  # noqa: BLE001 —— 备份失败不能拦住人进自己的库
        logger.warning("启动自动备份失败（应用照常启动）：%s", exc)


__all__ = [
    "BackupInfo",
    "BackupSourceMissing",
    "KEEP_COUNT",
    "auto_backup_on_startup",
    "backup_dir",
    "create_backup",
    "list_backups",
]
