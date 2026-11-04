"""备份接口：列出现有备份、手动做一份新的。

启动自动备份在 `main.py` 的 lifespan 里（D-068），这里只有
用户主动触发的两条。备份是本机文件操作，不走 AI、不发网络请求。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.config import Settings, get_settings
from backend.schemas.backup_api import BackupListView, BackupView
from backend.services.backup_service import (
    KEEP_COUNT,
    BackupSourceMissing,
    create_backup,
    list_backups,
)

router = APIRouter(prefix="/api/backups", tags=["backups"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=BackupListView)
def get_backups(settings: SettingsDep) -> BackupListView:
    return BackupListView(
        backups=[BackupView.model_validate(b) for b in list_backups(settings)],
        keep_count=KEEP_COUNT,
    )


@router.post("", response_model=BackupView, status_code=status.HTTP_201_CREATED)
def post_backup(settings: SettingsDep) -> BackupView:
    try:
        info = create_backup(settings)
    except BackupSourceMissing as exc:
        # 库文件不存在说明还没跑迁移——这不是"服务出错"，是"还没初始化"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return BackupView.model_validate(info)
