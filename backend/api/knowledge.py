"""知识单元的 REST 接口。

本层只做三件事：解析请求、调 service、映射 HTTP 状态码。
**不写 SQL，不做业务判断**（见 docs/ARCHITECTURE.md 的分层约定）。

## ⚠️ 路由顺序

FastAPI 按声明顺序匹配。`/domains`、`/search`、`/similar` 这些**静态路径必须
声明在 `/{knowledge_id}` 之前**，否则请求 `/api/knowledge/domains` 会被
`{knowledge_id}` 捕获，把 "domains" 当成一个知识 id 去查，返回 404。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from backend.db.session import get_session
from backend.schemas.knowledge import (
    DomainSuggestion,
    KnowledgeCreate,
    KnowledgePage,
    KnowledgeRead,
    KnowledgeUpdate,
    SearchResult,
)
from backend.services import knowledge_service, search_service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

SessionDep = Annotated[Session, Depends(get_session)]

# 列表接口的状态过滤。"all" 映射成 service 层的 None（不过滤）。
StatusFilter = Literal["active", "archived", "all"]

LimitQuery = Annotated[int, Query(ge=1, le=200, description="每页条数，最多 200")]
OffsetQuery = Annotated[int, Query(ge=0, description="跳过多少条")]


# ===== 静态路径：必须在 /{knowledge_id} 之前 =====


@router.get("/domains", response_model=list[DomainSuggestion])
def list_domains(
    session: SessionDep,
    limit: LimitQuery = 50,
) -> list[DomainSuggestion]:
    """已用过的领域名及条数，供前端 `<datalist>` 自动补全（DECISIONS D-031）。

    这只是**减少**领域名写法分散，不能消除——用户仍可无视提示硬打一个新写法。
    """
    return [
        DomainSuggestion(domain=domain, count=count)
        for domain, count in search_service.suggest_domains(session, limit=limit)
    ]


@router.get("/search", response_model=SearchResult)
def search_knowledge(
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=200, description="搜索词")],
    status_filter: Annotated[StatusFilter, Query(alias="status")] = "active",
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> SearchResult:
    """全文检索。

    ≥3 个字符走 FTS5 trigram，1~2 个字符走 LIKE 兜底并查权威表
    （中文的 2 字词如「梯度」极其常见，而 trigram 要求至少 3 字符）。
    响应里的 `strategy` 字段会说明实际走了哪条路径。见 DECISIONS D-007。
    """
    return search_service.search(
        session,
        q,
        status=None if status_filter == "all" else status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/similar", response_model=list[str])
def similar_titles(
    session: SessionDep,
    title: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> list[str]:
    """按标题找可能已存在的知识，供新建表单提示"你是不是已经记过了"。

    刻意用 LIKE 而不是 FTS：这个提示对 2 字标题也要有效。
    """
    return search_service.find_similar_titles(session, title, limit=limit)


# ===== 集合与单条 =====


@router.post("", response_model=KnowledgeRead, status_code=status.HTTP_201_CREATED)
def create_knowledge(session: SessionDep, payload: KnowledgeCreate) -> KnowledgeRead:
    """新建知识单元，同时创建第 1 个版本。

    「名称」和「自己的理解」必填。强制写理解是刻意设计：
    如果允许只写标题就保存，会退化成收藏癖——存了 500 条，一条都没学会。
    """
    knowledge = knowledge_service.create_knowledge(session, payload)
    return KnowledgeRead.model_validate(knowledge)


@router.get("", response_model=KnowledgePage)
def list_knowledge(
    session: SessionDep,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = "active",
    domain: Annotated[str | None, Query(max_length=100)] = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> KnowledgePage:
    """分页列出知识单元。默认只列 active，归档项要显式请求（Blueprint 5.1）。"""
    items, total = knowledge_service.list_knowledge(
        session,
        status=None if status_filter == "all" else status_filter,
        domain=domain,
        limit=limit,
        offset=offset,
    )
    return KnowledgePage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{knowledge_id}", response_model=KnowledgeRead)
def get_knowledge(session: SessionDep, knowledge_id: str) -> KnowledgeRead:
    """知识详情，含全部版本历史与来源。"""
    return KnowledgeRead.model_validate(
        knowledge_service.get_knowledge(session, knowledge_id)
    )


@router.put("/{knowledge_id}", response_model=KnowledgeRead)
def update_knowledge(
    session: SessionDep, knowledge_id: str, payload: KnowledgeUpdate
) -> KnowledgeRead:
    """更新知识单元。

    **只有「自己的理解 / 理解依据 / 尚存疑问」真的变了才创建新版本**；
    改标题、领域、摘要、来源是就地更新，不产生版本（DECISIONS D-030）。

    请求体里**没出现**的字段保持原样；显式传 `null` 表示清空该字段。
    这两者语义不同，靠 Pydantic 的 `model_fields_set` 区分。

    归档不走这里，用下面的 archive / restore 接口。
    """
    return KnowledgeRead.model_validate(
        knowledge_service.update_knowledge(session, knowledge_id, payload)
    )


@router.post("/{knowledge_id}/archive", response_model=KnowledgeRead)
def archive_knowledge(session: SessionDep, knowledge_id: str) -> KnowledgeRead:
    """归档。**不物理删除**：数据完整保留，只是默认列表和搜索不再显示。

    做成独立接口而不是 PUT 的一个字段，是因为归档会把数据从默认视野里移走，
    误操作代价比误改标题大得多，前端应当把它做成需要确认的动作。
    """
    return KnowledgeRead.model_validate(
        knowledge_service.archive_knowledge(session, knowledge_id)
    )


@router.post("/{knowledge_id}/restore", response_model=KnowledgeRead)
def restore_knowledge(session: SessionDep, knowledge_id: str) -> KnowledgeRead:
    """从归档恢复。"""
    return KnowledgeRead.model_validate(
        knowledge_service.restore_knowledge(session, knowledge_id)
    )
