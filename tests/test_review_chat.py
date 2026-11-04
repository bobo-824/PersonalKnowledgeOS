"""对话式复习（阶段 3 第⑤步）的验收测试。

工作流 D 的三条重点：
- **C7**：AI 的评级建议只是建议，`suggest_review_rating` 不写任何表，
  排期只认用户提交的 rating。
- **C5**：发给 AI 的仍然只有标题 + 领域 + 当前版本三个理解字段，
  对话原文是用户自己说的话，不算额外泄露。
- **D-062 的后端责任**：AI 失败时开场必须什么都不落库——
  留一个空会话壳在历史里，等于让"降级成纯自测"变成撒谎。
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import (
    CONVERSATION_KIND_CHAT,
    CONVERSATION_KIND_REVIEW,
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_USER,
    RATINGS,
    AiConversation,
    Knowledge,
    ReviewSession,
)
from backend.schemas.ai import RatingSuggestion
from backend.schemas.knowledge import KnowledgeCreate
from backend.services import ai_service, review_service
from backend.services.ai.errors import AiSchemaInvalid, AiTimeout
from backend.services.ai.fake import FakeProvider
from backend.services.ai.prompts import system_message
from backend.services.knowledge_service import (
    KnowledgeNotFound,
    archive_knowledge,
    create_knowledge,
)
from backend.services.review_service import (
    KnowledgeNotSchedulable,
    ReviewConversationNotFound,
)

FIRST_QUESTION = "按照你的理解，学习率设太大会发生什么？"
FOLLOW_UP = "那震荡的时候损失值是什么样子？"


def make_knowledge(
    session: Session, title: str = "梯度下降", *, archived: bool = False
) -> Knowledge:
    knowledge = create_knowledge(
        session,
        KnowledgeCreate(
            title=title,
            user_understanding="每步往降最快的方向走一小步",
            reasoning="因为梯度指向上升最快的方向",
            open_questions="非凸函数上会怎样？",
            domain="机器学习",
        ),
    )
    if archived:
        archive_knowledge(session, knowledge.id)
        session.expire_all()
        knowledge = session.get(Knowledge, knowledge.id)
    return knowledge


def start_chat(session: Session, provider: FakeProvider, knowledge_id: str):
    provider.queue_stream([FIRST_QUESTION[:4], FIRST_QUESTION[4:]])
    return ai_service.start_review_chat(session, provider, knowledge_id)


def answer_once(session: Session, provider: FakeProvider, conversation_id: str) -> None:
    """让用户答一轮（走生产路径 stream_reply）。"""
    provider.queue_stream([FOLLOW_UP])
    list(ai_service.stream_reply(session, provider, conversation_id, "会跳过最低点"))


# ==================================================================== 开场


def test_start_creates_review_conversation_with_question(db_session: Session) -> None:
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)

    conversation = start_chat(db_session, provider, knowledge.id)

    assert conversation.kind == CONVERSATION_KIND_REVIEW
    assert conversation.title.startswith("复习：")

    messages = ai_service.list_messages(db_session, conversation.id)
    assert [m.role for m in messages] == [MESSAGE_ROLE_ASSISTANT]
    assert messages[0].content == FIRST_QUESTION, "流式碎片要拼成完整文本落库"

    # 第一问的请求 = 复习提示词 + 一条只有理解字段的用户消息
    assert len(provider.stream_calls) == 1
    request = provider.stream_calls[0]
    assert request[0] == system_message("review_chat")
    assert len(request) == 2
    body = request[1].content
    assert "梯度下降" in body and "机器学习" in body
    assert "每步往降最快的方向走一小步" in body
    assert "因为梯度指向上升最快的方向" in body
    assert "非凸函数上会怎样" in body


def test_start_does_not_reveal_or_send_extra_knowledge(
    db_session: Session,
) -> None:
    """C5：库里有第二条知识时，请求里不能出现它的任何内容。"""
    provider = FakeProvider()
    make_knowledge(db_session, title="不该出现的知识")
    knowledge = make_knowledge(db_session)

    start_chat(db_session, provider, knowledge.id)

    body = "\n".join(m.content for m in provider.stream_calls[0])
    assert "不该出现的知识" not in body


def test_start_rejects_unknown_and_archived_without_paying(
    db_session: Session,
) -> None:
    provider = FakeProvider()

    with pytest.raises(KnowledgeNotFound):
        ai_service.start_review_chat(db_session, provider, "不存在的ID")

    knowledge = make_knowledge(db_session, archived=True)
    with pytest.raises(KnowledgeNotSchedulable):
        ai_service.start_review_chat(db_session, provider, knowledge.id)

    assert provider.stream_calls == [], "不能复习的知识不该花 AI 请求费"


def test_start_failure_leaves_nothing_behind(db_session: Session) -> None:
    """D-062 的后端半边：AI 挂了，不许留半个会话壳。"""
    provider = FakeProvider()
    provider.queue_stream(AiTimeout())
    knowledge = make_knowledge(db_session)

    with pytest.raises(AiTimeout):
        ai_service.start_review_chat(db_session, provider, knowledge.id)

    count = db_session.scalar(
        select(func.count(AiConversation.id)).where(
            AiConversation.kind == CONVERSATION_KIND_REVIEW
        )
    )
    assert count == 0


# ==================================================================== 追问走 stream_reply


def test_follow_up_turns_use_review_prompt(db_session: Session) -> None:
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    conversation = start_chat(db_session, provider, knowledge.id)

    answer_once(db_session, provider, conversation.id)

    messages = ai_service.list_messages(db_session, conversation.id)
    assert [m.role for m in messages] == [
        MESSAGE_ROLE_ASSISTANT,
        MESSAGE_ROLE_USER,
        MESSAGE_ROLE_ASSISTANT,
    ]
    # 追问请求的提示词必须是 review_chat，不是学习室的 study_chat
    assert provider.stream_calls[-1][0] == system_message("review_chat")

    # 对照组：学习室对话还是 study_chat
    plain = ai_service.create_conversation(db_session, "什么是梯度下降？")
    provider.queue_stream(["你先说说你的理解？"])
    list(ai_service.stream_reply(db_session, provider, plain.id, "不知道"))
    assert provider.stream_calls[-1][0] == system_message("study_chat")


def test_study_room_list_hides_review_conversations(db_session: Session) -> None:
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    review_conversation = start_chat(db_session, provider, knowledge.id)
    plain = ai_service.create_conversation(db_session, "闲聊一句")

    listed = ai_service.list_conversations(db_session)
    assert [c.id for c in listed] == [plain.id]
    assert review_conversation.id not in [c.id for c in listed]


# ==================================================================== 评级建议


def test_suggestion_is_returned_but_never_written(db_session: Session) -> None:
    """C7 的 service 证据：建议带回来，但库里一行都不多。"""
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    conversation = start_chat(db_session, provider, knowledge.id)
    answer_once(db_session, provider, conversation.id)

    provider.queue(
        RatingSuggestion(suggested_rating="hard", evaluation="答对了大意，漏了震荡")
    )
    suggestion = ai_service.suggest_review_rating(
        db_session, provider, knowledge.id, conversation.id
    )
    assert suggestion.suggested_rating == "hard"
    assert suggestion.evaluation == "答对了大意，漏了震荡"

    # 评价调用的形状：review_rate 提示词 + 对话原文 + 收尾的理解参考
    request = provider.chat_calls[0]
    assert request[0] == system_message("review_rate")
    transcript = [m.content for m in request[1:-1]]
    assert FIRST_QUESTION in " ".join(transcript)
    assert "会跳过最低点" in " ".join(transcript)
    assert "每步往降最快的方向走一小步" in request[-1].content

    assert provider.remaining() == 0, "排了队的结果必须真的被消费掉"
    assert db_session.scalar(select(func.count(ReviewSession.id))) == 0, "建议不落库"


def test_suggestion_on_empty_conversation_is_rejected(db_session: Session) -> None:
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    empty = ai_service.create_conversation(db_session, "还没聊")

    with pytest.raises(ValueError, match="还没有内容"):
        ai_service.suggest_review_rating(
            db_session, provider, knowledge.id, empty.id
        )
    assert provider.chat_calls == [], "空对话不该花 AI 请求费"


def test_suggestion_with_malformed_ai_output_writes_nothing(
    db_session: Session,
) -> None:
    """C3：评级建议也是 AI 输出，畸形值（词表外）必须被校验拦住。"""
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    conversation = start_chat(db_session, provider, knowledge.id)
    answer_once(db_session, provider, conversation.id)

    provider.queue('{"suggested_rating": "简单", "evaluation": "不错"}')
    with pytest.raises(AiSchemaInvalid):
        ai_service.suggest_review_rating(
            db_session, provider, knowledge.id, conversation.id
        )
    assert db_session.scalar(select(func.count(ReviewSession.id))) == 0


# ==================================================================== 评级落到 review_now


def test_review_now_stores_conversation_link_and_duration(db_session: Session) -> None:
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    conversation = start_chat(db_session, provider, knowledge.id)

    outcome = review_service.review_now(
        db_session,
        knowledge.id,
        "good",
        mode="ai_chat",
        suggested_rating="hard",
        ai_comment="AI 的评价",
        conversation_id=conversation.id,
        duration_ms=90_000,
    )
    assert outcome.knowledge_id == knowledge.id

    row = db_session.scalars(select(ReviewSession)).one()
    assert row.conversation_id == conversation.id
    assert row.duration_ms == 90_000
    assert row.suggested_rating == "hard", "建议进存档列"
    assert row.rating == "good", "权威评级只来自用户"


def test_review_now_rejects_unknown_conversation(db_session: Session) -> None:
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    start_chat(db_session, provider, knowledge.id)

    with pytest.raises(ReviewConversationNotFound):
        review_service.review_now(
            db_session,
            knowledge.id,
            "good",
            mode="ai_chat",
            conversation_id="不存在的对话",
        )


def test_review_now_rejects_negative_duration(db_session: Session) -> None:
    knowledge = make_knowledge(db_session)
    with pytest.raises(ValueError, match="duration_ms"):
        review_service.review_now(
            db_session, knowledge.id, "good", mode="self_test", duration_ms=-1
        )


# ==================================================================== 约束锁定（⑦ 补）


def test_full_ai_review_cycle_never_touches_user_understanding(
    db_session: Session,
) -> None:
    """C1 在复习场景的落点：一整场 AI 复习走完（开场、追问、建议、评级落库），
    用户的原文一个字节都不许变，版本数也不许涨。"""
    provider = FakeProvider()
    knowledge = make_knowledge(db_session)
    original = knowledge.current_version.user_understanding

    conversation = start_chat(db_session, provider, knowledge.id)
    answer_once(db_session, provider, conversation.id)
    provider.queue(RatingSuggestion(suggested_rating="hard", evaluation="还行"))
    ai_service.suggest_review_rating(db_session, provider, knowledge.id, conversation.id)
    review_service.review_now(
        db_session,
        knowledge.id,
        "again",
        mode="ai_chat",
        suggested_rating="hard",
        ai_comment="还行",
        conversation_id=conversation.id,
    )

    db_session.expire_all()
    fresh = db_session.get(Knowledge, knowledge.id)
    assert fresh.current_version.user_understanding == original
    assert len(fresh.versions) == 1, "复习流程不该产生新版本"


def test_suggestion_request_carries_no_other_knowledge(db_session: Session) -> None:
    """C5：评级建议请求 = 提示词 + 这场对话的原文 + 这条知识的理解参考，
    库里其他知识的内容一个字都不许出现。"""
    provider = FakeProvider()
    make_knowledge(db_session, title="不该出现的知识")
    knowledge = make_knowledge(db_session)
    conversation = start_chat(db_session, provider, knowledge.id)
    answer_once(db_session, provider, conversation.id)

    provider.queue(RatingSuggestion(suggested_rating="good", evaluation="行"))
    ai_service.suggest_review_rating(db_session, provider, knowledge.id, conversation.id)

    body = "\n".join(message.content for message in provider.chat_calls[0])
    assert "不该出现的知识" not in body
