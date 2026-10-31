"""学习来源与引用信息。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow

if TYPE_CHECKING:
    from backend.db.models.knowledge import Knowledge

# ⚠️ 本项目有**两套**长得像但含义完全不同的来源词表，别混用：
#
#   sources.source_type    （本文件，SOURCE_KIND_*）—— 资料本身是什么形式
#   knowledge.source_type  （knowledge.py，SOURCE_TYPE_*）—— 这条知识怎么进系统的
#
# 两套里都有 "conversation"，但意思不一样：前者指"参考资料是一次谈话"，
# 后者指"这条知识是从 AI 对话里提取的"。前缀不同就是为了在代码里一眼区分。
#
# 用字符串常量而不是 SQLAlchemy Enum，理由见 docs/DECISIONS.md D-005：
# SQLite 的 Enum 落成 CHECK 约束，而 SQLite 不能 ALTER TABLE 修改 CHECK，
# 以后加一种类型就得整表重建。字符串列 + Pydantic 校验，加类型只是加一行常量。
SOURCE_KIND_BOOK = "book"
SOURCE_KIND_ARTICLE = "article"
SOURCE_KIND_VIDEO = "video"
SOURCE_KIND_COURSE = "course"
SOURCE_KIND_PODCAST = "podcast"
SOURCE_KIND_PAPER = "paper"
SOURCE_KIND_CONVERSATION = "conversation"
SOURCE_KIND_OTHER = "other"

SOURCE_KINDS: tuple[str, ...] = (
    SOURCE_KIND_BOOK,
    SOURCE_KIND_ARTICLE,
    SOURCE_KIND_VIDEO,
    SOURCE_KIND_COURSE,
    SOURCE_KIND_PODCAST,
    SOURCE_KIND_PAPER,
    SOURCE_KIND_CONVERSATION,
    SOURCE_KIND_OTHER,
)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    knowledge_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge.id", name="fk_sources_knowledge_id"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SOURCE_KIND_OTHER
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    url_or_reference: Mapped[str | None] = mapped_column(String(1000))
    # source_type='other' 时本列必填（DECISIONS D-029）：选了"其他"却不写清楚
    # 到底是什么来源，信息就等于丢了。跨字段校验在 schemas 层做。
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    knowledge: Mapped[Knowledge] = relationship(
        back_populates="sources", foreign_keys=[knowledge_id]
    )
