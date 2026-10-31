"""用户对 AI 审查结果里**某一条**的处置。

对应 AI_WORKFLOWS 工作流 C 的第⑤步。不存这张表的话，
用户刷新页面，勾过的采纳/忽略就全没了。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow

if TYPE_CHECKING:
    from backend.db.models.ai_review import AiReview

# 五个值就是审查结果 JSON 里的五个字段名，一一对应，不做二次翻译。
CATEGORY_CORRECT_POINTS = "correct_points"
CATEGORY_ERRORS = "errors"
CATEGORY_OMISSIONS = "omissions"
CATEGORY_FOLLOW_UP_QUESTIONS = "follow_up_questions"
CATEGORY_NEEDS_VERIFICATION = "needs_verification"

REVIEW_CATEGORIES: tuple[str, ...] = (
    CATEGORY_CORRECT_POINTS,
    CATEGORY_ERRORS,
    CATEGORY_OMISSIONS,
    CATEGORY_FOLLOW_UP_QUESTIONS,
    CATEGORY_NEEDS_VERIFICATION,
)

# 五分类不能简化成"对/错"两类，尤其是 needs_verification：
# 它承认 AI 自己也不确定。只有两类的话 AI 会被迫在不确定时也给确定判断，
# 这正是原则 6（AI 反馈不是绝对权威）要防止的。
DECISION_ACCEPTED = "accepted"
DECISION_IGNORED = "ignored"
DECISION_DISAGREED = "disagreed"

REVIEW_DECISIONS: tuple[str, ...] = (
    DECISION_ACCEPTED,
    DECISION_IGNORED,
    DECISION_DISAGREED,
)


class AiReviewDecision(Base):
    __tablename__ = "ai_review_decisions"
    __table_args__ = (
        # 一条审查里的一条条目只允许一个处置，改主意是 UPSERT 不是插新行
        UniqueConstraint(
            "review_id", "category", "item_index", name="uq_ai_review_decision_item"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    review_id: Mapped[str] = mapped_column(
        ForeignKey(
            "ai_reviews.id", name="fk_ai_review_decisions_review_id", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    # 条目在 result_json 对应数组里的下标。这个定位方式成立的前提是
    # ai_reviews.result_json 永不 UPDATE（见该模型的注释）。
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 「忽略」和「不同意」都是普通操作：C7 要求用户不采纳时
    # 不弹警告、不标红、不降级体验。数据模型上三者完全平等，没有默认值偏向。
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    review: Mapped[AiReview] = relationship(back_populates="decisions")
