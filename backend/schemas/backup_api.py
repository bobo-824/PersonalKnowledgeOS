"""备份接口的请求/响应模型。

和 `schemas/ai_api.py` 分文件的理由相同：AI 的 schema 是"AI 输出的边界"，
这里的只是本机文件清单的投影，混在一起只会让人误会它们共享同样的信任级别。
"""

from __future__ import annotations

from datetime import datetime

from backend.schemas.ai_api import _ReadModel


class BackupView(_ReadModel):
    filename: str
    size_bytes: int
    created_at: datetime


class BackupListView(_ReadModel):
    backups: list[BackupView]
    keep_count: int
