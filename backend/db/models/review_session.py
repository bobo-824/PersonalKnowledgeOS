"""一次复习的历史记录。

**用户的评级是唯一权威**（C7 / Blueprint 8.1）：`rating` 只来自用户的
四级打分。AI 建议的评级只能进 `suggested_rating`，永远不写 `rating`。

这张表还有一个数据职责：fsrs `Card` 不提供的 `reps` / `lapses` /
`elapsed_days` / `scheduled_days` 四个统计值全部由这里的历史推算
（ROADMAP 阶段 3 风险清单），所以 (knowledge_id, reviewed_at) 上建索引。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base, UTCDateTime, new_uuid, utcnow


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    __table_args__ = (
        Index("ix_review_sessions_knowledge_reviewed", "knowledge_id", "reviewed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    knowledge_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge.id", name="fk_review_sessions_knowledge_id"),
        nullable=False,
        # 不加 index=True：复合索引 (knowledge_id, reviewed_at) 的最左前缀
        # 已经覆盖按知识查历史，单列索引是重复的
    )
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # 用户自己打的分，四级大白话的英文值。唯一权威。
    rating: Mapped[str] = mapped_column(String(16), nullable=False)
    # self_test（不花 AI 请求）/ ai_chat
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # AI 给的评级建议。可空 = 纯自测没有建议。⚠️ 只是建议，写进这里
    # 不影响排期——排期只看 rating（C7）。
    suggested_rating: Mapped[str | None] = mapped_column(String(16))
    # AI 的评价文字（哪里答得好、哪里漏了）。可空。评价是 AI 的话，
    # 与用户原文无关，不存在 C1 冲突。
    ai_comment: Mapped[str | None] = mapped_column(Text)
    # 对话式复习（ai_chat）的问答原文在 ai_conversations / ai_messages 里，
    # 这里存指针。⚠️ ondelete SET NULL：清空对话历史只删对话，评级记录活着——
    # conversation_id 变 NULL 表示"原文已被用户主动清掉"，不是数据坏了。
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "ai_conversations.id",
            name="fk_review_sessions_conversation_id",
            ondelete="SET NULL",
        )
    )
    # 答题耗时（毫秒）。前端从出题展示计时到提交评级；量不到就留空，
    # 宁可空也不编一个数。
    duration_ms: Mapped[int | None] = mapped_column()
