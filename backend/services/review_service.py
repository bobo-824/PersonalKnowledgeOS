"""复习旁路的业务编排（阶段 3 第③步）。事务边界同 ai_service：
一个业务动作 = 一个函数 = 一次 commit。

## 三条硬规矩（DECISIONS 第十节）

1. **评级只能来自用户**（C7 / D-061）。`review_now` 收到的 `rating`
   就是用户打的分；AI 的建议只准放进 `suggested_rating` 字段存档，
   排期计算**只看 rating**。
2. **不依赖 AI**（C4 / D-062）。本文件没有任何 import 碰 AI——
   纯自测复习走这里就够了，AI 对话式复习在 ⑤ 里另起一层，
   最后仍落到本文件的 `review_now`。
3. **一条知识一份排期**（D-060）。创建知识时自动进循环
   （见 `ensure_schedule` 在 `knowledge_service.create_knowledge` 的挂钩），
   单条可暂停。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.base import utcnow
from backend.db.models import (
    RATINGS,
    REVIEW_MODES,
    SCHEDULE_STATUS_ACTIVE,
    SCHEDULE_STATUS_SUSPENDED,
    STATUS_ACTIVE,
    AiConversation,
    Knowledge,
    ReviewSchedule,
    ReviewSession,
)
from backend.domain.scheduler import (
    FIRST_REVIEW_DELAY,
    CardState,
    DerivedStats,
    card_state_from_row,
    derive_stats,
    new_card_state,
    review,
)
from backend.services.knowledge_service import KnowledgeNotFound

logger = logging.getLogger(__name__)

# 到期列表一次最多取这么多条。单人本地应用一天用不完 50 条
_LIST_LIMIT_MAX = 200


class KnowledgeNotSchedulable(LookupError):
    """知识不存在，或者已归档（归档的知识不排复习）。"""


class ReviewConversationNotFound(LookupError):
    """rate 请求里带的 conversation_id 指向的对话不存在。"""


@dataclass(frozen=True)
class DueItem:
    """到期列表的一行。刻意只带标题和领域，不带理解正文——
    列表页还没开始回忆，先把答案列出来就毁掉这次复习了。"""

    knowledge_id: str
    title: str
    domain: str | None
    due: datetime
    state: str
    reps: int


@dataclass(frozen=True)
class ReviewOutcome:
    knowledge_id: str
    rating: str
    mode: str
    state: str
    due: datetime
    stats: DerivedStats


# ================================================================ 排期


def ensure_schedule(session: Session, knowledge: Knowledge) -> ReviewSchedule:
    """取排期，没有就建（due = 现在 + 1 天，第一次复习在明天，D-074）。

    只 add 不 commit：调用方（create_knowledge / review_now）自己控制事务。
    """
    schedule = session.scalar(
        select(ReviewSchedule).where(ReviewSchedule.knowledge_id == knowledge.id)
    )
    if schedule is not None:
        return schedule
    fresh = new_card_state(due=utcnow() + FIRST_REVIEW_DELAY)
    schedule = ReviewSchedule(
        knowledge_id=knowledge.id,
        state=fresh.state,
        step=fresh.step,
        stability=fresh.stability,
        difficulty=fresh.difficulty,
        due=fresh.due,
        last_review=fresh.last_review,
    )
    session.add(schedule)
    return schedule


def get_schedule(session: Session, knowledge_id: str) -> ReviewSchedule:
    schedule = session.scalar(
        select(ReviewSchedule).where(ReviewSchedule.knowledge_id == knowledge_id)
    )
    if schedule is None:
        raise KnowledgeNotFound(knowledge_id)
    return schedule


def set_suspended(
    session: Session, knowledge_id: str, *, suspended: bool
) -> ReviewSchedule:
    """暂停 / 恢复（D-060）。幂等：重复暂停不报错。

    暂停不改 due——恢复时如果 due 已在过去，那条知识自然立刻到期，
    这是期望行为（暂停期间错过的复习不追溯补排，直接回到当下）。
    """
    schedule = get_schedule(session, knowledge_id)
    schedule.status = SCHEDULE_STATUS_SUSPENDED if suspended else SCHEDULE_STATUS_ACTIVE
    session.commit()
    logger.info(
        "知识 %s 的复习已%s", knowledge_id, "暂停" if suspended else "恢复"
    )
    return schedule


# ================================================================ 到期列表


def due_list(
    session: Session, *, now: datetime | None = None, limit: int = 50
) -> list[DueItem]:
    """现在该复习哪些。归档的、暂停的一律不出现。"""
    if not 1 <= limit <= _LIST_LIMIT_MAX:
        raise ValueError(f"limit 必须在 1..{_LIST_LIMIT_MAX}，收到 {limit}")
    now = now or utcnow()
    rows = session.execute(
        select(ReviewSchedule, Knowledge.title, Knowledge.domain, func.count(ReviewSession.id))
        .join(Knowledge, Knowledge.id == ReviewSchedule.knowledge_id)
        .outerjoin(ReviewSession, ReviewSession.knowledge_id == ReviewSchedule.knowledge_id)
        .where(
            ReviewSchedule.status == SCHEDULE_STATUS_ACTIVE,
            ReviewSchedule.due <= now,
            Knowledge.status == STATUS_ACTIVE,
        )
        .group_by(ReviewSchedule.id, Knowledge.title, Knowledge.domain)
        .order_by(ReviewSchedule.due)
        .limit(limit)
    ).all()
    return [
        DueItem(
            knowledge_id=schedule.knowledge_id,
            title=title,
            domain=domain,
            due=schedule.due,
            state=schedule.state,
            reps=count,
        )
        for schedule, title, domain, count in rows
    ]


def due_count(session: Session, *, now: datetime | None = None) -> int:
    """首页要的"今天还剩几条"。同 due_list 的过滤条件。"""
    now = now or utcnow()
    return int(
        session.scalar(
            select(func.count())
            .select_from(ReviewSchedule)
            .join(Knowledge, Knowledge.id == ReviewSchedule.knowledge_id)
            .where(
                ReviewSchedule.status == SCHEDULE_STATUS_ACTIVE,
                ReviewSchedule.due <= now,
                Knowledge.status == STATUS_ACTIVE,
            )
        )
        or 0
    )


# ================================================================ 提交评级


def require_live_knowledge(session: Session, knowledge_id: str) -> Knowledge:
    """取一条**活着（未归档）**的知识，给所有"要对它复习"的入口共用。

    ai_service 的对话式复习也走这里——已归档的知识同样不能开 AI 复习。
    """
    knowledge = session.get(Knowledge, knowledge_id)
    if knowledge is None:
        raise KnowledgeNotFound(knowledge_id)
    if knowledge.status != STATUS_ACTIVE:
        raise KnowledgeNotSchedulable(
            f"知识 {knowledge_id} 已归档，不能提交复习评级"
        )
    return knowledge


def _card_of(schedule: ReviewSchedule) -> CardState:
    return card_state_from_row(
        state=schedule.state,
        step=schedule.step,
        stability=schedule.stability,
        difficulty=schedule.difficulty,
        due=schedule.due,
        last_review=schedule.last_review,
    )


def review_now(
    session: Session,
    knowledge_id: str,
    rating: str,
    *,
    mode: str,
    suggested_rating: str | None = None,
    ai_comment: str | None = None,
    conversation_id: str | None = None,
    duration_ms: int | None = None,
    now: datetime | None = None,
) -> ReviewOutcome:
    """提交一次复习。用户评级是唯一权威，排期只看它（C7）。

    AI 建议如果给了，也必须是词表里的值——AI 的输出过 C3 校验是
    ⑤ 的职责，但这里再拦一道：写进 suggested_rating 的东西将来
    要拿去展示，不能是任意字符串。
    """
    if rating not in RATINGS:
        raise ValueError(f"评级只能是 {' / '.join(RATINGS)} 之一，收到 {rating!r}")
    if mode not in REVIEW_MODES:
        raise ValueError(f"mode 只能是 {' / '.join(REVIEW_MODES)} 之一，收到 {mode!r}")
    if suggested_rating is not None and suggested_rating not in RATINGS:
        raise ValueError("suggested_rating 不是合法的评级值")
    if conversation_id is not None and session.get(AiConversation, conversation_id) is None:
        raise ReviewConversationNotFound(conversation_id)
    if duration_ms is not None and duration_ms < 0:
        raise ValueError("duration_ms 不能是负数")
    now = now or utcnow()

    require_live_knowledge(session, knowledge_id)
    schedule = ensure_schedule(session, session.get(Knowledge, knowledge_id))
    new_card = review(_card_of(schedule), rating, now)

    schedule.state = new_card.state
    schedule.step = new_card.step
    schedule.stability = new_card.stability
    schedule.difficulty = new_card.difficulty
    schedule.due = new_card.due
    schedule.last_review = new_card.last_review

    session.add(
        ReviewSession(
            knowledge_id=knowledge_id,
            reviewed_at=now,
            rating=rating,
            mode=mode,
            suggested_rating=suggested_rating,
            ai_comment=ai_comment,
            conversation_id=conversation_id,
            duration_ms=duration_ms,
        )
    )
    session.commit()

    history = session.execute(
        select(ReviewSession.reviewed_at, ReviewSession.rating)
        .where(ReviewSession.knowledge_id == knowledge_id)
        .order_by(ReviewSession.reviewed_at)
    ).all()
    stats = derive_stats(
        [(row[0], row[1]) for row in history], current_due=schedule.due, now=now
    )
    logger.info(
        "知识 %s 复习完成（%s/%s），下次 %s", knowledge_id, mode, rating, schedule.due
    )
    return ReviewOutcome(
        knowledge_id=knowledge_id,
        rating=rating,
        mode=mode,
        state=schedule.state,
        due=schedule.due,
        stats=stats,
    )


# ================================================================ 历史


def history(
    session: Session, knowledge_id: str, *, limit: int = 100
) -> list[ReviewSession]:
    """某条知识的复习历史，时间升序。"""
    if not 1 <= limit <= _LIST_LIMIT_MAX:
        raise ValueError(f"limit 必须在 1..{_LIST_LIMIT_MAX}，收到 {limit}")
    require_live_knowledge(session, knowledge_id)
    return list(
        session.execute(
            select(ReviewSession)
            .where(ReviewSession.knowledge_id == knowledge_id)
            .order_by(ReviewSession.reviewed_at)
            .limit(limit)
        ).scalars()
    )
