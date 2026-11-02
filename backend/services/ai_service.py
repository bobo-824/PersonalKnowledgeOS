"""AI 旁路的业务编排（工作流 A / B / C）。

**事务边界在这一层**，一个业务动作 = 一个函数 = 一次 commit。

## 贯穿本文件的三条硬规则

1. **AI 永不写 `user_understanding`（C1）**。
   唯一的"把 AI 的输出变成知识"的通路是 `confirm_draft`，而它收的是
   **用户已经看过并确认过的草稿对象**；采纳审查建议走 `accept_ai_review`，
   那里的新理解是**函数入参，由用户打字提供**。
   两个签名都不接受"把 AI 文本塞进 user_understanding"这个调用方式——
   约束长在结构上，不是靠注释提醒。
2. **草稿不落库（C2）**。`draft_from_conversation` 只返回内存对象，
   用户中途关页面，什么都不留。
3. **畸形输出不写库（C3）**。校验发生在 provider 层，
   `AiSchemaInvalid` 一路冒上来，本文件不捕获它、也就没有任何路径能把它变成行。

## 为什么 provider 是入参而不是在这里自己构造

和 `session` 同理：让调用方注入，测试才能塞进 `FakeProvider`。
见 `backend/services/ai/fake.py`——**全部 AI 测试不联网、不需要 key**（D-038）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from backend.db.base import utcnow
from backend.db.models import (
    CREATED_BY_AI_CONFIRMED,
    DECISION_ACCEPTED,
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_USER,
    REVIEW_CATEGORIES,
    AiConversation,
    AiMessage,
    AiReview,
    AiReviewDecision,
    IdempotencyKey,
    Knowledge,
    KnowledgeVersion,
)
from backend.domain.versioning import Understanding, next_version_number
from backend.schemas.ai import KnowledgeDraft, ReviewResult
from backend.schemas.knowledge import KnowledgeCreate
from backend.services.ai.errors import AiError
from backend.services.ai.prompts import PROMPT_VERSION, system_message
from backend.services.ai.provider import AiProvider, ChatMessage
from backend.services.knowledge_service import append_version, create_knowledge, get_knowledge
from backend.services.search_service import sync_index

logger = logging.getLogger(__name__)

# 对话上下文最多带这么多条消息。发得越多越贵，也越容易让模型抓不住重点。
MAX_CONTEXT_MESSAGES = 40

# 会话标题从第一条用户消息截断而来，列宽是 String(100)
_TITLE_SOURCE_LENGTH = 60
_TITLE_MAX_LENGTH = 100


class ConversationNotFound(LookupError):
    pass


class ReviewNotFound(LookupError):
    pass


class ReviewItemNotFound(LookupError):
    pass


class EmptyDraftError(ValueError):
    """草稿里没有任何可保存的理解。"""


# ================================================================ 会话与消息


def create_conversation(session: Session, first_user_text: str) -> AiConversation:
    """新建会话。标题取用户第一句话的前若干字，**不让 AI 起名**。

    让 AI 起名要多发一次付费调用，而且对话还没聊完就得先等 AI
    （D-042 要求批量付费操作先确认）。用户之后可以自己在界面上改。
    """
    conversation = AiConversation(title=_derive_title(first_user_text))
    session.add(conversation)
    session.commit()
    logger.info("已创建 AI 会话 %s", conversation.id)
    return conversation


def get_conversation(session: Session, conversation_id: str) -> AiConversation:
    conversation = session.get(AiConversation, conversation_id)
    if conversation is None:
        raise ConversationNotFound(conversation_id)
    return conversation


def list_conversations(session: Session, limit: int = 50) -> list[AiConversation]:
    return list(
        session.scalars(
            select(AiConversation)
            .order_by(AiConversation.updated_at.desc())
            .limit(limit)
        )
    )


def list_messages(session: Session, conversation_id: str) -> list[AiMessage]:
    """按会话内序号排序。不能用 created_at 排——同一毫秒内的两条消息先后不定。"""
    return list(
        session.scalars(
            select(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .order_by(AiMessage.seq)
        )
    )


def stream_reply(
    session: Session,
    provider: AiProvider,
    conversation_id: str,
    user_text: str,
) -> Iterator[str]:
    """工作流 A：流式对话（D-039，只有这条路走流式）。

    ⚠️ 是生成器。用户消息**先落库**，助手消息在流结束时落库——
    存的是拼好的完整文本，不是碎片。失败也落一条助手消息，
    内容是给用户看的错误文案，`error_kind` 记类别（不记响应体）。

    之所以先提交用户消息：如果 AI 挂了，用户那句话仍然在，
    不会因为"助手没回"而把他的输入一起丢掉。
    """
    conversation = get_conversation(session, conversation_id)
    _append_message(session, conversation, MESSAGE_ROLE_USER, user_text)
    session.commit()

    request = _conversation_request(session, conversation)
    collected: list[str] = []

    try:
        for chunk in provider.stream_chat(request):
            collected.append(chunk)
            yield chunk
        content = "".join(collected)
    except AiError as exc:
        # 错误文案是固定字符串，不含响应体和 key；用户能看到"这里失败了"
        _append_message(
            session,
            conversation,
            MESSAGE_ROLE_ASSISTANT,
            exc.user_message,
            provider=provider,
            error_kind=exc.error_kind,
        )
        session.commit()
        logger.warning("会话 %s 的 AI 调用失败：%s", conversation.id, exc.error_kind)
        raise

    _append_message(
        session,
        conversation,
        MESSAGE_ROLE_ASSISTANT,
        content,
        provider=provider,
    )
    session.commit()


def clear_conversation_history(session: Session) -> int:
    """删除全部会话与消息。返回删掉的**会话数**。

    这是全项目唯一的硬删除入口（D-041）。删的是对话，不是知识：
    `knowledge` / `knowledge_versions` 一行都不碰，
    消息行靠数据库级 `ON DELETE CASCADE` 一起走（有专门的测试）。

    日志只记条数，不记正文（C6）。
    """
    count = session.scalar(select(func.count(AiConversation.id))) or 0
    session.execute(delete(AiConversation))
    session.commit()
    logger.info("已清空 AI 对话历史，删除 %s 个会话", count)
    return int(count)


# ================================================================ 工作流 B


def draft_from_conversation(
    session: Session, provider: AiProvider, conversation_id: str
) -> KnowledgeDraft:
    """从对话提取知识草稿。**只在内存里，不落库**（C2）。

    发给 AI 的只有这一段对话（C5）。用户中途关页面什么都不留——
    这既是 C2 的要求，也避免了垃圾数据堆积。

    收 `conversation_id` 而不是收 `AiConversation` 对象，
    是为了让上下文一定来自一次新鲜查询（见 `_context_messages` 的 ⚠️）。
    """
    conversation = get_conversation(session, conversation_id)
    request = [system_message("extract")] + [
        _as_chat_message(m) for m in _context_messages(session, conversation.id)
    ]
    result = provider.chat(request, KnowledgeDraft)
    return result.value


def draft_to_create_payload(draft: KnowledgeDraft) -> dict:
    """草稿 → `KnowledgeCreate` 的转接。

    三件事必须在这里讲清楚（对应 tests/test_ai_schemas.py 的映射表）：
    - `suggested_sources` **改名**成 `sources`（有意不同名，D-049）
    - `ai_supplements` **丢弃**：它没有对应的列，也不该有。
      用户要采纳某条补充，那句话得由他打字写进 `user_understanding`，
      程序一拼，这条记录就说不清是谁说的了（C1）。
    - `source_type` 固定成 `conversation`：这条知识是从对话里来的。
    """
    payload = draft.model_dump(
        exclude={"ai_supplements", "suggested_sources"}
    )
    payload["sources"] = draft.model_dump()["suggested_sources"]
    payload["source_type"] = "conversation"
    return payload


def confirm_draft(
    session: Session, draft: KnowledgeDraft, *, idempotency_key: str
) -> Knowledge:
    """用户确认草稿，这才第一次落库。

    ⚠️ **幂等**：Blueprint 阶段 2 验收项要求"不会重复提交造成重复记录"。
    前端按钮 disabled 只挡得住手滑，挡不住超时重发和刷新后重点，
    所以去重必须在服务端。

    ⚠️ 已知的残余风险（诚实记录）：`create_knowledge` 自己会 commit，
    所以"知识行已提交"和"幂等键已记录"之间有一个极窄的窗口。
    进程恰好在那中间被杀，重试就会多出一条重复知识。
    彻底关掉要么让 `knowledge_id` 可空（先占位再回填，需要迁移），
    要么改造 `create_knowledge` 的事务边界。对单人本地应用，
    这个代价大于收益，所以留下并写明。
    """
    if not draft.user_understanding.strip():
        raise EmptyDraftError("草稿里还没有可保存的理解，请自己写一句再保存")

    existing = session.get(IdempotencyKey, idempotency_key)
    if existing is not None:
        logger.info("幂等键命中，返回已创建的知识 %s", existing.knowledge_id)
        return get_knowledge(session, existing.knowledge_id)

    knowledge = create_knowledge(session, KnowledgeCreate.model_validate(draft_to_create_payload(draft)))
    session.add(IdempotencyKey(key=idempotency_key, knowledge_id=knowledge.id))
    session.commit()
    return get_knowledge(session, knowledge.id)


# ================================================================ 工作流 C


def review_knowledge(session: Session, provider: AiProvider, knowledge_id: str) -> AiReview:
    """工作流 C：审查当前版本的理解。

    ⚠️ **最小必要传输（C5）就体现在 `_review_request` 里**：
    只发当前版本的三个理解字段 + 标题 + 领域。
    不发历史版本、不发其他知识、不发来源全文。
    有专门的测试断言发出去的内容里没有别的东西。
    """
    knowledge = get_knowledge(session, knowledge_id)
    version = knowledge.current_version

    result = provider.chat(_review_request(knowledge, version), ReviewResult)
    review = AiReview(
        knowledge_id=knowledge.id,
        version_id=version.id,
        model=provider.model_id,
        prompt_version=PROMPT_VERSION,
        result_json=result.value.model_dump_json(),
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        duration_ms=result.duration_ms,
    )
    session.add(review)
    session.commit()
    logger.info(
        "知识单元 %s 的 v%d 已审查（%dms，%d 条建议）",
        knowledge.id,
        version.version_number,
        review.duration_ms or 0,
        _item_count(result.value),
    )
    return review


def get_review(session: Session, review_id: str) -> AiReview:
    review = session.get(AiReview, review_id)
    if review is None:
        raise ReviewNotFound(review_id)
    return review


def list_reviews(session: Session, knowledge_id: str) -> list[AiReview]:
    """按时间倒序。⚠️ 旧的审查记录**不删**——
    处置决定是按 (category, item_index) 定位的，删了就指错条目了。"""
    return list(
        session.scalars(
            select(AiReview)
            .where(AiReview.knowledge_id == knowledge_id)
            .options(selectinload(AiReview.decisions))
            .order_by(AiReview.created_at.desc())
        )
    )


def review_result(review: AiReview) -> ReviewResult:
    """把存下来的转储读回成模型。存的永远是校验过的转储，不会解析失败。"""
    return ReviewResult.model_validate_json(review.result_json)


def decide_review_item(
    session: Session, review_id: str, category: str, item_index: int, decision: str
) -> AiReviewDecision:
    """给审查结果里的某一条记一个处置。改主意是覆盖，不是插新行。

    三个处置（采纳 / 忽略 / 不同意）在数据上完全平等，没有默认值偏向——
    C7 要求用户不采纳时不弹警告、不标红、不降级体验。
    """
    if category not in REVIEW_CATEGORIES:
        raise ReviewItemNotFound(f"未知的审查类别：{category}")
    review = get_review(session, review_id)

    if item_index < 0 or item_index >= len(_category_items(review_result(review), category)):
        raise ReviewItemNotFound(f"{category} 里没有第 {item_index} 条")

    existing = session.scalar(
        select(AiReviewDecision).where(
            AiReviewDecision.review_id == review_id,
            AiReviewDecision.category == category,
            AiReviewDecision.item_index == item_index,
        )
    )
    if existing is not None:
        existing.decision = decision
        existing.updated_at = utcnow()
        session.commit()
        return existing

    record = AiReviewDecision(
        review_id=review_id,
        category=category,
        item_index=item_index,
        decision=decision,
    )
    session.add(record)
    session.commit()
    return record


def accept_ai_review(
    session: Session,
    *,
    knowledge_id: str,
    review_id: str,
    new_understanding: str,
    reasoning: str | None = None,
    open_questions: str | None = None,
    change_reason: str | None = None,
) -> Knowledge:
    """用户采纳若干条审查建议后，把他**重写过的理解**存成新版本。

    ⚠️ **这条路径是 C1 的结构性保证**：
    `user_understanding` 只来自 `new_understanding` 这个入参，由用户打字提供。
    AI 的文字唯一去处是 `ai_revision` 列。
    换句话说，"把 AI 的话写进用户理解"这个调用**在这个签名下无法表达**，
    不是靠调用方自觉。

    采纳了哪些条目也不由调用方传，而是**读数据库里已记录的处置**
    （decision='accepted'）。这样 ai_revision 的内容和用户实际点过的
    东西一定对得上，也杜绝了塞进没展示过的条目。

    理解没有真的改变时不创建新版本（D-030），避免"点了保存但版本号 +1"的假历史。
    此时 ai_revision 也不会被写进任何行——没有版本可写。
    """
    knowledge = get_knowledge(session, knowledge_id)
    review = get_review(session, review_id)
    if review.knowledge_id != knowledge.id:
        raise ReviewNotFound(f"审查 {review_id} 不属于知识 {knowledge_id}")

    current = knowledge.current_version
    effective = Understanding(
        user_understanding=new_understanding,
        reasoning=reasoning if reasoning is not None else current.reasoning,
        open_questions=(
            open_questions if open_questions is not None else current.open_questions
        ),
    )

    version = append_version(
        session,
        knowledge,
        current,
        effective,
        change_reason=change_reason or "采纳 AI 审查建议",
        created_by=CREATED_BY_AI_CONFIRMED,
        ai_revision=_accepted_digest(review),
    )
    if version is None:
        logger.info("采纳审查建议但理解文本未变化，不创建新版本：%s", knowledge_id)
        return knowledge

    # 正文变了，索引必须同步（D-006）。放在这里而不是 append_version 里，
    # 是为了让"一个事务同步一次"这件事在调用点就看得见。
    sync_index(session, knowledge)
    session.commit()
    return get_knowledge(session, knowledge.id)


# ==================================================================== 内部


def _derive_title(first_user_text: str) -> str:
    text = " ".join(first_user_text.split())
    if not text:
        return "新的对话"
    return text[:_TITLE_MAX_LENGTH]


def _context_messages(session: Session, conversation_id: str) -> list[AiMessage]:
    """取最近若干条**成功**的消息作为上下文。

    ⚠️ 这里刻意用一次显式查询，而不是读 `conversation.messages` 关系属性。
    会话配了 `expire_on_commit=False`（`db/session.py`），同一个 Session 里
    已经加载过的集合**不会自动刷新**——读关系属性会拿到陈旧集合，
    多轮对话就把前面的助手回复整段丢掉。生产路径每个请求一个新 session
    侥幸不会踩到，但依赖"每次都是新 session"太脆，依赖查询则一定正确。
    （变异测试实测出来的：删掉下面的 error_kind 过滤后测试仍然全绿，
    就是因为集合是陈旧的、过滤根本没机会生效。）

    失败的那几条不发给 AI：把「无法连接 AI 服务」当作对话历史再发回去，
    只会让它继续困惑。
    """
    messages = [
        message
        for message in list_messages(session, conversation_id)
        if message.error_kind is None
    ]
    return messages[-MAX_CONTEXT_MESSAGES:]


def _as_chat_message(message: AiMessage) -> ChatMessage:
    return ChatMessage(role=message.role, content=message.content)


def _conversation_request(
    session: Session, conversation: AiConversation
) -> list[ChatMessage]:
    return [system_message("study_chat")] + [
        _as_chat_message(m) for m in _context_messages(session, conversation.id)
    ]


def _review_request(knowledge: Knowledge, version: KnowledgeVersion) -> list[ChatMessage]:
    """组装发给 AI 的内容。**C5 最小必要就锁在这个函数里。**

    只有标题、领域和当前版本的三个理解字段。
    历史版本、来源全文、其他知识，一个都不发。
    """
    parts = [f"名称：{knowledge.title}", f"领域：{knowledge.domain or '（未填）'}"]
    parts.append(f"我自己的理解：{version.user_understanding}")
    if version.reasoning:
        parts.append(f"我的推理依据：{version.reasoning}")
    if version.open_questions:
        parts.append(f"我还没解决的问题：{version.open_questions}")
    return [system_message("review"), ChatMessage(role="user", content="\n".join(parts))]


def _append_message(
    session: Session,
    conversation: AiConversation,
    role: str,
    content: str,
    *,
    provider: AiProvider | None = None,
    error_kind: str | None = None,
) -> AiMessage:
    seq = next_version_number(
        session.scalars(select(AiMessage.seq).where(AiMessage.conversation_id == conversation.id))
    )
    message = AiMessage(
        conversation_id=conversation.id,
        seq=seq,
        role=role,
        content=content,
        model=provider.model_id if provider is not None else None,
        error_kind=error_kind,
    )
    session.add(message)
    # 每追加一条都要顶一下 updated_at，历史列表按它倒序排
    conversation.updated_at = utcnow()
    session.flush()
    return message


def _category_items(result: ReviewResult, category: str) -> list:
    return list(getattr(result, category))


def _item_count(result: ReviewResult) -> int:
    return sum(len(_category_items(result, category)) for category in REVIEW_CATEGORIES)


def _accepted_digest(review: AiReview) -> str:
    """把用户**点过采纳**的条目原文拼成一段可读文本，存进 `ai_revision`。

    为什么留档而不是用完就丢（AI_WORKFLOWS 第四节）：
    三个月后回看这条知识，会想知道"我当初为什么改了这个理解？
    AI 当时说了什么？"只有把原话存下来，成长轨迹才是完整的。

    用 plain text 而不是 JSON：这一列是给人读的，不是给人解析的。
    内容一字不改地照抄存储的转储，只加类别前缀和序号定位。
    """
    result = review_result(review)
    decisions = [d for d in review.decisions if d.decision == DECISION_ACCEPTED]
    if not decisions:
        return ""

    lines: list[str] = []
    for decision in sorted(decisions, key=lambda d: (d.category, d.item_index)):
        items = _category_items(result, decision.category)
        if decision.item_index >= len(items):
            # 理论上不该发生：result_json 永不 UPDATE，索引一定对得上。
            # 真对不上说明数据被外部改过，宁可不写也别指错条目。
            logger.error(
                "审查 %s 的处置指向了不存在的条目：%s[%d]",
                review.id,
                decision.category,
                decision.item_index,
            )
            continue
        lines.append(f"[{decision.category} #{decision.item_index}] {_render_item(items[decision.item_index])}")
    return "\n".join(lines)


def _render_item(item: object) -> str:
    """把一个审查条目渲染成一行可读文字。顺序用 model_dump，稳定可预期。"""
    if isinstance(item, str):
        return item
    data = item.model_dump() if hasattr(item, "model_dump") else item
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)
    return "；".join(f"{key}={value}" for key, value in data.items())


__all__ = [
    "ConversationNotFound",
    "EmptyDraftError",
    "accept_ai_review",
    "clear_conversation_history",
    "confirm_draft",
    "create_conversation",
    "decide_review_item",
    "draft_from_conversation",
    "draft_to_create_payload",
    "get_conversation",
    "get_review",
    "list_conversations",
    "list_messages",
    "list_reviews",
    "review_knowledge",
    "review_result",
    "stream_reply",
]
