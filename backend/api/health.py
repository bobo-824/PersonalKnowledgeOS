"""健康检查：服务状态、数据库连通性、迁移版本、前端构建状态。

刻意不返回任何密钥内容，只返回布尔值（Blueprint 9.1：日志与接口不得泄露 API Key）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.db.session import get_session
from backend.services.ai.factory import is_configured

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """始终返回 200，用 status 字段区分 ok / degraded。

    这样前端自检页能直接渲染详情，而不是拿到一个看不懂的 HTTP 错误。
    """
    schema_version: str | None = None
    reachable = False
    error: str | None = None
    try:
        row = session.execute(text("SELECT version_num FROM alembic_version")).first()
        schema_version = row[0] if row else None
        reachable = True
    except SQLAlchemyError as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "status": "ok" if reachable else "degraded",
        "app_version": "0.1.0",
        "database": {
            "reachable": reachable,
            "schema_version": schema_version,
            "path": str(settings.database_path),
            "error": error,
        },
        # "能不能发请求"的定义只有 factory 一份。
        # 原先这里写的是 bool(ai_api_key)，而 key 对本地 Ollama 是不需要的，
        # 两处判断不一致时前端会显示"未配置"却照样能对话。
        "ai_configured": is_configured(settings),
        "frontend_built": (settings.frontend_dist / "index.html").exists(),
    }
