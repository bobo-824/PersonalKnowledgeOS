"""迁移的结构保证（0001 知识库 + 0002 AI）。

这些测试盯的是 Blueprint 里几条硬验收项：
原文不被覆盖、追加式版本、外键真的生效、FTS5 真的建出来了、迁移可回滚。
0002 增加的部分盯的是：AI 数据独立存储、数据库级级联真的生效、
幂等键真的能挡住重复提交。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from conftest import make_alembic_config

from backend.config import get_settings
from backend.db.base import utcnow
from backend.db.models import (
    AiConversation,
    AiMessage,
    AiReview,
    AiReviewDecision,
    IdempotencyKey,
    Knowledge,
    KnowledgeVersion,
    ReviewSchedule,
    ReviewSession,
)

CORE_TABLES = ["alembic_version", "knowledge", "knowledge_versions", "sources"]
AI_TABLES = [
    "ai_conversations",
    "ai_messages",
    "ai_reviews",
    "ai_review_decisions",
    "idempotency_keys",
]
REVIEW_TABLES = ["review_schedules", "review_sessions"]


def table_names(session: Session) -> set[str]:
    rows = session.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
    return {row[0] for row in rows}


def test_core_tables_exist(db_session: Session) -> None:
    present = table_names(db_session)
    missing = [name for name in CORE_TABLES if name not in present]
    assert not missing, f"缺少表：{missing}"


def test_fts5_virtual_table_created(db_session: Session) -> None:
    """中文检索依赖 FTS5 虚拟表，它不在 ORM metadata 里，只能直接查 sqlite_master。"""
    ddl = db_session.execute(
        text("SELECT sql FROM sqlite_master WHERE name='knowledge_fts'")
    ).scalar_one_or_none()

    assert ddl is not None, "knowledge_fts 没有建出来"
    assert "fts5" in ddl.lower()
    assert "trigram" in ddl.lower(), "分词器必须是 trigram，否则中文搜不到"

    # FTS5 会自动生成影子表，缺了说明建表不完整
    present = table_names(db_session)
    for shadow in ("knowledge_fts_data", "knowledge_fts_idx", "knowledge_fts_config"):
        assert shadow in present, f"缺少 FTS5 影子表：{shadow}"


def test_alembic_current_matches_head(db_session: Session) -> None:
    current = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    heads = list(ScriptDirectory.from_config(make_alembic_config()).get_heads())
    assert current in heads, f"当前版本 {current} 不是最新 {heads}"


def test_version_number_unique_per_knowledge(db_session: Session) -> None:
    """同一条知识的版本号不能重复，否则版本历史会出现歧义。"""
    knowledge = Knowledge(title="自注意力机制", domain="深度学习")
    db_session.add(knowledge)
    db_session.flush()

    db_session.add_all(
        [
            KnowledgeVersion(
                knowledge_id=knowledge.id, version_number=1, user_understanding="第一版理解"
            ),
            KnowledgeVersion(
                knowledge_id=knowledge.id, version_number=1, user_understanding="重复版本号"
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_keys_are_enforced(db_session: Session) -> None:
    """SQLite 默认不强制外键，session.py 里显式打开了 PRAGMA foreign_keys。"""
    assert db_session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    db_session.add(
        KnowledgeVersion(
            knowledge_id="不存在的知识ID", version_number=1, user_understanding="孤儿记录"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_circular_fk_current_version_backfill(db_session: Session) -> None:
    """knowledge ↔ knowledge_versions 是循环外键，插入时要能正确回填。"""
    knowledge = Knowledge(title="梯度下降", domain="机器学习")
    db_session.add(knowledge)
    # UUID 主键的 default 要到 flush 时才生成，必须先 flush 才能拿到 id
    db_session.flush()

    version = KnowledgeVersion(
        knowledge_id=knowledge.id, version_number=1, user_understanding="沿负梯度方向更新参数"
    )
    db_session.add(version)
    # 挂上 relationship，让 SQLAlchemy 用 post_update 回填 current_version_id
    knowledge.current_version = version
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(Knowledge, knowledge.id)
    assert reloaded.current_version_id == version.id
    assert reloaded.current_version.user_understanding == "沿负梯度方向更新参数"


def test_bogus_current_version_rejected(db_session: Session) -> None:
    knowledge = Knowledge(title="反向传播", current_version_id="不存在的版本ID")
    db_session.add(knowledge)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_naive_datetime_rejected(db_session: Session) -> None:
    """Blueprint 9.2：时区错乱会让复习日期算错，所以在写库那一刻就拦下来。"""
    knowledge = Knowledge(title="测试知识")
    db_session.add(knowledge)
    db_session.flush()

    version = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding="不带时区的时间",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    db_session.add(version)
    # UTCDateTime 抛的是 ValueError，SQLAlchemy 会把它包成 StatementError 再抛出来
    with pytest.raises(StatementError, match="不带时区") as excinfo:
        db_session.commit()
    assert isinstance(excinfo.value.__cause__, ValueError)
    db_session.rollback()


def test_aware_datetime_roundtrips_as_utc(db_session: Session) -> None:
    knowledge = Knowledge(title="测试知识UTC")
    db_session.add(knowledge)
    db_session.flush()

    utc_noon = datetime(2026, 5, 1, 21, 0, 0, tzinfo=timezone.utc)
    version = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding="带时区的时间",
        created_at=utc_noon,
    )
    db_session.add(version)
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(KnowledgeVersion, version.id)
    assert reloaded.created_at.tzinfo is not None
    assert reloaded.created_at == utc_noon


def test_original_understanding_survives_new_version(db_session: Session) -> None:
    """Blueprint 7.1 硬验收项：用户原文不会被后续版本或 AI 建议覆盖。"""
    knowledge = Knowledge(title="过拟合", domain="机器学习")
    db_session.add(knowledge)
    db_session.flush()

    v1 = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding="模型在训练集上表现好，在新数据上表现差",
        reasoning="我按字面意思理解的",
        open_questions="为什么参数多就会这样？",
    )
    db_session.add(v1)
    knowledge.current_version = v1
    db_session.commit()

    # 用户采纳 AI 建议后生成新版本，ai_revision 单独存放
    v2 = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=2,
        user_understanding="模型记住了训练数据的噪声，导致泛化能力下降",
        ai_revision="建议补充：泛化误差与训练误差的差距是判断依据",
        created_by="ai_confirmed",
        change_reason="采纳 AI 审查建议",
    )
    db_session.add(v2)
    db_session.flush()
    knowledge.current_version_id = v2.id
    db_session.commit()

    db_session.expire_all()
    reloaded_v1 = db_session.get(KnowledgeVersion, v1.id)
    assert reloaded_v1.user_understanding == "模型在训练集上表现好，在新数据上表现差"
    assert reloaded_v1.reasoning == "我按字面意思理解的"
    assert reloaded_v1.open_questions == "为什么参数多就会这样？"

    reloaded_knowledge = db_session.get(Knowledge, knowledge.id)
    assert reloaded_knowledge.current_version_id == v2.id
    assert [v.version_number for v in reloaded_knowledge.versions] == [1, 2]


def test_downgrade_upgrade_roundtrip_leaves_no_residue(migrated_database) -> None:
    """迁移必须可回滚，且回滚后不留 FTS5 影子表之类的残渣。"""
    url = get_settings().database_url

    def snapshot() -> set[str]:
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
                return {row[0] for row in rows}
        finally:
            engine.dispose()

    assert "knowledge_fts" in snapshot()

    config = make_alembic_config()
    command.downgrade(config, "base")

    # alembic_version 表本身会保留（Alembic 只清空版本行），
    # 其余业务表必须全部消失。这里**不按表名前缀白名单过滤**——
    # 过滤条件写死的话，以后加一张新表忘了改这里，残渣就检测不到了。
    leftover = sorted(
        name
        for name in snapshot()
        if name != "alembic_version" and not name.startswith("sqlite_")
    )
    assert not leftover, f"downgrade 之后残留了表：{leftover}"

    command.upgrade(config, "head")

    restored = snapshot()
    for name in (*CORE_TABLES, *AI_TABLES, *REVIEW_TABLES, "knowledge_fts"):
        assert name in restored, f"重新 upgrade 后缺少 {name}"

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()
    # 不写死版本号：断言"等于当前 head"，加迁移时这条测试不用改
    heads = list(ScriptDirectory.from_config(config).get_heads())
    assert version in heads, f"回滚再升级后版本 {version} 不是最新 {heads}"


def test_utcnow_is_timezone_aware() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------- 迁移 0002


def make_version(session: Session, title: str = "过拟合") -> KnowledgeVersion:
    """建一条带 v1 的知识，供审查和幂等键的外键测试复用。"""
    knowledge = Knowledge(title=title, domain="机器学习")
    session.add(knowledge)
    session.flush()
    version = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding="模型记住了训练数据里的噪声",
    )
    session.add(version)
    knowledge.current_version = version
    session.commit()
    return version


def make_review(session: Session, version: KnowledgeVersion) -> AiReview:
    review = AiReview(
        knowledge_id=version.knowledge_id,
        version_id=version.id,
        model="fake-model",
        prompt_version="v1",
        result_json=json.dumps(
            {
                "correct_points": [],
                "errors": [],
                "omissions": [],
                "follow_up_questions": [],
                "needs_verification": [],
            }
        ),
    )
    session.add(review)
    session.commit()
    return review


def test_ai_tables_exist(db_session: Session) -> None:
    present = table_names(db_session)
    missing = [name for name in AI_TABLES if name not in present]
    assert not missing, f"缺少表：{missing}"


def test_ai_message_seq_unique_per_conversation(db_session: Session) -> None:
    """会话内序号重复会让消息顺序出现歧义，必须被唯一约束挡住。"""
    conversation = AiConversation(title="聊聊梯度下降")
    db_session.add(conversation)
    db_session.flush()

    db_session.add_all(
        [
            AiMessage(
                conversation_id=conversation.id, seq=1, role="user", content="什么是梯度下降"
            ),
            AiMessage(
                conversation_id=conversation.id, seq=1, role="assistant", content="重复序号"
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_seq_allowed_across_conversations(db_session: Session) -> None:
    """唯一约束是 (conversation_id, seq)，不是全局 seq——两个会话都从 1 开始。"""
    first = AiConversation(title="会话一")
    second = AiConversation(title="会话二")
    db_session.add_all([first, second])
    db_session.flush()

    db_session.add_all(
        [
            AiMessage(conversation_id=first.id, seq=1, role="user", content="甲"),
            AiMessage(conversation_id=second.id, seq=1, role="user", content="乙"),
        ]
    )
    db_session.commit()

    assert db_session.execute(text("SELECT COUNT(*) FROM ai_messages")).scalar_one() == 2


def test_conversation_delete_cascades_at_database_level(db_session: Session) -> None:
    """清空对话历史（D-041）靠的是数据库级 CASCADE，不是 ORM 的 delete-orphan。

    所以这里故意用原生 SQL 删除，绕开 ORM。如果 PRAGMA foreign_keys 没打开、
    或者迁移里漏了 ondelete="CASCADE"，消息就会变成孤儿留在这里。
    """
    conversation = AiConversation(title="待清空的会话")
    db_session.add(conversation)
    db_session.flush()
    db_session.add_all(
        [
            AiMessage(conversation_id=conversation.id, seq=1, role="user", content="问"),
            AiMessage(conversation_id=conversation.id, seq=2, role="assistant", content="答"),
        ]
    )
    db_session.commit()
    conversation_id = conversation.id

    db_session.execute(
        text("DELETE FROM ai_conversations WHERE id = :id"), {"id": conversation_id}
    )
    db_session.commit()

    assert db_session.execute(text("SELECT COUNT(*) FROM ai_messages")).scalar_one() == 0


def test_cascaded_conversation_delete_never_touches_knowledge(db_session: Session) -> None:
    """D-041 的验收项：清空对话历史只删对话，任何知识行都不受影响。"""
    version = make_version(db_session, title="必须活下来的知识")
    conversation = AiConversation(title="要清空的会话")
    db_session.add(conversation)
    db_session.flush()
    db_session.add(
        AiMessage(conversation_id=conversation.id, seq=1, role="user", content="问")
    )
    db_session.commit()

    db_session.execute(
        text("DELETE FROM ai_conversations WHERE id = :id"), {"id": conversation.id}
    )
    db_session.commit()

    assert db_session.execute(text("SELECT COUNT(*) FROM knowledge")).scalar_one() == 1
    assert (
        db_session.execute(text("SELECT COUNT(*) FROM knowledge_versions")).scalar_one() == 1
    )
    assert db_session.get(KnowledgeVersion, version.id) is not None


def test_ai_review_decision_unique_per_item(db_session: Session) -> None:
    """一条审查里的一条条目只允许一个处置，改主意是 UPSERT 不是插新行。"""
    version = make_version(db_session)
    review = make_review(db_session, version)

    db_session.add_all(
        [
            AiReviewDecision(
                review_id=review.id, category="errors", item_index=0, decision="accepted"
            ),
            AiReviewDecision(
                review_id=review.id, category="errors", item_index=0, decision="ignored"
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_same_item_index_allowed_across_categories(db_session: Session) -> None:
    """唯一约束是 (review_id, category, item_index)：不同类别的第 0 条互不冲突。"""
    version = make_version(db_session)
    review = make_review(db_session, version)

    db_session.add_all(
        [
            AiReviewDecision(
                review_id=review.id, category="errors", item_index=0, decision="accepted"
            ),
            AiReviewDecision(
                review_id=review.id, category="omissions", item_index=0, decision="ignored"
            ),
        ]
    )
    db_session.commit()

    assert (
        db_session.execute(text("SELECT COUNT(*) FROM ai_review_decisions")).scalar_one() == 2
    )


def test_review_delete_cascades_to_decisions(db_session: Session) -> None:
    version = make_version(db_session)
    review = make_review(db_session, version)
    db_session.add(
        AiReviewDecision(
            review_id=review.id, category="errors", item_index=0, decision="disagreed"
        )
    )
    db_session.commit()

    db_session.execute(text("DELETE FROM ai_reviews WHERE id = :id"), {"id": review.id})
    db_session.commit()

    assert (
        db_session.execute(text("SELECT COUNT(*) FROM ai_review_decisions")).scalar_one() == 0
    )


def test_ai_review_rejects_unknown_version(db_session: Session) -> None:
    """审查必须挂在真实存在的版本上，否则说不清它审的是哪一版理解。"""
    version = make_version(db_session)
    db_session.add(
        AiReview(
            knowledge_id=version.knowledge_id,
            version_id="不存在的版本ID",
            model="fake-model",
            prompt_version="v1",
            result_json="{}",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_idempotency_key_blocks_duplicate(db_session: Session) -> None:
    """Blueprint 阶段 2 验收项：同一个幂等键提交两次只能产生一条知识。"""
    version = make_version(db_session)
    db_session.add(
        IdempotencyKey(key="同一个键", knowledge_id=version.knowledge_id)
    )
    db_session.commit()

    db_session.add(
        IdempotencyKey(key="同一个键", knowledge_id=version.knowledge_id)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_idempotency_key_rejects_unknown_knowledge(db_session: Session) -> None:
    db_session.add(IdempotencyKey(key="孤儿键", knowledge_id="不存在的知识ID"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------- 迁移 0003


def make_schedule(session: Session, title: str = "待复习的知识") -> Knowledge:
    knowledge = Knowledge(title=title, domain="机器学习")
    session.add(knowledge)
    session.flush()
    session.add(
        ReviewSchedule(
            knowledge_id=knowledge.id,
            state="learning",
            step=0,
            due=utcnow(),
        )
    )
    session.commit()
    return knowledge


def test_review_tables_exist(db_session: Session) -> None:
    present = table_names(db_session)
    missing = [name for name in REVIEW_TABLES if name not in present]
    assert not missing, f"缺少表：{missing}"


def test_one_schedule_per_knowledge(db_session: Session) -> None:
    """一条知识只有一份排期——排期是知识的属性，出现两行就是数据坏了。"""
    knowledge = make_schedule(db_session)
    db_session.add(
        ReviewSchedule(
            knowledge_id=knowledge.id, state="learning", step=0, due=utcnow()
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_review_sessions_allow_many_per_knowledge(db_session: Session) -> None:
    """历史记录和排期相反：一条知识要能积累任意多次复习。"""
    knowledge = make_schedule(db_session)
    db_session.add_all(
        [
            ReviewSession(
                knowledge_id=knowledge.id, reviewed_at=utcnow(), rating="good",
                mode="self_test",
            ),
            ReviewSession(
                knowledge_id=knowledge.id, reviewed_at=utcnow(), rating="again",
                mode="ai_chat",
            ),
        ]
    )
    db_session.commit()
    assert (
        db_session.execute(text("SELECT COUNT(*) FROM review_sessions")).scalar_one()
        == 2
    )


def test_review_tables_reject_unknown_knowledge(db_session: Session) -> None:
    db_session.add(
        ReviewSchedule(
            knowledge_id="不存在的知识ID", state="learning", step=0, due=utcnow()
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------- 复习对话承载列（阶段 3 ⑤）


def test_conversation_kind_defaults_to_chat(db_session: Session) -> None:
    """0002 时代的会话没有 kind，迁移的 server_default 要把它们归入学习室。"""
    conversation = AiConversation(title="老会话")
    db_session.add(conversation)
    db_session.commit()
    db_session.expire_all()
    reloaded = db_session.get(AiConversation, conversation.id)
    assert reloaded.kind == "chat"


def test_clearing_conversations_nulls_review_link_but_keeps_the_record(
    db_session: Session,
) -> None:
    """D-041 + ⑤：清空对话历史删得掉问答原文，删不掉用户的评级记录。

    对话用原生 SQL 删（绕开 ORM 的级联设置），只有这样才测得到
    数据库层的 ON DELETE SET NULL 是不是真的建出来了。
    """
    knowledge = Knowledge(title="过拟合", domain="机器学习")
    db_session.add(knowledge)
    db_session.flush()
    conversation = AiConversation(title="复习：过拟合", kind="review")
    db_session.add(conversation)
    db_session.flush()
    session_row = ReviewSession(
        knowledge_id=knowledge.id,
        reviewed_at=datetime.now(timezone.utc),
        rating="good",
        mode="ai_chat",
        conversation_id=conversation.id,
    )
    db_session.add(session_row)
    db_session.commit()

    db_session.execute(text("DELETE FROM ai_conversations"))
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(ReviewSession, session_row.id)
    assert reloaded is not None, "清空对话把评级记录也删了"
    assert reloaded.conversation_id is None
    assert db_session.execute(text("SELECT COUNT(*) FROM ai_messages")).scalar_one() == 0
