"""知识单元主记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow
from backend.db.models.knowledge_version import KnowledgeVersion
from backend.db.models.source import Source

# 状态与来源类型用字符串常量而非 SQLAlchemy Enum：SQLite 上枚举变更会让 Alembic
# 迁移变得脆弱，取值校验交给 Pydantic schema 层（理由见 docs/DECISIONS.md）。
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

SOURCE_TYPE_MANUAL = "manual"
SOURCE_TYPE_CONVERSATION = "conversation"


class Knowledge(Base):
    __tablename__ = "knowledge"
    __table_args__ = (
        Index("ix_knowledge_status_updated_at", "status", "updated_at"),
        Index("ix_knowledge_domain", "domain"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SOURCE_TYPE_MANUAL
    )
    # 归档而非物理删除（Blueprint 5.1）：archived 的知识不出现在默认列表，但数据保留
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=STATUS_ACTIVE)

    # knowledge 与 knowledge_versions 互相引用形成循环外键，
    # use_alter 让 Alembic 在两张表都建好之后再补这条外键。
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "knowledge_versions.id",
            use_alter=True,
            name="fk_knowledge_current_version_id",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[KnowledgeVersion]] = relationship(
        back_populates="knowledge",
        foreign_keys="KnowledgeVersion.knowledge_id",
        order_by="KnowledgeVersion.version_number",
        cascade="all, delete-orphan",
    )
    sources: Mapped[list[Source]] = relationship(
        back_populates="knowledge", cascade="all, delete-orphan"
    )
    # post_update 打破循环依赖：插入时先写 knowledge，再回填 current_version_id
    current_version: Mapped[KnowledgeVersion | None] = relationship(
        foreign_keys=[current_version_id], post_update=True
    )
