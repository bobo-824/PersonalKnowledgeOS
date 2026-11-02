"""AI 对话里的单条消息。

对话记录**独立存储**，绝不混进 knowledge_versions（AI_WORKFLOWS 工作流 A）。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow

if TYPE_CHECKING:
    from backend.db.models.ai_conversation import AiConversation

# 只存这两种角色。系统提示词不落库——它能由 ai_reviews.prompt_version
# 或代码里的 prompts.py 完整重建，存一份只是白白占空间（C6 最小必要）。
MESSAGE_ROLE_USER = "user"
MESSAGE_ROLE_ASSISTANT = "assistant"

MESSAGE_ROLES: tuple[str, ...] = (MESSAGE_ROLE_USER, MESSAGE_ROLE_ASSISTANT)

# 调用失败时记**类别**，不记响应体、不记 traceback。
# 与 services/ai/errors.py 的异常类一一对应（AI_WORKFLOWS 第一节：
# 统一报"AI 出错了"等于没报，用户分不清该重试、该改网络还是该改设置）。
ERROR_KIND_UNAVAILABLE = "unavailable"
ERROR_KIND_TIMEOUT = "timeout"
ERROR_KIND_AUTH_REJECTED = "auth_rejected"
ERROR_KIND_SCHEMA_INVALID = "schema_invalid"
# 服务端 4xx 里除了 401/403 的那些（主要是 400）。单独一类是实测前的判断：
# **模型名写错是新手最容易犯的错**，而它返回 400。如果并进"无法连接"，
# 用户会去查网络和代理，而真正该改的是设置页里的模型名。
ERROR_KIND_BAD_REQUEST = "bad_request"

ERROR_KINDS: tuple[str, ...] = (
    ERROR_KIND_UNAVAILABLE,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_AUTH_REJECTED,
    ERROR_KIND_SCHEMA_INVALID,
    ERROR_KIND_BAD_REQUEST,
)

# 用字符串常量而不是 SQLAlchemy Enum，理由见 docs/DECISIONS.md D-005：
# SQLite 的 Enum 落成 CHECK 约束，而 SQLite 不能 ALTER TABLE 修改 CHECK。


class AiMessage(Base):
    __tablename__ = "ai_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_ai_message_seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey(
            "ai_conversations.id",
            name="fk_ai_messages_conversation_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    # 会话内递增序号。排序不能只靠 created_at：同一毫秒内的两条消息
    # （用户消息和紧随其后的助手消息）先后顺序会不确定。
    # 取值必须是 MAX+1 而不是 len+1，理由和知识版本号一样（见 D-030）。
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # 成功时是消息正文；失败时是**我们自己生成给用户看的那句话**
    # （如「无法连接 AI 服务」），不是 AI 的原始畸形返回——那个不许存（C3）。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 以下四列只对 assistant 行有意义，用户消息一律留空
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_kind: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    conversation: Mapped[AiConversation] = relationship(back_populates="messages")
