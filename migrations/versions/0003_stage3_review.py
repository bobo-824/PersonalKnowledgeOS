"""stage 3 review: review_schedules / review_sessions + 复习对话承载列

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04

手写而非 autogenerate，与 0001/0002 保持一致（约束名必须显式，
SQLite 不能 ALTER TABLE ADD CONSTRAINT）。写完立刻用
`uv run alembic check` 反向验证。

两张表的存在理由与字段取舍见 docs/DATA_MODEL.md「阶段 3 的表」
与 docs/DECISIONS.md 第十节（D-060~D-063）。

⑤（对话式复习）复用 ai_conversations / ai_messages 存问答，不另建一套：
所以这里给 ai_conversations 补 kind 列（学习室列表据此滤掉复习对话），
给 review_sessions 补 conversation_id（指向问答原文，清空对话历史时
置 NULL 而不是删记录——评级是用户的数据，对话才是 AI 的）和
duration_ms（答题耗时，由前端量，两种复习模式统一）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_schedules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        # fsrs Card 的学习步。漏掉它，同一天内的第二次复习会被当成首次，
        # 短间隔进度直接丢掉。
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("stability", sa.Float(), nullable=True),
        sa.Column("difficulty", sa.Float(), nullable=True),
        sa.Column("due", sa.DateTime(), nullable=False),
        sa.Column("last_review", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["knowledge_id"],
            ["knowledge.id"],
            name="fk_review_schedules_knowledge_id",
        ),
        # 一条知识一份排期（排期是知识的属性，不是独立实体）
        sa.UniqueConstraint("knowledge_id", name="uq_review_schedule_knowledge"),
    )
    op.create_index(
        "ix_review_schedules_due", "review_schedules", ["status", "due"]
    )
    op.create_index(
        "ix_review_schedules_knowledge_id",
        "review_schedules",
        ["knowledge_id"],
    )

    op.create_table(
        "review_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        # AI 的评级建议只进这里，永远不写 rating（C7：AI 建议不是权威）
        sa.Column("suggested_rating", sa.String(length=16), nullable=True),
        sa.Column("ai_comment", sa.Text(), nullable=True),
        # 指向问答原文（ai_conversations）。可空 = 纯自测，或对话已被清空。
        # SET NULL 而不是 CASCADE：清空对话历史（D-041）只删对话，
        # 评级历史是用户的复习记录，必须活着。
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        # 答题耗时（毫秒），前端从出题展示到提交评级计时。可空 = 没量到。
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["knowledge_id"],
            ["knowledge.id"],
            name="fk_review_sessions_knowledge_id",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            name="fk_review_sessions_conversation_id",
            ondelete="SET NULL",
        ),
    )
    # reps / lapses / elapsed_days / scheduled_days 全靠这组查询推算
    op.create_index(
        "ix_review_sessions_knowledge_reviewed",
        "review_sessions",
        ["knowledge_id", "reviewed_at"],
    )
    # 复习对话复用 AI 对话表，kind 区分两种用途。
    # server_default 让 0002 时代已有的会话行自动归入学习室。
    op.add_column(
        "ai_conversations",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="chat"),
    )


def downgrade() -> None:
    op.drop_column("ai_conversations", "kind")
    op.drop_index(
        "ix_review_sessions_knowledge_reviewed", table_name="review_sessions"
    )
    op.drop_table("review_sessions")
    op.drop_index("ix_review_schedules_knowledge_id", table_name="review_schedules")
    op.drop_index("ix_review_schedules_due", table_name="review_schedules")
    op.drop_table("review_schedules")
