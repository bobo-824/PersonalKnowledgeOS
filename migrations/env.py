"""Alembic 迁移环境。

数据库 URL 从 backend.config 读取而非写死在 alembic.ini；
测试可用环境变量 PKOS_MIGRATION_DATABASE_URL 指向临时库。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import get_settings  # noqa: E402
from backend.db import models  # noqa: E402,F401  导入以把全部模型注册进 metadata
from backend.db.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# FTS5 虚拟表及其影子表（_data/_idx/_content/_docsize/_config）由迁移用原生 SQL 管理，
# 不在 ORM metadata 中。必须显式排除，否则 alembic check 和 autogenerate 会一直
# 认为这些表是多余的并要求 drop 掉它们。
_FTS_TABLE_PREFIX = "knowledge_fts"


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name.startswith(_FTS_TABLE_PREFIX):
        return False
    return True


def database_url() -> str:
    override = os.environ.get("PKOS_MIGRATION_DATABASE_URL")
    if override:
        return override
    settings = get_settings()
    # 目录不存在时 SQLite 无法建库文件，迁移前先确保数据目录就位
    settings.ensure_runtime_dirs()
    return settings.database_url


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=_include_object,
        # SQLite 不支持大多数 ALTER TABLE。batch 模式让 Alembic 改用
        # “建新表 → 搬数据 → 删旧表 → 改名”来实现改表，否则后续加字段会直接失败。
        render_as_batch=True,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {}) or {}
    configuration["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
