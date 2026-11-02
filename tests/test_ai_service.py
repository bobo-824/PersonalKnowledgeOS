"""阶段 2 第⑥步：AI 业务编排。

重点测的不是"函数能不能跑通"，而是三条硬约束在**这一层**有没有落到位：
C1（AI 永不写 user_understanding）、C2（草稿不落库）、C3（畸形输出不落库），
外加 C5（最小必要传输）和阶段 1 踩过的那两个坑在 AI 路径上有没有复发。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.db.models import (
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_USER,
    AiConversation,
    AiMessage,
    AiReview,
    AiReviewDecision,
    IdempotencyKey,
    Knowledge,
    KnowledgeVersion,
    Source,
)
from backend.schemas.ai import KnowledgeDraft, ReviewResult
from backend.schemas.knowledge import KnowledgeCreate
from backend.services import ai_service
from backend.services.ai.errors import AiSchemaInvalid, AiUnavailable
from backend.services.ai.fake import FakeProvider
from backend.services.ai.prompts import PROMPT_VERSION
from backend.services.knowledge_service import create_knowledge

DRAFT_JSON = {
    "title": "梯度下降",
    "domain": "机器学习",
    "summary": "沿负梯度方向更新参数",
    "user_understanding": "我自己说的话：每步往下降最快的方向走一小步",
    "ai_supplements": ["AI 补的一句：学习率太大会跳过最低点"],
    "reasoning": "因为梯度指向增长最快的方向",
    "open_questions": "非凸问题上会怎样？",
    "suggested_sources": [
        {"source_type": "course", "title": "统计学习方法", "url_or_reference": None, "note": "第三章"}
    ],
}

REVIEW_JSON = {
    "correct_points": [{"point": "方向选对了", "why": "梯度确实指向最陡上升方向"}],
    "errors": [
        {"claim": "每步往下降最快的方向走一小步", "correction": "是负梯度方向", "evidence": "梯度定义"}
    ],
    "omissions": [{"missing": "学习率的影响", "why_it_matters": "选错会不收敛"}],
    "follow_up_questions": ["局部最小和全局最小有什么区别？"],
    "needs_verification": [{"item": "收敛速度", "how_to_verify": "查凸性条件下的收敛率"}],
}


def make_knowledge(session: Session, title: str, understanding: str) -> Knowledge:
    return create_knowledge(
        session,
        KnowledgeCreate.model_validate(
            {"title": title, "user_understanding": understanding, "domain": "机器学习"}
        ),
    )


def fts_body(session: Session, knowledge_id: str) -> str:
    """直接读 FTS 表里的正文——这是"索引同步对不对"最不加中介的检查方式。"""
    return (
        session.execute(
            text("SELECT body FROM knowledge_fts WHERE knowledge_id = :kid"),
            {"kid": knowledge_id},
        )
        .scalar()
        or ""
    )


# =================================================================== 会话


def test_conversation_title_comes_from_the_user_not_the_ai(db_session: Session) -> None:
    """不让 AI 起名：那要多发一次付费调用，而且对话还没聊完就得先等它。"""
    conversation = ai_service.create_conversation(db_session, "我想搞懂梯度下降到底在干什么")
    assert conversation.title.startswith("我想搞懂梯度下降")
    # 标题来源是一次用户消息，不是一次 AI 调用
    assert FakeProvider().remaining() == 0


def test_conversation_title_falls_back_when_first_message_is_blank(db_session: Session) -> None:
    conversation = ai_service.create_conversation(db_session, "   \n  ")
    assert conversation.title == "新的对话"


def test_stream_reply_persists_both_sides(db_session: Session) -> None:
    provider = FakeProvider().queue_stream(["梯度", "下降", "就是"])
    conversation = ai_service.create_conversation(db_session, "什么是梯度下降")

    chunks = list(ai_service.stream_reply(db_session, provider, conversation.id, "什么是梯度下降"))

    assert chunks == ["梯度", "下降", "就是"]
    messages = ai_service.list_messages(db_session, conversation.id)
    assert [(m.role, m.seq, m.content) for m in messages] == [
        (MESSAGE_ROLE_USER, 1, "什么是梯度下降"),
        (MESSAGE_ROLE_ASSISTANT, 2, "梯度下降就是"),
    ]
    # 存的是拼好的完整文本，不是碎片
    assert messages[1].content == "梯度下降就是"
    assert messages[1].model == "fake-model"
    assert messages[1].error_kind is None


def test_failed_reply_still_keeps_the_users_message(db_session: Session) -> None:
    """⚠️ 用户那句必须先提交。AI 挂了不能把他的输入一起丢掉。"""
    provider = FakeProvider().queue_stream(AiUnavailable())
    conversation = ai_service.create_conversation(db_session, "甲")

    with pytest.raises(AiUnavailable):
        list(ai_service.stream_reply(db_session, provider, conversation.id, "我的问题原文"))

    messages = ai_service.list_messages(db_session, conversation.id)
    assert [m.role for m in messages] == [MESSAGE_ROLE_USER, MESSAGE_ROLE_ASSISTANT]
    assert messages[0].content == "我的问题原文"
    # 助手那条存的是固定错误文案，不是响应体（C6）
    assert messages[1].content == AiUnavailable.user_message
    assert messages[1].error_kind == "unavailable"


def test_message_seq_is_max_plus_one(db_session: Session) -> None:
    """和版本号同一条规则（D-030）。按 len+1 算的话，删掉中间一条就会算出已用过的号：
    这里删掉 seq=2 后剩 3 条，len+1 会算出 4，而 4 已经被占了，直接撞唯一约束。"""
    provider = FakeProvider()
    conversation = ai_service.create_conversation(db_session, "甲")
    for _ in range(2):
        provider.queue_stream(["回"])
        list(ai_service.stream_reply(db_session, provider, conversation.id, "问"))

    db_session.execute(text("DELETE FROM ai_messages WHERE seq = 2"))
    db_session.commit()

    provider.queue_stream(["再来"])
    list(ai_service.stream_reply(db_session, provider, conversation.id, "问"))

    seqs = [m.seq for m in ai_service.list_messages(db_session, conversation.id)]
    # 删掉 seq=2 之后剩 3 条（1、3、4）。len+1 会算出 4，而 4 已被占用。
    assert seqs == [1, 3, 4, 5, 6], "序号被复用或倒退了"


def test_updated_at_bumps_on_each_message(db_session: Session) -> None:
    """历史列表按 updated_at 倒序排；不顶这个值，最活跃的会话就沉底了。"""
    provider = FakeProvider()
    old = ai_service.create_conversation(db_session, "先建的")
    fresh = ai_service.create_conversation(db_session, "后建的")
    before = fresh.updated_at

    provider.queue_stream(["答"])
    list(ai_service.stream_reply(db_session, provider, fresh.id, "问"))
    db_session.expire_all()

    after = db_session.get(AiConversation, fresh.id).updated_at
    assert after > before
    assert ai_service.list_conversations(db_session)[0].id == fresh.id
    assert old.id != fresh.id


def test_context_excludes_failed_messages(db_session: Session) -> None:
    """把「无法连接 AI 服务」当对话历史再发回去，只会让模型继续困惑。"""
    provider = FakeProvider()
    conversation = ai_service.create_conversation(db_session, "甲")

    provider.queue_stream(AiUnavailable())
    with pytest.raises(AiUnavailable):
        list(ai_service.stream_reply(db_session, provider, conversation.id, "第一次问"))

    provider.queue_stream(["正常回答"])
    list(ai_service.stream_reply(db_session, provider, conversation.id, "第二次问"))

    provider.queue_stream(["第三次"])
    list(ai_service.stream_reply(db_session, provider, conversation.id, "第三次问"))

    sent = provider.stream_calls[-1]
    joined = "\n".join(m.content for m in sent)
    # 成功的助手回复**必须**在上下文里——不然下面那条"不在"就是假通过
    assert "正常回答" in joined
    assert "第二次问" in joined
    # 失败那条（content 是我们的错误文案）不带上
    assert AiUnavailable.user_message not in joined
    assert "第一次问" in joined
    # system + 4 条业务消息 = 5
    assert len(sent) == 5, [m.content[:12] for m in sent]


def test_context_is_capped(db_session: Session) -> None:
    """上下文越长越贵、越容易抓不住重点，所以要封顶。"""
    provider = FakeProvider()
    conversation = ai_service.create_conversation(db_session, "甲")
    for _ in range(ai_service.MAX_CONTEXT_MESSAGES + 10):
        provider.queue_stream(["答"])
        list(ai_service.stream_reply(db_session, provider, conversation.id, "问"))

    sent = provider.stream_calls[-1]
    # 一条 system + 最多 N 条历史
    assert len(sent) <= ai_service.MAX_CONTEXT_MESSAGES + 1
    assert sent[0].role == "system"


# ================================================================= 清空历史


def test_clear_history_removes_everything_but_knowledge(db_session: Session) -> None:
    """D-041 的验收项。删的是对话，不是知识。"""
    provider = FakeProvider()
    make_knowledge(db_session, "必须活下来", "我的理解原文")
    conversation = ai_service.create_conversation(db_session, "甲")
    provider.queue_stream(["答"])
    list(ai_service.stream_reply(db_session, provider, conversation.id, "问"))

    deleted = ai_service.clear_conversation_history(db_session)

    assert deleted == 1
    assert db_session.scalar(select(AiConversation)) is None
    assert db_session.scalar(select(AiMessage)) is None
    # 知识与版本一行都没少——级联只带走对话
    assert db_session.scalar(select(func.count(Knowledge.id))) == 1
    assert db_session.scalar(select(func.count(KnowledgeVersion.id))) == 1


def test_clear_history_returns_zero_when_nothing_to_delete(db_session: Session) -> None:
    assert ai_service.clear_conversation_history(db_session) == 0


# ================================================================= 提取草稿


def test_draft_never_touches_the_database(db_session: Session) -> None:
    """⚠️ C2 的核心：草稿阶段不落库。用户中途关页面，什么都不留。"""
    provider = FakeProvider().queue(DRAFT_JSON)
    conversation = ai_service.create_conversation(db_session, "聊聊梯度下降")
    provider.queue_stream(["你补一句：学习率很关键"])
    list(ai_service.stream_reply(db_session, provider, conversation.id, "我大概懂了"))

    draft = ai_service.draft_from_conversation(db_session, provider, conversation.id)

    assert isinstance(draft, KnowledgeDraft)
    # 提取请求带上了这段对话的上下文（用户句 + 助手句），且第一条是系统提示词
    sent = provider.chat_calls[0]
    assert sent[0].role == "system"
    joined = "\n".join(m.content for m in sent)
    assert "我大概懂了" in joined
    assert "你补一句：学习率很关键" in joined
    assert db_session.scalar(select(Knowledge)) is None
    assert db_session.scalar(select(KnowledgeVersion)) is None
    assert db_session.scalar(select(Source)) is None
    assert db_session.scalar(select(IdempotencyKey)) is None


def test_malformed_draft_is_not_saved(db_session: Session) -> None:
    """C3：AI 返回不合契约，一行都不许多。"""
    provider = FakeProvider().queue("我觉得这个概念很有意思")
    conversation = ai_service.create_conversation(db_session, "聊聊梯度下降")

    with pytest.raises(AiSchemaInvalid):
        ai_service.draft_from_conversation(db_session, provider, conversation.id)

    assert db_session.scalar(select(Knowledge)) is None


def test_confirm_draft_with_blank_understanding_is_rejected(db_session: Session) -> None:
    """空理解不是"AI 提的"，是"还没形成"——不能就这么存进去。"""
    payload = dict(DRAFT_JSON, user_understanding="")
    draft = KnowledgeDraft.model_validate(payload)

    with pytest.raises(ai_service.EmptyDraftError):
        ai_service.confirm_draft(db_session, draft, idempotency_key="key-blank")

    assert db_session.scalar(select(Knowledge)) is None
    assert db_session.scalar(select(IdempotencyKey)) is None


def test_confirm_draft_creates_one_knowledge_with_conversation_source(db_session: Session) -> None:
    draft = KnowledgeDraft.model_validate(DRAFT_JSON)
    knowledge = ai_service.confirm_draft(db_session, draft, idempotency_key="key-1")

    assert knowledge.source_type == "conversation"
    assert knowledge.current_version.version_number == 1
    assert knowledge.current_version.created_by == "user"
    assert knowledge.sources[0].source_type == "course"
    assert knowledge.current_version.user_understanding.startswith("我自己说的话")


def test_ai_supplements_never_enter_the_saved_understanding(db_session: Session) -> None:
    """⚠️ C1 最容易悄悄失守的地方：只要第⑥步把 ai_supplements 拼到理解后面，
    功能上看起来一切正常，但这条记录就说不清是谁说的了。"""
    draft = KnowledgeDraft.model_validate(DRAFT_JSON)
    knowledge = ai_service.confirm_draft(db_session, draft, idempotency_key="key-c1")

    assert "AI 补的一句" not in knowledge.current_version.user_understanding
    assert knowledge.current_version.user_understanding == DRAFT_JSON["user_understanding"]


def test_confirm_draft_is_idempotent(db_session: Session) -> None:
    """同一个幂等键提交两次只能有一条知识（Blueprint 阶段 2 验收项）。
    挡的是超时重发和刷新后重点——按钮 disabled 挡不住这两个。"""
    draft = KnowledgeDraft.model_validate(DRAFT_JSON)
    first = ai_service.confirm_draft(db_session, draft, idempotency_key="same-key")
    second = ai_service.confirm_draft(db_session, draft, idempotency_key="same-key")

    assert second.id == first.id
    assert db_session.scalar(select(IdempotencyKey)) is not None
    assert len(db_session.scalars(select(Knowledge)).all()) == 1


def test_different_keys_create_different_knowledge(db_session: Session) -> None:
    draft = KnowledgeDraft.model_validate(DRAFT_JSON)
    ai_service.confirm_draft(db_session, draft, idempotency_key="key-a")
    ai_service.confirm_draft(db_session, draft, idempotency_key="key-b")
    assert len(db_session.scalars(select(Knowledge)).all()) == 2


def test_confirmed_draft_is_searchable(db_session: Session) -> None:
    """落库要走完 FTS 同步。索引没同步的话记录存在却搜不到，
    而数据库里看着完全正常——阶段 1 实测踩过的坑。"""
    draft = KnowledgeDraft.model_validate(DRAFT_JSON)
    knowledge = ai_service.confirm_draft(db_session, draft, idempotency_key="key-fts")
    assert "每步往下降最快的方向走一小步" in fts_body(db_session, knowledge.id)


# ================================================================= 知识审查


def test_review_lands_in_its_own_table(db_session: Session) -> None:
    """⚠️ 审查结果独立存储，knowledge_versions 的现有行一个字都不许改。"""
    knowledge = make_knowledge(db_session, "过拟合", "模型记住了训练数据的噪声")
    v1 = knowledge.current_version
    original_v1_text = v1.user_understanding

    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, knowledge.id)

    assert isinstance(review, AiReview)
    assert review.version_id == v1.id
    assert review.prompt_version == PROMPT_VERSION
    assert review.model == "fake-model"
    assert ReviewResult.model_validate_json(review.result_json).errors

    db_session.expire_all()
    reloaded_v1 = db_session.get(KnowledgeVersion, v1.id)
    assert reloaded_v1.user_understanding == original_v1_text
    assert reloaded_v1.ai_revision is None
    assert review.id != v1.id


def test_review_sends_only_the_current_versions_relevant_fields(db_session: Session) -> None:
    """⚠️ C5 最小必要传输。这里断言的是**实际发出去的内容**，不是注释里的承诺。

    构造一份"别的东西都不该被发出去"的场景：旧版本正文、来源标题、
    另一条知识，全都不能出现在请求里。
    """
    knowledge = make_knowledge(db_session, "过拟合", "第一版：泛化能力下降")
    from backend.services.knowledge_service import update_knowledge
    from backend.schemas.knowledge import KnowledgeUpdate

    update_knowledge(
        db_session,
        knowledge.id,
        KnowledgeUpdate.model_validate({"user_understanding": "第二版：记住了训练数据的噪声"}),
    )
    # 来源和另一条知识，都不该被发出去
    db_session.add(
        Source(knowledge_id=knowledge.id, source_type="paper", title="《绝对不该被发出去的那篇论文》")
    )
    make_knowledge(db_session, "反向传播", "另一条知识：链式法则")
    db_session.commit()

    provider = FakeProvider().queue(REVIEW_JSON)
    ai_service.review_knowledge(db_session, provider, knowledge.id)

    sent = provider.chat_calls[0]
    assert [m.role for m in sent] == ["system", "user"]
    user_payload = sent[1].content

    assert "第二版：记住了训练数据的噪声" in user_payload
    assert "过拟合" in user_payload
    # 旧版本正文不发
    assert "第一版：泛化能力下降" not in user_payload
    # 来源全文不发
    assert "绝对不该被发出去的那篇论文" not in user_payload
    # 其他知识不发
    assert "链式法则" not in user_payload
    # 全库不发
    assert "反向传播" not in user_payload


def test_review_rejects_items_that_were_never_shown(db_session: Session) -> None:
    """处置要能对应到真实存在的条目。指向不存在的条目会被静默丢掉，
    用户以为记下了、其实没有。"""
    knowledge = make_knowledge(db_session, "过拟合", "我的理解")
    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, knowledge.id)

    with pytest.raises(ai_service.ReviewItemNotFound):
        ai_service.decide_review_item(db_session, review.id, "errors", 9, "accepted")
    with pytest.raises(ai_service.ReviewItemNotFound):
        ai_service.decide_review_item(db_session, review.id, "not_a_category", 0, "accepted")


def test_review_item_can_be_overtyped_not_duplicated(db_session: Session) -> None:
    """改主意是覆盖。插新行会让同一条目有两个互相矛盾的处置。"""
    knowledge = make_knowledge(db_session, "过拟合", "我的理解")
    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, knowledge.id)

    ai_service.decide_review_item(db_session, review.id, "errors", 0, "accepted")
    latest = ai_service.decide_review_item(db_session, review.id, "errors", 0, "disagreed")

    decisions = db_session.scalars(
        select(AiReviewDecision).where(AiReviewDecision.review_id == review.id)
    ).all()
    assert len(decisions) == 1
    assert latest.decision == "disagreed"


def test_review_lookup_rejects_unknown_id(db_session: Session) -> None:
    with pytest.raises(ai_service.ReviewNotFound):
        ai_service.get_review(db_session, "不存在")


def test_accept_ai_review_appends_a_version_without_touching_the_original(db_session: Session) -> None:
    """⚠️ 阶段 1 那两个坑在 AI 路径上的复现检查：
    1. 只改外键不改关系属性 → sync_index 读到旧版本，索引里是过时正文；
    2. 完全不查索引 → 改了理解却搜不到新内容。
    所以这里必须直接读 FTS 表里的正文。
    """
    knowledge = make_knowledge(db_session, "过拟合", "模型记住了训练数据的噪声")
    v1 = knowledge.current_version
    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, knowledge.id)
    ai_service.decide_review_item(db_session, review.id, "errors", 0, "accepted")

    updated = ai_service.accept_ai_review(
        db_session,
        knowledge_id=knowledge.id,
        review_id=review.id,
        new_understanding="泛化误差和训练误差的差距才是判断依据",
    )

    assert updated.current_version.version_number == 2
    assert updated.current_version.created_by == "ai_confirmed"
    assert updated.current_version.change_reason == "采纳 AI 审查建议"
    # 索引里必须是**新版**正文，不是 v1
    body = fts_body(db_session, knowledge.id)
    assert "泛化误差和训练误差的差距才是判断依据" in body
    assert "模型记住了训练数据的噪声" not in body

    db_session.expire_all()
    reloaded_v1 = db_session.get(KnowledgeVersion, v1.id)
    assert reloaded_v1.user_understanding == "模型记住了训练数据的噪声"
    assert reloaded_v1.created_by == "user"


def test_ai_revision_keeps_the_accepted_words_verbatim(db_session: Session) -> None:
    """AI_WORKFLOWS 第四节：三个月后回看要知道"AI 当时说了什么"。
    只留用户**点过采纳**的那些，一字不改。"""
    knowledge = make_knowledge(db_session, "过拟合", "我的原话")
    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, knowledge.id)
    ai_service.decide_review_item(db_session, review.id, "errors", 0, "accepted")
    ai_service.decide_review_item(db_session, review.id, "omissions", 0, "ignored")

    updated = ai_service.accept_ai_review(
        db_session,
        knowledge_id=knowledge.id,
        review_id=review.id,
        new_understanding="改成负梯度方向才准确",
    )

    revision = updated.current_version.ai_revision
    assert "是负梯度方向" in revision
    assert "梯度定义" in revision
    # 被忽略的那条不进留档
    assert "学习率的影响" not in revision


def test_accepting_does_not_put_ai_words_into_user_understanding(db_session: Session) -> None:
    """C1 的正面断言：AI 原话只进 ai_revision，用户理解只来自入参。"""
    knowledge = make_knowledge(db_session, "过拟合", "我自己的话")
    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, knowledge.id)
    ai_service.decide_review_item(db_session, review.id, "errors", 0, "accepted")

    updated = ai_service.accept_ai_review(
        db_session,
        knowledge_id=knowledge.id,
        review_id=review.id,
        new_understanding="我自己重新写的一句话",
    )

    assert updated.current_version.user_understanding == "我自己重新写的一句话"
    assert "是负梯度方向" not in updated.current_version.user_understanding


def test_accepting_without_editing_creates_no_version(db_session: Session) -> None:
    """D-030：理解没真的变就不该多一个版本，否则会出现"点了保存版本号却 +1"的假历史。"""
    knowledge = make_knowledge(db_session, "过拟合", "一个字都没动的原话")
    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, knowledge.id)
    ai_service.decide_review_item(db_session, review.id, "errors", 0, "accepted")

    updated = ai_service.accept_ai_review(
        db_session,
        knowledge_id=knowledge.id,
        review_id=review.id,
        new_understanding="一个字都没动的原话",
    )

    assert updated.current_version.version_number == 1
    assert len(updated.versions) == 1


def test_accept_requires_understanding_of_that_same_knowledge(db_session: Session) -> None:
    """把 A 的审查结果采纳到 B 上是错用，必须拒掉而不是静默产生脏数据。"""
    first = make_knowledge(db_session, "过拟合", "甲")
    second = make_knowledge(db_session, "梯度下降", "乙")
    provider = FakeProvider().queue(REVIEW_JSON)
    review = ai_service.review_knowledge(db_session, provider, first.id)

    with pytest.raises(ai_service.ReviewNotFound):
        ai_service.accept_ai_review(
            db_session,
            knowledge_id=second.id,
            review_id=review.id,
            new_understanding="不该成功",
        )
    assert len(second.versions) == 1


def test_result_json_is_stored_as_the_validated_dump(db_session: Session) -> None:
    """存的是**校验后的转储**，不是 AI 原文。
    多余字段因此注定落不下来（D-047），而这正是 item_index 寻址能成立的前提。"""
    knowledge = make_knowledge(db_session, "过拟合", "甲")
    payload = dict(REVIEW_JSON, overall_score=88)
    provider = FakeProvider().queue(json.dumps(payload, ensure_ascii=False))

    review = ai_service.review_knowledge(db_session, provider, knowledge.id)
    stored = json.loads(review.result_json)

    assert "overall_score" not in stored
    assert set(stored) == {
        "correct_points",
        "errors",
        "omissions",
        "follow_up_questions",
        "needs_verification",
    }


def test_malformed_review_writes_nothing(db_session: Session) -> None:
    """C3 端到端：AI 返回不合契约 → 审查记录一条都不产生。"""
    knowledge = make_knowledge(db_session, "过拟合", "甲")
    provider = FakeProvider().queue('{"errors": [{"claim": "缺了 correction 和 evidence"}]}')

    with pytest.raises(AiSchemaInvalid):
        ai_service.review_knowledge(db_session, provider, knowledge.id)

    assert db_session.scalar(select(AiReview)) is None
