"""一条知识的复习排期。

一张知识单元对应一行（`knowledge_id` 唯一）——排期是知识的属性，
不是独立的"卡片"实体。这里存的是 fsrs `Card` 的**完整可重建状态**
（state / step / stability / difficulty / due / last_review），
复习时用它重建 `Card` 交给 `Scheduler.review_card()`，拿到新状态再写回。

⚠️ 必须存 `step`：fsrs 的学习步（同一天内的短间隔重复）也在这个字段里，
漏掉它，当天第二次复习就会被当成首次，间隔直接算错。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow

if TYPE_CHECKING:
    from backend.db.models.knowledge import Knowledge
    from backend.db.models.review_session import ReviewSession

# 复习评级。界面用大白话（忘了/困难/良好/简单），存库用英文值（D-061）。
RATING_AGAIN = "again"
RATING_HARD = "hard"
RATING_GOOD = "good"
RATING_EASY = "easy"
RATINGS = (RATING_AGAIN, RATING_HARD, RATING_GOOD, RATING_EASY)

# 这次复习是怎么进行的。纯自测不花 AI 请求（D-062），AI 对话式花（D-063）。
REVIEW_MODE_SELF_TEST = "self_test"
REVIEW_MODE_AI_CHAT = "ai_chat"
REVIEW_MODES = (REVIEW_MODE_SELF_TEST, REVIEW_MODE_AI_CHAT)

# fsrs Card 的三种状态（fsrs.State 的名字）。首次复习前是 learning。
SCHEDULE_LEARNING = "learning"
SCHEDULE_REVIEW = "review"
SCHEDULE_RELEARNING = "relearning"
SCHEDULE_STATES = (SCHEDULE_LEARNING, SCHEDULE_REVIEW, SCHEDULE_RELEARNING)

# 排期的开关状态（D-060：默认全进复习循环，单条可暂停）。
SCHEDULE_STATUS_ACTIVE = "active"
SCHEDULE_STATUS_SUSPENDED = "suspended"
SCHEDULE_STATUSES = (SCHEDULE_STATUS_ACTIVE, SCHEDULE_STATUS_SUSPENDED)


class ReviewSchedule(Base):
    __tablename__ = "review_schedules"
    __table_args__ = (
        # 到期列表的核心查询就是 WHERE status='active' AND due <= now ORDER BY due
        Index("ix_review_schedules_due", "status", "due"),
        # 一条知识一份排期（排期是知识的属性，不是独立实体）。
        # ⚠️ 用显式 UniqueConstraint 而不是列上的 unique=True：
        # SQLite 的 autogenerate 会把后者渲染成"唯一索引"，跟迁移里的
        # 约束对不上，alembic check 会报漂移。
        UniqueConstraint("knowledge_id", name="uq_review_schedule_knowledge"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    knowledge_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge.id", name="fk_review_schedules_knowledge_id"),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 新卡片（还没复习过）这两个字段是 NULL，和 fsrs Card 一致
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    # 下次该复习的时间。新卡片建行时 = 创建时刻 + 1 天（D-074）
    due: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_review: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SCHEDULE_STATUS_ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    knowledge: Mapped[Knowledge] = relationship(foreign_keys=[knowledge_id])
    # 历史挂在 knowledge_id 上（Blueprint 5.3 的设计），排期 1:1 对应知识，
    # 所以用 viewonly + 显式 primaryjoin 关联；删除策略见 review_session.py
    sessions: Mapped[list[ReviewSession]] = relationship(
        primaryjoin="ReviewSchedule.knowledge_id == foreign(ReviewSession.knowledge_id)",
        viewonly=True,
        order_by="ReviewSession.reviewed_at",
    )
