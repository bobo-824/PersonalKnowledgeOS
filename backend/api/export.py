"""导出接口：整库 Markdown / JSON 下载（D-069）。

GET + 附件响应头，浏览器直接触发下载。导出是纯本机读库操作，
不走 AI；正文会进导出文件，但那是用户自己点下载拿到的本机文件。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from typing import Annotated

from backend.db.session import get_session
from backend.services.export_service import export_json, export_markdown

router = APIRouter(prefix="/api/export", tags=["export"])

SessionDep = Annotated[Session, Depends(get_session)]

_MARKDOWN_MEDIA = "text/markdown; charset=utf-8"
_JSON_MEDIA = "application/json"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _attachment(filename: str) -> str:
    # 文件名只有 ASCII（时间戳），不需要 RFC 5987 的 filename* 兜底
    return f'attachment; filename="{filename}"'


@router.get("/markdown")
def get_markdown_export(session: SessionDep) -> Response:
    content = export_markdown(session).encode("utf-8")
    return Response(
        content=content,
        media_type=_MARKDOWN_MEDIA,
        headers={"Content-Disposition": _attachment(f"pkos-export-{_stamp()}.md")},
    )


@router.get("/json")
def get_json_export(session: SessionDep) -> Response:
    return Response(
        content=export_json(session),
        media_type=_JSON_MEDIA,
        headers={"Content-Disposition": _attachment(f"pkos-export-{_stamp()}.json")},
    )
