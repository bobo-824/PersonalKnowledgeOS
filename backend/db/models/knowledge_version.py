"""知识理解的历史版本：追加式记录，永不覆盖（Blueprint 1.5 / 5.1）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow

if TYPE_CHECKING:
    from backend.db.models.knowledge import Knowledge

# user = 用户自己写的；ai_confirmed = 用户确认采纳 AI 建议后生成的新版本
CREATED_BY_USER = "user"
CREATED_BY_AI_CONFIRMED = "ai_confirmed"


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "version_number", name="uq_knowledge_version_number"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    knowledge_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge.id", name="fk_knowledge_versions_knowledge_id"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(nullable=False)

    # 用户原文。任何流程（包括 AI）都不得覆盖这一列——Blueprint 7.1 的硬验收项。
    user_understanding: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text)
    open_questions: Mapped[str | None] = mapped_column(Text)

    # AI 的修订建议单独存放，与用户原文物理隔离，采纳后才生成新版本
    ai_revision: Mapped[str | None] = mapped_column(Text)

    change_reason: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CREATED_BY_USER
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    knowledge: Mapped[Knowledge] = relationship(
        back_populates="versions", foreign_keys=[knowledge_id]
    )
