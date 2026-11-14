"""review_service 的验收测试（阶段 3 第③步）。

盯的是三条硬规矩在数据层的落点：
- 评级只来自用户，AI 建议不改变排期（C7 / D-061 / D-063）
- 整个模块碰不到 AI（C4 / D-062）——这条由"本文件没有任何 AI import"
  的架构保证，测试只验证纯自测路径完整可用
- 一条知识一份排期，创建即自动进循环（D-060）
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.base import utcnow
from backend.db.models import Knowledge, ReviewSchedule, ReviewSession
from backend.services.knowledge_service import (
    KnowledgeNotFound,
    archive_knowledge,
    create_knowledge,
)
from backend.schemas.knowledge import KnowledgeCreate
from backend.services.review_service import (
    KnowledgeNotSchedulable,
    due_count,
    due_list,
    ensure_schedule,
    get_schedule,
    history,
    review_now,
    set_suspended,
)


def make_knowledge(session: Session, title: str = "梯度下降") -> str:
    return create_knowledge(
        session,
        KnowledgeCreate(
            title=title,
            user_understanding="每步往下降最快的方向走一小步",
            domain="机器学习",
        ),
    ).id


def make_due(session: Session, title: str = "梯度下降") -> str:
    """造一条"现在就该复习"的知识。

    入库后首复排在明天（D-074），所以到期场景要手动把 due 拨到过去——
    这也顺带证明了新排期不会把刚记下的知识立刻塞进到期列表。
    """
    knowledge_id = make_knowledge(session, title)
    schedule = get_schedule(session, knowledge_id)
    schedule.due = utcnow() - timedelta(hours=1)
    session.commit()
    return knowledge_id


# ================================================================ 自动进循环


def test_create_knowledge_automatically_enrolls(db_session: Session) -> None:
    """D-060 的核心：记一条知识，不用做任何设置，排期表里就有它。"""
    before = utcnow()
    knowledge_id = make_knowledge(db_session)
    after = utcnow()

    schedule = get_schedule(db_session, knowledge_id)
    assert schedule.state == "learning"
    assert schedule.step == 0
    assert schedule.status == "active"
    assert before + timedelta(days=1) <= schedule.due <= after + timedelta(days=1), (
        "第一次复习在入库后第 1 天，不是刚压缩完就考（D-074）"
    )


def test_new_knowledge_is_not_due_yet(db_session: Session) -> None:
    """刚记完一条知识，首页的"今天还剩几条"不该立刻 +1。"""
    make_knowledge(db_session)

    assert due_list(db_session) == []
    assert due_count(db_session) == 0


def test_ensure_schedule_is_idempotent(db_session: Session) -> None:
    knowledge_id = make_knowledge(db_session)
    knowledge = db_session.get(Knowledge, knowledge_id)
    first = ensure_schedule(db_session, knowledge)
    again = ensure_schedule(db_session, knowledge)
    assert first.id == again.id


# ================================================================ 到期列表


def test_due_list_includes_due_and_excludes_the_rest(db_session: Session) -> None:
    due_id = make_due(db_session, title="该复习的")

    # 未到期的：手动把 due 推到未来
    future_id = make_knowledge(db_session, title="还没到期的")
    schedule = get_schedule(db_session, future_id)
    schedule.due = utcnow() + timedelta(days=7)
    db_session.commit()

    # 暂停的：due 在过去但 status=suspended
    suspended_id = make_due(db_session, title="暂停的")
    set_suspended(db_session, suspended_id, suspended=True)

    # 归档的：知识不在用
    archived_id = make_due(db_session, title="归档的")
    archive_knowledge(db_session, archived_id)

    ids = [item.knowledge_id for item in due_list(db_session)]
    assert due_id in ids
    assert future_id not in ids
    assert suspended_id not in ids
    assert archived_id not in ids


def test_due_list_carries_title_but_not_the_answer(db_session: Session) -> None:
    """列表阶段只给标题。把理解正文也带出来，用户瞄一眼就把"回忆"跳过去了。"""
    make_due(db_session, title="梯度下降")

    items = due_list(db_session)
    assert len(items) == 1
    assert items[0].title == "梯度下降"
    assert not any(
        "走一小步" in repr(item) for item in items
    ), "到期列表里出现了理解正文"


def test_due_count_matches_due_list(db_session: Session) -> None:
    make_due(db_session)
    make_due(db_session, title="第二条")

    # 暂停和归档的同样不许进条数——首页那个数字和列表必须是同一套过滤条件
    suspended_id = make_due(db_session, title="暂停的")
    set_suspended(db_session, suspended_id, suspended=True)
    archived_id = make_due(db_session, title="归档的")
    archive_knowledge(db_session, archived_id)

    assert due_count(db_session) == len(due_list(db_session)) == 2


# ================================================================ 提交评级


def test_review_now_writes_session_and_advances_schedule(db_session: Session) -> None:
    knowledge_id = make_knowledge(db_session)
    before = get_schedule(db_session, knowledge_id)
    old_due = before.due

    outcome = review_now(db_session, knowledge_id, "good", mode="self_test")

    assert outcome.rating == "good"
    assert outcome.due > old_due
    assert outcome.due >= utcnow() + timedelta(days=1), (
        "评完 good 之后下次复习至少隔一天，不是十分钟后（D-074）"
    )
    after = get_schedule(db_session, knowledge_id)
    assert after.last_review is not None

    rows = db_session.execute(
        select(ReviewSession).where(ReviewSession.knowledge_id == knowledge_id)
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].rating == "good" and rows[0].mode == "self_test"
    assert rows[0].suggested_rating is None


def test_ai_suggestion_never_changes_the_schedule(db_session: Session) -> None:
    """C7 的数据层证据：同样的用户评级，AI 建议是什么都不影响下一次到期时间。"""
    plain_id = make_knowledge(db_session, title="没有建议的")
    with_suggestion_id = make_knowledge(db_session, title="有建议的")

    # 两次传同一个 now：due 里含 now 分量，不固定的话差异来自时钟而不是建议
    at = utcnow()
    plain = review_now(db_session, plain_id, "good", mode="self_test", now=at)
    suggested = review_now(
        db_session, with_suggestion_id, "good", mode="ai_chat",
        suggested_rating="easy", ai_comment="答得不错，但漏了学习率的作用",
        now=at,
    )

    assert plain.due == suggested.due
    plain_row = history(db_session, plain_id)[0]
    suggested_row = history(db_session, with_suggestion_id)[0]
    assert suggested_row.suggested_rating == "easy"
    assert suggested_row.ai_comment is not None


def test_review_now_rejects_bad_rating_mode_and_suggestion(db_session: Session) -> None:
    knowledge_id = make_knowledge(db_session)

    with pytest.raises(ValueError, match="again / hard / good / easy"):
        review_now(db_session, knowledge_id, "还行", mode="self_test")
    with pytest.raises(ValueError, match="mode"):
        review_now(db_session, knowledge_id, "good", mode="心里想的")
    with pytest.raises(ValueError, match="suggested_rating"):
        review_now(
            db_session, knowledge_id, "good", mode="ai_chat", suggested_rating="满分"
        )


def test_review_archived_knowledge_is_rejected(db_session: Session) -> None:
    knowledge_id = make_knowledge(db_session)
    archive_knowledge(db_session, knowledge_id)

    with pytest.raises(KnowledgeNotSchedulable, match="归档"):
        review_now(db_session, knowledge_id, "good", mode="self_test")


def test_review_unknown_knowledge_is_not_found(db_session: Session) -> None:
    with pytest.raises(KnowledgeNotFound):
        review_now(db_session, "不存在的ID", "good", mode="self_test")


def test_stats_come_out_of_the_history(db_session: Session) -> None:
    """reps / lapses 要能从累积的历史里推出来（fsrs 不给，只能算）。"""
    knowledge_id = make_knowledge(db_session)
    review_now(db_session, knowledge_id, "good", mode="self_test")
    outcome = review_now(db_session, knowledge_id, "again", mode="self_test")

    assert outcome.stats.reps == 2
    assert outcome.stats.lapses == 1


# ================================================================ 暂停与历史


def test_suspend_is_idempotent_and_resume_keeps_overdue_due(db_session: Session) -> None:
    knowledge_id = make_due(db_session)
    set_suspended(db_session, knowledge_id, suspended=True)
    set_suspended(db_session, knowledge_id, suspended=True)  # 重复暂停不炸

    assert get_schedule(db_session, knowledge_id).status == "suspended"

    set_suspended(db_session, knowledge_id, suspended=False)
    resumed = get_schedule(db_session, knowledge_id)
    assert resumed.status == "active"
    assert resumed.due <= utcnow(), "暂停期间错过的复习，恢复后立刻到期"


def test_history_is_ascending(db_session: Session) -> None:
    knowledge_id = make_knowledge(db_session)
    review_now(db_session, knowledge_id, "good", mode="self_test")
    review_now(db_session, knowledge_id, "easy", mode="self_test")

    rows = history(db_session, knowledge_id)
    assert [row.rating for row in rows] == ["good", "easy"]
    assert rows[0].reviewed_at <= rows[1].reviewed_at


def test_history_of_unknown_knowledge_is_not_found(db_session: Session) -> None:
    with pytest.raises(KnowledgeNotFound):
        history(db_session, "不存在的ID")
