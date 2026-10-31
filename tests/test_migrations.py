"""迁移 0001 的结构保证。

这些测试盯的是 Blueprint 里几条硬验收项：
原文不被覆盖、追加式版本、外键真的生效、FTS5 真的建出来了、迁移可回滚。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from conftest import make_alembic_config

from backend.config import get_settings
from backend.db.base import utcnow
from backend.db.models import Knowledge, KnowledgeVersion

CORE_TABLES = ["alembic_version", "knowledge", "knowledge_versions", "sources"]


def table_names(session: Session) -> set[str]:
    rows = session.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
    return {row[0] for row in rows}


def test_core_tables_exist(db_session: Session) -> None:
    present = table_names(db_session)
    missing = [name for name in CORE_TABLES if name not in present]
    assert not missing, f"缺少表：{missing}"


def test_fts5_virtual_table_created(db_session: Session) -> None:
    """中文检索依赖 FTS5 虚拟表，它不在 ORM metadata 里，只能直接查 sqlite_master。"""
    ddl = db_session.execute(
        text("SELECT sql FROM sqlite_master WHERE name='knowledge_fts'")
    ).scalar_one_or_none()

    assert ddl is not None, "knowledge_fts 没有建出来"
    assert "fts5" in ddl.lower()
    assert "trigram" in ddl.lower(), "分词器必须是 trigram，否则中文搜不到"

    # FTS5 会自动生成影子表，缺了说明建表不完整
    present = table_names(db_session)
    for shadow in ("knowledge_fts_data", "knowledge_fts_idx", "knowledge_fts_config"):
        assert shadow in present, f"缺少 FTS5 影子表：{shadow}"


def test_alembic_current_matches_head(db_session: Session) -> None:
    current = db_session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    heads = list(ScriptDirectory.from_config(make_alembic_config()).get_heads())
    assert current in heads, f"当前版本 {current} 不是最新 {heads}"


def test_version_number_unique_per_knowledge(db_session: Session) -> None:
    """同一条知识的版本号不能重复，否则版本历史会出现歧义。"""
    knowledge = Knowledge(title="自注意力机制", domain="深度学习")
    db_session.add(knowledge)
    db_session.flush()

    db_session.add_all(
        [
            KnowledgeVersion(
                knowledge_id=knowledge.id, version_number=1, user_understanding="第一版理解"
            ),
            KnowledgeVersion(
                knowledge_id=knowledge.id, version_number=1, user_understanding="重复版本号"
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_foreign_keys_are_enforced(db_session: Session) -> None:
    """SQLite 默认不强制外键，session.py 里显式打开了 PRAGMA foreign_keys。"""
    assert db_session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    db_session.add(
        KnowledgeVersion(
            knowledge_id="不存在的知识ID", version_number=1, user_understanding="孤儿记录"
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_circular_fk_current_version_backfill(db_session: Session) -> None:
    """knowledge ↔ knowledge_versions 是循环外键，插入时要能正确回填。"""
    knowledge = Knowledge(title="梯度下降", domain="机器学习")
    db_session.add(knowledge)
    # UUID 主键的 default 要到 flush 时才生成，必须先 flush 才能拿到 id
    db_session.flush()

    version = KnowledgeVersion(
        knowledge_id=knowledge.id, version_number=1, user_understanding="沿负梯度方向更新参数"
    )
    db_session.add(version)
    # 挂上 relationship，让 SQLAlchemy 用 post_update 回填 current_version_id
    knowledge.current_version = version
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(Knowledge, knowledge.id)
    assert reloaded.current_version_id == version.id
    assert reloaded.current_version.user_understanding == "沿负梯度方向更新参数"


def test_bogus_current_version_rejected(db_session: Session) -> None:
    knowledge = Knowledge(title="反向传播", current_version_id="不存在的版本ID")
    db_session.add(knowledge)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_naive_datetime_rejected(db_session: Session) -> None:
    """Blueprint 9.2：时区错乱会让复习日期算错，所以在写库那一刻就拦下来。"""
    knowledge = Knowledge(title="测试知识")
    db_session.add(knowledge)
    db_session.flush()

    version = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding="不带时区的时间",
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    db_session.add(version)
    # UTCDateTime 抛的是 ValueError，SQLAlchemy 会把它包成 StatementError 再抛出来
    with pytest.raises(StatementError, match="不带时区") as excinfo:
        db_session.commit()
    assert isinstance(excinfo.value.__cause__, ValueError)
    db_session.rollback()


def test_aware_datetime_roundtrips_as_utc(db_session: Session) -> None:
    knowledge = Knowledge(title="测试知识UTC")
    db_session.add(knowledge)
    db_session.flush()

    utc_noon = datetime(2026, 5, 1, 21, 0, 0, tzinfo=timezone.utc)
    version = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding="带时区的时间",
        created_at=utc_noon,
    )
    db_session.add(version)
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(KnowledgeVersion, version.id)
    assert reloaded.created_at.tzinfo is not None
    assert reloaded.created_at == utc_noon


def test_original_understanding_survives_new_version(db_session: Session) -> None:
    """Blueprint 7.1 硬验收项：用户原文不会被后续版本或 AI 建议覆盖。"""
    knowledge = Knowledge(title="过拟合", domain="机器学习")
    db_session.add(knowledge)
    db_session.flush()

    v1 = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding="模型在训练集上表现好，在新数据上表现差",
        reasoning="我按字面意思理解的",
        open_questions="为什么参数多就会这样？",
    )
    db_session.add(v1)
    knowledge.current_version = v1
    db_session.commit()

    # 用户采纳 AI 建议后生成新版本，ai_revision 单独存放
    v2 = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=2,
        user_understanding="模型记住了训练数据的噪声，导致泛化能力下降",
        ai_revision="建议补充：泛化误差与训练误差的差距是判断依据",
        created_by="ai_confirmed",
        change_reason="采纳 AI 审查建议",
    )
    db_session.add(v2)
    db_session.flush()
    knowledge.current_version_id = v2.id
    db_session.commit()

    db_session.expire_all()
    reloaded_v1 = db_session.get(KnowledgeVersion, v1.id)
    assert reloaded_v1.user_understanding == "模型在训练集上表现好，在新数据上表现差"
    assert reloaded_v1.reasoning == "我按字面意思理解的"
    assert reloaded_v1.open_questions == "为什么参数多就会这样？"

    reloaded_knowledge = db_session.get(Knowledge, knowledge.id)
    assert reloaded_knowledge.current_version_id == v2.id
    assert [v.version_number for v in reloaded_knowledge.versions] == [1, 2]


def test_downgrade_upgrade_roundtrip_leaves_no_residue(migrated_database) -> None:
    """迁移必须可回滚，且回滚后不留 FTS5 影子表之类的残渣。"""
    url = get_settings().database_url

    def snapshot() -> set[str]:
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
                return {row[0] for row in rows}
        finally:
            engine.dispose()

    assert "knowledge_fts" in snapshot()

    config = make_alembic_config()
    command.downgrade(config, "base")

    # alembic_version 表本身会保留（Alembic 只清空版本行），其余业务表必须全部消失
    leftover = sorted(
        name
        for name in snapshot()
        if name.startswith("knowledge") or name == "sources"
    )
    assert not leftover, f"downgrade 之后残留了表：{leftover}"

    command.upgrade(config, "head")

    restored = snapshot()
    for name in ("knowledge", "knowledge_versions", "sources", "knowledge_fts"):
        assert name in restored, f"重新 upgrade 后缺少 {name}"

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()
    assert version == "0001"


def test_utcnow_is_timezone_aware() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 0
