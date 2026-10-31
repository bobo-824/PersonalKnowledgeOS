"""stage 2 ai: ai_conversations / ai_messages / ai_reviews / ai_review_decisions / idempotency_keys

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-31

手写而非 autogenerate，与 0001 保持一致：约束名要显式（SQLite 不能
ALTER TABLE ADD CONSTRAINT，将来改约束只能整表重建，名字必须一开始就定死），
而且写完立刻用 `uv run alembic check` 反向验证——如果手写漏了列或索引，
check 会报漂移，所以"手写"不等于"没有校验"。

五张表各自存在的理由见 docs/DECISIONS.md 第九节与 docs/DATA_MODEL.md。
其中 ai_reviews / ai_review_decisions / idempotency_keys 三张是设计文档里
没列过、但实现时绕不过去的：审查结果要求"独立存储且不写进 knowledge_versions
的任何现有行"，逐条采纳/忽略的状态要能扛住刷新，而提取草稿不落库意味着
"确认保存"那一次请求必须靠服务端去重来挡住重复提交。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 建表顺序按外键依赖：被引用的一方先建。
    op.create_table(
        "ai_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_conversations_updated_at", "ai_conversations", ["updated_at"]
    )

    op.create_table(
        "ai_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_kind", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_conversations.id"],
            name="fk_ai_messages_conversation_id",
            # 清空对话历史（D-041）靠数据库级级联，不能只依赖 ORM 的
            # delete-orphan：绕过 ORM 的批量 DELETE 也要能把消息带走。
            # session.py 里已经 PRAGMA foreign_keys=ON，否则这行形同虚设。
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_ai_message_seq"),
    )
    op.create_index(
        "ix_ai_messages_conversation_id", "ai_messages", ["conversation_id"]
    )

    op.create_table(
        "ai_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["knowledge_id"], ["knowledge.id"], name="fk_ai_reviews_knowledge_id"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["knowledge_versions.id"],
            name="fk_ai_reviews_version_id",
        ),
    )
    op.create_index("ix_ai_reviews_knowledge_id", "ai_reviews", ["knowledge_id"])
    op.create_index(
        "ix_ai_reviews_knowledge_created", "ai_reviews", ["knowledge_id", "created_at"]
    )

    op.create_table(
        "ai_review_decisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("review_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["ai_reviews.id"],
            name="fk_ai_review_decisions_review_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "review_id", "category", "item_index", name="uq_ai_review_decision_item"
        ),
    )
    op.create_index(
        "ix_ai_review_decisions_review_id", "ai_review_decisions", ["review_id"]
    )

    op.create_table(
        "idempotency_keys",
        # 客户端生成的键直接当主键，不加代理主键：去重就是"这个主键存在吗"
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
        sa.ForeignKeyConstraint(
            ["knowledge_id"],
            ["knowledge.id"],
            name="fk_idempotency_keys_knowledge_id",
        ),
    )
    op.create_index(
        "ix_idempotency_keys_knowledge_id", "idempotency_keys", ["knowledge_id"]
    )


def downgrade() -> None:
    # 反向：先删引用别人的，再删被引用的
    op.drop_index("ix_idempotency_keys_knowledge_id", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index(
        "ix_ai_review_decisions_review_id", table_name="ai_review_decisions"
    )
    op.drop_table("ai_review_decisions")
    op.drop_index("ix_ai_reviews_knowledge_created", table_name="ai_reviews")
    op.drop_index("ix_ai_reviews_knowledge_id", table_name="ai_reviews")
    op.drop_table("ai_reviews")
    op.drop_index("ix_ai_messages_conversation_id", table_name="ai_messages")
    op.drop_table("ai_messages")
    op.drop_index("ix_ai_conversations_updated_at", table_name="ai_conversations")
    op.drop_table("ai_conversations")
