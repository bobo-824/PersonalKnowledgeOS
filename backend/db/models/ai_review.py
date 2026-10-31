"""一次 AI 知识审查的结果。

对应 AI_WORKFLOWS 工作流 C 的第④步：审查结果**独立存储**，
不写进 knowledge_versions 的任何现有行。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow

if TYPE_CHECKING:
    from backend.db.models.ai_review_decision import AiReviewDecision
    from backend.db.models.knowledge import Knowledge
    from backend.db.models.knowledge_version import KnowledgeVersion


class AiReview(Base):
    __tablename__ = "ai_reviews"
    __table_args__ = (
        Index("ix_ai_reviews_knowledge_created", "knowledge_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    knowledge_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge.id", name="fk_ai_reviews_knowledge_id"),
        nullable=False,
        index=True,
    )
    # 审的是哪一个版本。用户采纳建议后会生成新版本，
    # 没有这一列就说不清"这份审查针对的是 v1 还是 v2"。
    version_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_versions.id", name="fk_ai_reviews_version_id"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    # 提示词改了会影响审查质量，将来排查"为什么结果变差了"要知道当时用的哪一版
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    # 五分类结果（correct_points / errors / omissions / follow_up_questions /
    # needs_verification）序列化成一个 JSON 文本列。
    #
    # 不拆成五张子表：五个类别的字段形状各不相同（errors 有
    # claim/correction/evidence，follow_up_questions 只是字符串数组），
    # 对单人本地项目拆表太重，而且从来不需要按类别做 SQL 查询。
    #
    # ⚠️ 两条不变量：
    # 1. **只有过了 Pydantic 校验的结果才允许写进来**（C3）。畸形返回不落库，
    #    只在日志里记字段名和错误类型。
    # 2. **写入后永不 UPDATE**。ai_review_decisions 用 (category, item_index)
    #    定位条目，一旦这里的数组顺序或内容变了，已有的处置决定就会指错条目。
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    # 对 knowledge / knowledge_versions 只做单向引用，不加 back_populates：
    # 那两个模型属于阶段 1 已定稿的聚合，为了 AI 旁路去改它们不值得，
    # 也容易碰坏已有的 relationship 配置。
    knowledge: Mapped[Knowledge] = relationship(foreign_keys=[knowledge_id])
    version: Mapped[KnowledgeVersion] = relationship(foreign_keys=[version_id])
    decisions: Mapped[list[AiReviewDecision]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )
