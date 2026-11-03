"""AI 旁路的 HTTP 接口：学习对话、提取草稿、知识审查与处置。

和 `api/knowledge.py` 一样，本层只做三件事：解析请求、调 service、
把结果映射成 HTTP 语义。**不写 SQL，不做业务判断**（docs/ARCHITECTURE.md）。

## 三处和知识接口不同，都不是随手写的

**1. 流式端点的失败不走全局 exception handler。**
响应头在第一个碎片之前就发出去了（200 + text/event-stream），
那一刻起"把状态码改成 502"这件事已经不存在。所以 `_sse()` 自己捕获 `AiError`，
在流里发一个 `error` 事件；`/extract`、`/reviews` 这些非流式端点照旧由
`main.py` 的 handler 映射状态码。**同一个失败两种出口**是 HTTP 流式响应决定的。

**2. 失败响应额外带 `error_kind`。**
只说"AI 出错了"等于没报：timeout 该重试、auth_rejected 该去改 key、
bad_request 该改模型名（docs/AI_WORKFLOWS.md 第八节）。
`detail` 保持"给人看的那句话"，和知识接口的错误形状一致，前端不用特判。

**3. AI 未配置是 409，不是 500。**
`AiNotConfigured` 不是上游失败，是本地少填了地址或模型名（C4：
核心功能照常可用，AI 功能要给出可操作的下一步）。

## 路由顺序

全部路径段都不歧义：`/reviews` 与 `/reviews/{review_id}` 是两条完整路径，
`/conversations` 的 GET 与 DELETE 靠方法区分。
`api/knowledge.py` 那种"静态段必须排在 `/{id}` 之前"的坑在这里不存在。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.deps import ProviderDep
from backend.db.models import AiConversation, AiReview
from backend.db.session import get_session
from backend.schemas.ai import KnowledgeDraft
from backend.schemas.ai_api import (
    AcceptReviewRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    DraftConfirmRequest,
    DraftConfirmResult,
    HistoryCleared,
    MessageCreate,
    MessageRead,
    ReviewCreateRequest,
    ReviewDecisionRead,
    ReviewDecisionRequest,
    ReviewRead,
)
from backend.schemas.knowledge import KnowledgeRead
from backend.services import ai_service
from backend.services.ai.errors import AiError
from backend.services.ai.provider import AiProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])

SessionDep = Annotated[Session, Depends(get_session)]

# 清空历史是本项目唯一的硬删除入口（D-041）。
# 把这个 token 做成必填查询参数，是为了让"不小心调到删除"在**接口层就不可能**：
# 前端的确认框挡得住手滑，挡不住有人在脚本里照着 URL 敲。
ConfirmToken = Literal["yes"]

ListLimit = Annotated[int, Query(ge=1, le=200, description="返回条数上限")]


# ================================================================ 工作流 A：对话


@router.post("/conversations", response_model=ConversationRead, status_code=201)
def create_conversation(
    session: SessionDep, payload: ConversationCreate
) -> AiConversation:
    """新建学习会话。标题取用户第一句话，**不发付费请求去让 AI 起名**（D-042）。

    这里只建会话、不改名、不调 AI：第一句话要作为消息进入下一轮的上下文，
    那属于 `/messages` 的职责（一次调用一次流式回复）。
    """
    return ai_service.create_conversation(session, payload.text)


@router.get("/conversations", response_model=list[ConversationRead])
def list_conversations(session: SessionDep, limit: ListLimit = 50) -> list[AiConversation]:
    """历史会话列表，按最近活动倒序。永久保留，只有显式清空才会删（D-041）。"""
    return ai_service.list_conversations(session, limit=limit)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(session: SessionDep, conversation_id: str) -> ConversationDetail:
    """会话详情 + 全部消息。

    ⚠️ 消息用 `list_messages()` 显式查询，不读 `conversation.messages` 关系属性：
    `expire_on_commit=False` 下同一 Session 里已加载的集合不会自动刷新
    （阶段 2 实测踩过三次，见 `ai_service._context_messages` 的说明）。
    """
    conversation = ai_service.get_conversation(session, conversation_id)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            MessageRead.model_validate(message)
            for message in ai_service.list_messages(session, conversation_id)
        ],
    )


@router.post("/conversations/{conversation_id}/messages")
def stream_message(
    session: SessionDep,
    provider: ProviderDep,
    conversation_id: str,
    payload: MessageCreate,
) -> StreamingResponse:
    """发一句、以 SSE 流式收回答（D-039：只有自由对话走流式）。

    ⚠️ 先在这里确认会话存在，让"会话不存在"仍然是 404。
    一旦进入流，状态码已经发出去了，任何失败都只能变成流里的事件——
    把一个拼错的 id 变成"流里报 internal"就等于让用户猜。

    前端要用 `fetch` + `ReadableStream` 读，**不能用 EventSource**：
    后者只能发 GET，而这里是带请求体的 POST。
    """
    ai_service.get_conversation(session, conversation_id)
    return StreamingResponse(
        _sse(session, provider, conversation_id, payload.text),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/conversations", response_model=HistoryCleared)
def clear_history(
    session: SessionDep,
    confirm: Annotated[
        ConfirmToken, Query(description="必须显式传 yes，防止误删：这个操作不可撤销")
    ],
) -> HistoryCleared:
    """清空全部对话历史。**只删对话，不碰知识**。

    知识、版本、来源、审查记录一行都不动（有专门的测试），
    消息行靠数据库级 `ON DELETE CASCADE` 跟着会话走。
    """
    return HistoryCleared(
        deleted_conversations=ai_service.clear_conversation_history(session)
    )


@router.post("/conversations/{conversation_id}/extract", response_model=KnowledgeDraft)
def extract_draft(
    session: SessionDep, provider: ProviderDep, conversation_id: str
) -> KnowledgeDraft:
    """从这段对话提取知识草稿。

    ⚠️ 草稿**不落库**（C2）：这个响应里没有 id，用户不确认就什么都不留。
    `ai_supplements` 单独一栏，前端必须和"用户自己的理解"分开展示（C1）。

    空对话直接 409：那是一次注定没有意义的付费调用（D-042 要求少花冤枉钱）。
    """
    ai_service.get_conversation(session, conversation_id)
    if not ai_service.list_messages(session, conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="这段对话还没有内容，先聊几句再来提取。",
        )
    return ai_service.draft_from_conversation(session, provider, conversation_id)


@router.post("/drafts/confirm", response_model=DraftConfirmResult, status_code=201)
def confirm_draft(
    session: SessionDep, payload: DraftConfirmRequest, response: Response
) -> DraftConfirmResult:
    """用户确认（并且往往已经改过）的草稿，这是它第一次变成知识记录。

    ⚠️ 幂等：同一个 `idempotency_key` 重复提交只有一条知识。
    重放时把状态码改成 200 而不是再报一次 201——
    201 意味着"我新建了一条"，那是假的。
    """
    result = ai_service.confirm_draft(
        session, payload.draft, idempotency_key=payload.idempotency_key
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return DraftConfirmResult(
        knowledge=KnowledgeRead.model_validate(result.knowledge),
        replayed=result.replayed,
    )


# ================================================================ 工作流 C：审查


@router.post("/reviews", response_model=ReviewRead, status_code=201)
def create_review(
    session: SessionDep, provider: ProviderDep, payload: ReviewCreateRequest
) -> ReviewRead:
    """让 AI 审查这条知识当前的理解。一次调用 = 一行 `ai_reviews`。

    发给 AI 的内容只有标题、领域和当前版本的三个理解字段（C5 最小必要），
    历史版本、来源全文、别的知识一个字都不发。

    ⚠️ 这是**付费操作**，前端点击前要先说明"会调用一次 AI"（D-042）。
    """
    review = ai_service.review_knowledge(session, provider, payload.knowledge_id)
    return _review_read(review)


@router.get("/reviews", response_model=list[ReviewRead])
def list_reviews(
    session: SessionDep,
    knowledge_id: Annotated[str, Query(min_length=1, max_length=36, description="知识单元 id")],
) -> list[ReviewRead]:
    """某条知识的全部审查记录，按时间倒序。**旧记录不删**：
    处置决定按 (category, item_index) 定位，删掉旧审查会让指针对到别的条目上。"""
    return [_review_read(review) for review in ai_service.list_reviews(session, knowledge_id)]


@router.get("/reviews/{review_id}", response_model=ReviewRead)
def get_review(session: SessionDep, review_id: str) -> ReviewRead:
    return _review_read(ai_service.get_review(session, review_id))


@router.put("/reviews/{review_id}/decisions", response_model=ReviewRead)
def decide_review_item(
    session: SessionDep, review_id: str, payload: ReviewDecisionRequest
) -> ReviewRead:
    """给审查结果里的某一条记一个处置：采纳 / 忽略 / 不同意。

    三个处置在数据上完全平等，没有默认值偏向，改主意是覆盖而不是插新行。
    返回**整份审查**而不是单条处置，前端少一次往返，也就少一次
    "本地状态和服务端状态对不上"的机会。
    """
    ai_service.decide_review_item(
        session, review_id, payload.category, payload.item_index, payload.decision
    )
    # 再读一次：decide_review_item 内部已经加载过 decisions 集合，
    # 不重新查询就拿不到刚写入的那条（expire_on_commit=False 的老坑）。
    return _review_read(ai_service.get_review(session, review_id))


@router.post("/reviews/{review_id}/accept", response_model=KnowledgeRead)
def accept_review(
    session: SessionDep, review_id: str, payload: AcceptReviewRequest
) -> KnowledgeRead:
    """把**用户重写过的**理解存成新版本，采纳了哪些建议留档在 `ai_revision`。

    ⚠️ 这是 C1 的落点，两件事在这个签名下做不到：
    - 把 AI 的文字塞进 `user_understanding` —— 那句话只能由请求体里的
      `new_understanding` 提供，也就是用户打字的内容；
    - 声称采纳了没在界面上点过的条目 —— 采纳了哪些由服务端读数据库里
      `decision='accepted'` 的处置，不接受调用方传列表。

    `knowledge_id` 从审查记录本身取，而不是让前端再传一次：
    两个来源就有对不上的可能，而 service 层会再交叉校验一遍归属关系。
    """
    review = ai_service.get_review(session, review_id)
    knowledge = ai_service.accept_ai_review(
        session,
        knowledge_id=review.knowledge_id,
        review_id=review_id,
        new_understanding=payload.new_understanding,
        reasoning=payload.reasoning,
        open_questions=payload.open_questions,
        change_reason=payload.change_reason,
    )
    return KnowledgeRead.model_validate(knowledge)


# ==================================================================== 内部


def _sse(
    session: Session, provider: AiProvider, conversation_id: str, text: str
) -> Iterator[str]:
    """把 service 的文本碎片流包成 SSE 事件。

    ⚠️ `done` 必须在内层生成器**耗尽之后**才发：内层最后一段代码是
    "把拼好的完整文本落库 + commit"，它跟着最后一次 `next()` 执行。
    提前发 done 会让前端在数据还没提交时就去重新拉取，然后读到半截历史。
    """
    try:
        for chunk in ai_service.stream_reply(session, provider, conversation_id, text):
            yield _event({"type": "chunk", "text": chunk})
        yield _event({"type": "done"})
    except AiError as exc:
        # 状态码已经发出去了，这里只能往流里写。service 已经把这条失败
        # 落成一条带 error_kind 的助手消息，所以刷新历史也看得到。
        yield _event(
            {
                "type": "error",
                "error_kind": exc.error_kind,
                "message": exc.user_message,
            }
        )
    except Exception:  # noqa: BLE001 - 流已经开了，静默截断比报错更难排查
        logger.exception("会话 %s 的流式响应异常中断", conversation_id)
        yield _event(
            {
                "type": "error",
                "error_kind": "internal",
                "message": "服务在处理这条消息时出错了，请重试。",
            }
        )


def _event(event: dict) -> str:
    """一条 SSE 事件。`data:` 行 + 空行结束，这是浏览器的解析约定。

    ensure_ascii=False：正文是中文，转成 \\uXXXX 只会让流变大三倍。
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _review_read(review: AiReview) -> ReviewRead:
    """ORM 审查记录 + 反序列化的五分类 + 已记录的处置 → 一个响应对象。"""
    result = ai_service.review_result(review)
    return ReviewRead(
        id=review.id,
        knowledge_id=review.knowledge_id,
        version_id=review.version_id,
        version_number=review.version.version_number,
        model=review.model,
        prompt_version=review.prompt_version,
        created_at=review.created_at,
        prompt_tokens=review.prompt_tokens,
        completion_tokens=review.completion_tokens,
        duration_ms=review.duration_ms,
        result=result,
        decisions=[
            ReviewDecisionRead(
                category=decision.category,
                item_index=decision.item_index,
                decision=decision.decision,
                updated_at=decision.updated_at,
            )
            for decision in review.decisions
        ],
        is_empty=result.is_empty,
    )
