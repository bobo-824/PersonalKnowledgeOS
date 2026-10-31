"""pytest 共享夹具。

核心要求：测试绝不碰真实的 data/app.db。
做法是把 PKOS_DATA_DIR 指向 pytest 的临时目录，并清掉 get_settings 的缓存，
这样 backend.config、alembic/migrations/env.py 和 FastAPI 依赖三方看到的都是同一个临时库。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from backend.config import PROJECT_ROOT, get_settings
from backend.db.session import dispose_engine, init_engine, open_session
from backend.main import create_app

# httpx2 每次请求都打 INFO 日志，测试输出会被刷屏
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
# alembic 迁移日志同理，只在需要排查时手动调高
logging.getLogger("alembic").setLevel(logging.WARNING)

ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def make_alembic_config() -> Config:
    """构造 Alembic 配置。

    数据库 URL 不写在这里——migrations/env.py 会从 backend.config 读取，
    因此只要 PKOS_DATA_DIR 指对了，迁移自然落到临时库。
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """把整个应用的数据目录重定向到临时目录。"""
    data_dir = tmp_path / "test-data"
    monkeypatch.setenv("PKOS_DATA_DIR", str(data_dir))
    # 密钥留空，避免测试意外读到用户真实 .env 里的配置
    monkeypatch.setenv("PKOS_AI_API_KEY", "")
    monkeypatch.setenv("PKOS_AI_BASE_URL", "")
    monkeypatch.setenv("PKOS_AI_MODEL", "")
    get_settings.cache_clear()
    try:
        yield data_dir
    finally:
        get_settings.cache_clear()


@pytest.fixture
def migrated_database(isolated_data_dir: Path) -> Iterator[Path]:
    """跑真实的 alembic upgrade head，返回临时数据库文件路径。

    用真迁移而不是 Base.metadata.create_all()：只有这样才测得到
    FTS5 虚拟表、循环外键回填和 batch 模式改表这些 create_all 覆盖不到的东西。
    """
    command.upgrade(make_alembic_config(), "head")
    db_path = get_settings().database_path
    assert db_path.exists(), f"迁移后数据库文件不存在：{db_path}"
    # 双保险：临时库必须落在 pytest 的 tmp_path 里
    assert db_path.is_relative_to(isolated_data_dir.parent), (
        f"测试数据库跑到了临时目录之外：{db_path}"
    )
    yield db_path


@pytest.fixture
def db_session(migrated_database: Path) -> Iterator:
    """绑定到临时库的 ORM 会话。"""
    init_engine(get_settings().database_url)
    session = open_session()
    try:
        yield session
    finally:
        session.close()
        dispose_engine()


@pytest.fixture
def client(migrated_database: Path) -> Iterator[TestClient]:
    """已迁移临时库上的 FastAPI 测试客户端。"""
    init_engine(get_settings().database_url)
    app = create_app(get_settings())
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()


@pytest.fixture
def unmigrated_client(isolated_data_dir: Path) -> Iterator[TestClient]:
    """故意不跑迁移的客户端，用来验证降级路径（Blueprint 7.1：AI/DB 不可用时仍要能给出可读状态）。"""
    settings = get_settings()
    settings.ensure_runtime_dirs()
    init_engine(settings.database_url)
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()
