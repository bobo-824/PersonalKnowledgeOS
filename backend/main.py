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
from pydantic import ValidationError

from backend.api.ai import router as ai_router
from backend.api.health import router as health_router
from backend.api.knowledge import router as knowledge_router
from backend.api.settings import router as settings_router
from backend.config import ConfigFileError, Settings, get_settings
from backend.db.models import (
    ERROR_KINDS,
    ERROR_KIND_AUTH_REJECTED,
    ERROR_KIND_BAD_REQUEST,
    ERROR_KIND_SCHEMA_INVALID,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_UNAVAILABLE,
)
from backend.db.session import dispose_engine
from backend.services.ai.errors import AiError
from backend.services.ai.factory import AiNotConfigured
from backend.services.ai_service import (
    ConversationNotFound,
    EmptyDraftError,
    ReviewItemNotFound,
    ReviewNotFound,
)
from backend.services.knowledge_service import KnowledgeNotFound

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.0"

# AI 失败 → HTTP 状态码。按 `error_kind` 查而不是按异常类 isinstance 判断，
# 是因为这一列就是失败的**唯一对外词汇**（`ai_messages.error_kind` 存的是它）。
# 有专门的测试断言这张表恰好覆盖 ERROR_KINDS，不多不少——
# 少一行的后果是那一类失败冒成 500，把"该改 key"说成"服务出错了"。
AI_ERROR_STATUS: dict[str, int] = {
    # 上游连不上 / 拒绝 / 给了不能用的内容：都是"网关后面的服务没把事办成"
    ERROR_KIND_UNAVAILABLE: 502,
    ERROR_KIND_AUTH_REJECTED: 502,
    ERROR_KIND_BAD_REQUEST: 502,
    ERROR_KIND_SCHEMA_INVALID: 502,
    # 超时单独一档，因为处置不同：这一类重试往往有效
    ERROR_KIND_TIMEOUT: 504,
}


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

    @app.exception_handler(ConfigFileError)
    async def _broken_config(_request: Request, exc: ConfigFileError) -> JSONResponse:
        """配置文件坏了确实是 500，但**默认 500 不带任何信息**。

        启动阶段这个异常会让进程直接拒绝启动（那时用户看的是控制台输出）；
        运行中才会走到这里，比如手改了 .env 里某个值、又恰好触发了缓存重读。
        给一句"哪个文件、大概哪里、怎么办"比 `Internal Server Error` 有用得多。
        """
        return JSONResponse({"detail": str(exc)}, status_code=500)

    _register_ai_error_handlers(app)

    app.include_router(health_router)
    app.include_router(ai_router)
    app.include_router(settings_router)
    # 必须在 _mount_frontend 之前：SPA 回落注册了 /{full_path:path} 这个
    # 通配路由，FastAPI 按注册顺序匹配，后注册的业务路由会被它抢走。
    app.include_router(knowledge_router)
    _mount_frontend(app, settings)
    return app


def _register_ai_error_handlers(app: FastAPI) -> None:
    """AI 旁路的错误 → HTTP 状态码。

    ## ⚠️ 只管**非流式**端点

    `POST /api/ai/conversations/{id}/messages` 的失败不走这里——
    那时响应头（200 + text/event-stream）已经发出去了，
    改不了状态码，只能在流里发 error 事件（见 `api/ai.py` 的 `_sse`）。
    一个失败两种出口不是风格不统一，是 HTTP 流式响应的物理限制。

    ## 为什么 `detail` 只放给用户看的那句话

    `exc.detail` 里可能有 base_url、状态码、字段名。那些是排查用的，
    而排查信息已经进了日志。响应体按**最小必要**给（C5/C6），
    多带一个字段就多一处将来会带错东西的地方。
    """

    async def _ai_not_found(_request: Request, exc: LookupError) -> JSONResponse:
        # 三种"找不到"给同一个状态码，但文案要分开：
        # 找不到会话和找不到审查条目，用户下一步做的事完全不同。
        return JSONResponse({"detail": _not_found_message(exc)}, status_code=404)

    # 一个函数注册给三个异常类。不叠三层 @app.exception_handler 装饰器：
    # 那种写法依赖"装饰器返回被装饰函数"这个实现细节，而它并不是公开契约。
    for not_found_error in (ConversationNotFound, ReviewNotFound, ReviewItemNotFound):
        app.add_exception_handler(not_found_error, _ai_not_found)

    @app.exception_handler(EmptyDraftError)
    async def _empty_draft(_request: Request, exc: EmptyDraftError) -> JSONResponse:
        # 422 而不是 400：这是"内容不足以成为一条知识"，
        # 前端要把它显示成"请自己写一句理解"，而不是"请求格式错误"。
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(AiNotConfigured)
    async def _ai_not_configured(_request: Request, exc: AiNotConfigured) -> JSONResponse:
        # 409：本地少填了两项，和"上游服务出问题了"是两件不同的事。
        # 混成 500 会让用户以为程序坏了，其实是他还没去设置页。
        return JSONResponse(
            {
                "detail": exc.user_message,
                "error_kind": "not_configured",
                "missing": list(exc.missing),
            },
            status_code=409,
        )

    @app.exception_handler(AiError)
    async def _ai_failure(request: Request, exc: AiError) -> JSONResponse:
        status_code = AI_ERROR_STATUS.get(exc.error_kind, 502)
        # 日志只记 kind 和路径。异常对象本身按设计不含原文/key/正文（C6），
        # 但 logger 不该依赖那个设计——这里就一个 kind 一条路径。
        logger.warning("AI 调用失败：%s %s -> %s", request.url.path, exc.error_kind, status_code)
        return JSONResponse(
            {"detail": exc.user_message, "error_kind": exc.error_kind},
            status_code=status_code,
        )

    @app.exception_handler(ValidationError)
    async def _domain_validation(_request: Request, exc: ValidationError) -> JSONResponse:
        """service 层里 `model_validate` 失败 → 422，而不是 500。

        这条路是给"草稿不合知识的落库规则"准备的：确认草稿时
        `KnowledgeCreate.model_validate` 会拒绝空标题、只有标点符号的理解。
        那都是**用户能自己修好**的事，报 500 等于告诉他程序坏了。

        ⚠️ `exc.errors()` 里的 `input` 装着实际收到的值，也就是用户的正文，
        所以响应和日志都**只取字段路径**（和 `services/ai/parsing.py` 同一套纪律）。
        """
        fields = sorted({".".join(str(part) for part in (e.get("loc") or ())) for e in exc.errors()})
        return JSONResponse(
            {
                "detail": "内容还不足以保存，请按提示补齐后再试一次。",
                "invalid_fields": fields,
            },
            status_code=422,
        )


def _not_found_message(exc: LookupError) -> str:
    """把三种"找不到"翻译成人能看懂的一句。id 不回显，前端本来就有它。"""
    if isinstance(exc, ConversationNotFound):
        return "找不到这个对话，它可能已经被清空了。"
    if isinstance(exc, ReviewItemNotFound):
        return "这条审查里没有那个条目。"
    return "找不到这份审查记录。"


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
