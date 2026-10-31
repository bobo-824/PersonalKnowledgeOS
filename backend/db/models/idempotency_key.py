"""幂等键 → 已创建的知识，用来挡住重复提交。

Blueprint 阶段 2 验收项要求「不会重复提交造成重复记录」。
提取草稿阶段**不落库**（AI_WORKFLOWS 工作流 B 第③④⑤步之间只在前端内存里），
所以"确认保存"那一次请求是唯一会产生知识行的地方，必须在服务端去重：
前端按钮 disabled 只能挡住手滑，挡不住网络重发和刷新后重点。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base, UTCDateTime, utcnow


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    # 客户端生成（UUID），直接当主键——去重就是"这个主键存在吗"，
    # 不需要额外的代理主键。长度给到 64，比 UUID 的 36 宽，
    # 免得将来某个客户端用了别的格式就被截断成同一个键。
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge.id", name="fk_idempotency_keys_knowledge_id"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    # 不建到 Knowledge 的 relationship：去重只需要"这个键存在吗、指向哪条知识"，
    # 用一次 SELECT 就够，加载整个对象图是浪费。
