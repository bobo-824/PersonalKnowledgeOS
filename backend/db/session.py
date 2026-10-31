"""数据库引擎与会话工厂。

引擎延迟创建，测试可用 init_engine(临时库URL) 完全隔离，不会碰到 data/app.db。
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.config import get_settings

_engine: Engine | None = None
_sessions: sessionmaker[Session] | None = None


def build_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _apply_pragmas(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                # SQLite 默认不强制外键，必须显式打开，否则级联删除和约束形同虚设
                cursor.execute("PRAGMA foreign_keys=ON")
                # WAL：读写不互相阻塞。备份不要手动复制 app.db，
                # 用内置备份命令（VACUUM INTO），它能正确处理 WAL 未落盘的数据。
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

    return engine


def init_engine(database_url: str | None = None) -> Engine:
    """初始化或替换全局引擎。不传参数则使用配置里的数据库。"""
    global _engine, _sessions
    settings = get_settings()
    url = database_url or settings.database_url
    if url == settings.database_url:
        settings.ensure_runtime_dirs()
    _engine = build_engine(url)
    _sessions = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    return _engine


def open_session() -> Session:
    if _sessions is None:
        init_engine()
    return _sessions()


def get_session() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个会话，请求结束必关闭。"""
    session = open_session()
    try:
        yield session
    finally:
        session.close()


def dispose_engine() -> None:
    global _engine, _sessions
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessions = None
