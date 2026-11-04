"""复习中心的 REST 接口（阶段 3 第④⑤步）。

本层只做三件事：解析请求、调 service、映射 HTTP 状态码（分层约定同
api/knowledge.py）。**静态路径 /due 与 /due/count 必须声明在
/{knowledge_id}/... 之前**——不然 "due" 会被当成知识 id。

所有"知识不存在"由 main.py 的 KnowledgeNotFound 处理器统一映射 404；
"已归档不能复习"（KnowledgeNotSchedulable）映射 409，理由和
AiNotConfigured 用 409 一样：不是程序坏了，是这条数据现在不能这么操作。

对话式复习（⑤）的两个端点会花 AI 请求费，前端点击前要说明（D-042）；
AI 失败由 main.py 的 AiError 处理器映射状态码，前端降级成纯自测（D-062）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.api.deps import ProviderDep
from backend.db.base import utcnow
from backend.db.session import get_session
from backend.schemas.ai import RatingSuggestion
from backend.schemas.review_api import (
    DerivedStatsRead,
    DueCountRead,
    DueItemRead,
    DueListRead,
    RatingSuggestionRequest,
    ReviewChatStartRead,
    ReviewOutcomeRead,
    ReviewRatingRequest,
    ReviewSessionRead,
    ScheduleRead,
    SuspensionRequest,
    SuspensionResult,
)
from backend.services import ai_service, review_service

router = APIRouter(prefix="/api/review", tags=["review"])

SessionDep = Annotated[Session, Depends(get_session)]

LimitQuery = Annotated[int, Query(ge=1, le=200, description="最多返回多少条")]


# ===== 静态路径：必须在 /{knowledge_id} 之前 =====


@router.get("/due", response_model=DueListRead)
def list_due(session: SessionDep, limit: LimitQuery = 50) -> DueListRead:
    """现在该复习哪些。归档的、暂停的、还没到期的都不出现。"""
    items = review_service.due_list(session, limit=limit)
    return DueListRead(
        items=[DueItemRead.model_validate(item) for item in items],
        count=len(items),
        generated_at=utcnow(),
    )


@router.get("/due/count", response_model=DueCountRead)
def count_due(session: SessionDep) -> DueCountRead:
    """首页要的"今天还剩几条"。"""
    return DueCountRead(count=review_service.due_count(session), generated_at=utcnow())


# ===== 单条知识的排期与复习 =====


@router.get("/{knowledge_id}/schedule", response_model=ScheduleRead)
def get_schedule(knowledge_id: str, session: SessionDep) -> ScheduleRead:
    schedule = review_service.get_schedule(session, knowledge_id)
    return ScheduleRead.model_validate(schedule)


@router.post("/{knowledge_id}/rate", response_model=ReviewOutcomeRead)
def rate_knowledge(
    knowledge_id: str, body: ReviewRatingRequest, session: SessionDep
) -> ReviewOutcomeRead:
    """提交一次复习。`rating` 只认用户打的分（C7），AI 建议只存档。"""
    outcome = review_service.review_now(
        session,
        knowledge_id,
        body.rating,
        mode=body.mode,
        suggested_rating=body.suggested_rating,
        ai_comment=body.ai_comment,
        conversation_id=body.conversation_id,
        duration_ms=body.duration_ms,
    )
    return ReviewOutcomeRead(
        knowledge_id=outcome.knowledge_id,
        rating=outcome.rating,
        mode=outcome.mode,
        state=outcome.state,
        due=outcome.due,
        stats=DerivedStatsRead.model_validate(outcome.stats),
    )


@router.put("/{knowledge_id}/suspension", response_model=SuspensionResult)
def set_suspension(
    knowledge_id: str, body: SuspensionRequest, session: SessionDep
) -> SuspensionResult:
    """暂停 / 恢复复习（D-060）。幂等。"""
    schedule = review_service.set_suspended(
        session, knowledge_id, suspended=body.suspended
    )
    return SuspensionResult(knowledge_id=knowledge_id, status=schedule.status)


# ================================================================ 对话式复习（⑤）


@router.post(
    "/{knowledge_id}/session", response_model=ReviewChatStartRead, status_code=201
)
def start_review_chat(
    knowledge_id: str, session: SessionDep, provider: ProviderDep
) -> ReviewChatStartRead:
    """开一场 AI 对话式复习：AI 出第一道题。**付费操作**，前端要先说明（D-042）。

    AI 失败时 service 什么都不落库，异常由 AiError 处理器映射状态码；
    前端收到错误就降级成纯自测（D-062），复习不被 AI 绑架。
    """
    conversation = ai_service.start_review_chat(session, provider, knowledge_id)
    first_question = ai_service.list_messages(session, conversation.id)[0].content
    return ReviewChatStartRead(
        conversation_id=conversation.id,
        title=conversation.title,
        first_question=first_question,
    )


@router.post(
    "/{knowledge_id}/rating-suggestion", response_model=RatingSuggestion
)
def suggest_rating(
    knowledge_id: str,
    body: RatingSuggestionRequest,
    session: SessionDep,
    provider: ProviderDep,
) -> RatingSuggestion:
    """让 AI 根据这段复习对话给**评级建议**。返回值只是建议，用户确认才算数（C7）。

    空对话直接 409：那是一次注定没有意义的付费调用（同 /extract 的先例）。
    """
    ai_service.get_conversation(session, body.conversation_id)  # 不存在 → 404
    if not ai_service.list_messages(session, body.conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这场复习对话还没有内容，先回答 AI 的问题。",
        )
    return ai_service.suggest_review_rating(
        session, provider, knowledge_id, body.conversation_id
    )


@router.get("/{knowledge_id}/history", response_model=list[ReviewSessionRead])
def list_history(
    knowledge_id: str, session: SessionDep, limit: LimitQuery = 100
) -> list[ReviewSessionRead]:
    """某条知识的复习历史，时间升序。"""
    return [
        ReviewSessionRead.model_validate(row)
        for row in review_service.history(session, knowledge_id, limit=limit)
    ]
