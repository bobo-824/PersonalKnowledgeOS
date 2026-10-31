"""stage 1 knowledge core: knowledge / knowledge_versions / sources + FTS5 trigram

Revision ID: 0001
Revises:
Create Date: 2026-10-31

手写而非 autogenerate，原因有三：
1. FTS5 虚拟表 autogenerate 完全不认识，必须写原生 SQL。
2. knowledge 与 knowledge_versions 互相引用形成循环外键，
   必须按「建 knowledge（暂不含该外键）→ 建 knowledge_versions → 回填外键」的顺序。
3. SQLite 不能 ALTER TABLE ADD CONSTRAINT，回填外键要走 batch 模式重建表。

FTS5 采用独立表（非外部内容表 + 触发器）：因为要同时索引 knowledge.title 和
knowledge_versions.user_understanding 两张表的字段，外部内容表只能绑定单表。
同步由 services/search_service.py 显式负责，便于测试。详见 docs/DATA_MODEL.md。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_domain", "knowledge", ["domain"])
    op.create_index(
        "ix_knowledge_status_updated_at", "knowledge", ["status", "updated_at"]
    )

    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("user_understanding", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("open_questions", sa.Text(), nullable=True),
        sa.Column("ai_revision", sa.Text(), nullable=True),
        sa.Column("change_reason", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["knowledge_id"],
            ["knowledge.id"],
            name="fk_knowledge_versions_knowledge_id",
        ),
        sa.UniqueConstraint(
            "knowledge_id", "version_number", name="uq_knowledge_version_number"
        ),
    )
    op.create_index(
        "ix_knowledge_versions_knowledge_id", "knowledge_versions", ["knowledge_id"]
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("url_or_reference", sa.String(length=1000), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["knowledge_id"], ["knowledge.id"], name="fk_sources_knowledge_id"
        ),
    )
    op.create_index("ix_sources_knowledge_id", "sources", ["knowledge_id"])

    # 两张表都存在后，回填 knowledge -> knowledge_versions 的外键。
    # SQLite 走 batch 模式：建新表、搬数据、删旧表、改名。此时表为空，代价为零。
    with op.batch_alter_table("knowledge") as batch_op:
        batch_op.create_foreign_key(
            "fk_knowledge_current_version_id",
            "knowledge_versions",
            ["current_version_id"],
            ["id"],
        )

    # trigram 分词器：中文可用，但 MATCH 查询要求至少 3 个字符。
    # 1~2 字的中文查询（如「梯度」「复习」）由 search_service 走 LIKE 兜底，
    # 实测两条路径结果一致，5000 条数据下 LIKE 约 1.4ms。
    op.execute(
        """
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            knowledge_id UNINDEXED,
            title,
            body,
            tokenize='trigram'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_fts")
    with op.batch_alter_table("knowledge") as batch_op:
        batch_op.drop_constraint(
            "fk_knowledge_current_version_id", type_="foreignkey"
        )
    op.drop_index("ix_sources_knowledge_id", table_name="sources")
    op.drop_table("sources")
    op.drop_index("ix_knowledge_versions_knowledge_id", table_name="knowledge_versions")
    op.drop_table("knowledge_versions")
    op.drop_index("ix_knowledge_status_updated_at", table_name="knowledge")
    op.drop_index("ix_knowledge_domain", table_name="knowledge")
    op.drop_table("knowledge")
