"""AI 对话会话。一次「打开学习室聊一段」就是一行。

消息本身存在 ai_messages 里，本表只负责把消息分组，
让历史对话可以按次列出、按次清空（DECISIONS D-041）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow

if TYPE_CHECKING:
    from backend.db.models.ai_message import AiMessage


class AiConversation(Base):
    __tablename__ = "ai_conversations"
    # 历史对话列表按最近更新倒序排，这个排序键要有索引
    __table_args__ = (Index("ix_ai_conversations_updated_at", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    # 标题从第一条用户消息截断生成。不让 AI 起名：那会多一次付费调用，
    # 而且对话还没聊完就得先等 AI（D-042 要求批量付费操作先确认）。
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    # 每追加一条消息都要顶一下这个值，列表按它倒序排。
    # 不这么做的话就得每次 MAX(ai_messages.created_at) 联表聚合。
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    messages: Mapped[list[AiMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AiMessage.seq",
    )
