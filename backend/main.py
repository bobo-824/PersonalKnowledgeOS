"""FastAPI 应用入口。

单端口模式：同一进程既提供 /api，也托管 frontend/dist 静态文件，
浏览器只需访问 http://127.0.0.1:8000（Blueprint 6.4）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.health import router as health_router
from backend.api.knowledge import router as knowledge_router
from backend.config import Settings, get_settings
from backend.db.session import dispose_engine
from backend.services.knowledge_service import KnowledgeNotFound

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # 关闭连接池：SQLite 会在最后一个连接关闭时把 WAL 落盘，
    # 避免残留 -wal 文件让手动备份拿到不完整的数据。
    dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    app = FastAPI(
        title="Personal Knowledge OS",
        version=APP_VERSION,
        description="个人知识管理与长期学习系统（本地优先）",
        lifespan=_lifespan,
    )

    # 开发模式下前端跑在 Vite 端口，属于跨域；单端口模式同源，中间件不影响。
    # 只放行本机 Vite 源，不做 allow_origins=["*"]。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{settings.frontend_port}",
            f"http://localhost:{settings.frontend_port}",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # service 层用异常表达"不存在"，在这里统一映射成 404，
    # 免得每个路由函数都写一遍 try/except。
    @app.exception_handler(KnowledgeNotFound)
    async def _knowledge_not_found(
        _request: Request, exc: KnowledgeNotFound
    ) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    app.include_router(health_router)
    # 必须在 _mount_frontend 之前：SPA 回落注册了 /{full_path:path} 这个
    # 通配路由，FastAPI 按注册顺序匹配，后注册的业务路由会被它抢走。
    app.include_router(knowledge_router)
    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist.resolve()
    index = dist / "index.html"

    if not index.exists():
        if not settings.allow_missing_frontend:
            raise RuntimeError(
                f"前端未构建：找不到 {index}。请先运行 npm run build，"
                "或在 .env 中设置 PKOS_ALLOW_MISSING_FRONTEND=true。"
            )
        logger.warning("前端未构建（%s 不存在），仅提供 API", index)

        @app.get("/", include_in_schema=False)
        def _frontend_missing() -> JSONResponse:
            return JSONResponse(
                {
                    "message": "后端已启动，但前端尚未构建。",
                    "hint": "在项目根目录运行 scripts/setup.py，或先执行 "
                    "cd frontend && npm ci && npm run build",
                    "api_docs": "/docs",
                    "health": "/api/health",
                }
            )

        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        # /api/* 未匹配到具体路由时应返回 JSON 404，而不是把 index.html 当响应
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        candidate = (dist / full_path).resolve()
        # 防目录穿越：解析后的真实路径必须仍在 dist 内
        if not candidate.is_relative_to(dist) or not candidate.is_file():
            return FileResponse(index)
        return FileResponse(candidate)


app = create_app()
