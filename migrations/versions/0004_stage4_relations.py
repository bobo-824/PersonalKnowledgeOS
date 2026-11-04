"""stage 4 relations: knowledge_relations

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04

手写而非 autogenerate，与 0001~0003 保持一致（约束名必须显式，
SQLite 不能 ALTER TABLE ADD CONSTRAINT）。写完立刻用
`uv run alembic check` 反向验证。

字段取舍见 docs/DATA_MODEL.md「阶段 4 的表」与 docs/DECISIONS.md
第十一节（D-064~D-067）、docs/AI_WORKFLOWS.md 工作流 E：
- explanation / evidence 数据库层就 NOT NULL——没有理由和依据的
  候选在 Pydantic 层就会被拒（C3），这里再加一道保险。
- confidence 只用于排序，永远不作为自动通过的依据（C7）。
- rejected / ignored 的行**永久保留**：删掉它们，下次召回 AI 会把
  同一对关系再提一遍，审核队列永远清不完。
- 同一对知识同方向只允许一行（含 rejected）——这才是"不重复骚扰"
  的硬保证；用户想换关系类型时走「修改类型」处置，不靠重新提议。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_relations",
        sa.Column("id", sa.String(length=36), nullable=False),
        # 有向：source → target。语义随 relation_type 变（前置知识是
        # "target 是 source 的前置"；深入是 "target 比 source 深入"），
        # 具体语义由提示词与前端渲染解释。
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        # 产出这条候选的模型与提示词版本。纯本地召回产生的占位候选
        # （用户手动建的关联）这两个字段为 NULL。
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=16), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge.id"],
            name="fk_knowledge_relations_source_id",
            # 知识没有删除端点（只有归档），级联只是兜底：
            # 万一将来提供删除，图谱连线必须跟着走，不留悬空行。
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["knowledge.id"],
            name="fk_knowledge_relations_target_id",
            ondelete="CASCADE",
        ),
        # 同一对知识同方向唯一。注意没有 relation_type：用户拒绝过一次
        # 这对知识，就别让 AI 换个类型再来一遍。
        sa.UniqueConstraint(
            "source_id", "target_id", name="uq_knowledge_relations_pair"
        ),
        # 自关联毫无意义，数据库层直接拦
        sa.CheckConstraint(
            "source_id != target_id", name="ck_knowledge_relations_not_self"
        ),
    )
    # 待审核队列的核心查询：WHERE status='pending' ORDER BY created_at
    op.create_index(
        "ix_knowledge_relations_status", "knowledge_relations", ["status"]
    )
    op.create_index(
        "ix_knowledge_relations_source_id", "knowledge_relations", ["source_id"]
    )
    op.create_index(
        "ix_knowledge_relations_target_id", "knowledge_relations", ["target_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_relations_target_id", table_name="knowledge_relations"
    )
    op.drop_index(
        "ix_knowledge_relations_source_id", table_name="knowledge_relations"
    )
    op.drop_index("ix_knowledge_relations_status", table_name="knowledge_relations")
    op.drop_table("knowledge_relations")
