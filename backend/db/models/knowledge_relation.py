"""知识关系（阶段 4 工作流 E）。

一条候选 = AI（或未来用户手动）提出"source 和 target 有某种关系"，
附上解释与依据，等用户处置。词表与状态机在 `backend/domain/relation.py`
（纯函数，无框架依赖），本文件只做 SQLAlchemy 映射。

⚠️ 同一对知识同方向只允许一行（`uq_knowledge_relations_pair`），
且没有 relation_type 维度——用户拒绝过一次，AI 换个类型再来一遍
也算骚扰。想换类型走「修改类型」处置，改的是本行的 relation_type。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow
from backend.domain.relation import STATUS_PENDING

if TYPE_CHECKING:
    from backend.db.models.knowledge import Knowledge


class KnowledgeRelation(Base):
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        # 待审核队列的核心查询：WHERE status='pending'
        Index("ix_knowledge_relations_status", "status"),
        Index("ix_knowledge_relations_source_id", "source_id"),
        Index("ix_knowledge_relations_target_id", "target_id"),
        UniqueConstraint("source_id", "target_id", name="uq_knowledge_relations_pair"),
        CheckConstraint(
            "source_id != target_id", name="ck_knowledge_relations_not_self"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # 有向：source → target。语义随 relation_type 变，见迁移 0004 说明
    source_id: Mapped[str] = mapped_column(
        ForeignKey(
            "knowledge.id", name="fk_knowledge_relations_source_id", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey(
            "knowledge.id", name="fk_knowledge_relations_target_id", ondelete="CASCADE"
        ),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 没有解释和依据的候选不接受（工作流 E 硬要求），数据库层再保一道
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    # 只用于排序，永远不作为自动通过的依据（C7）
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDING
    )
    # 产出者。纯本地召回占位 / 用户手动建的关联为 NULL
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    source: Mapped[Knowledge] = relationship(
        foreign_keys=[source_id], lazy="joined"
    )
    target: Mapped[Knowledge] = relationship(
        foreign_keys=[target_id], lazy="joined"
    )
