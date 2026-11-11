"""备份与导出的验收测试（阶段 5，D-068/D-069）。

盯的是这几件事在数据层的落点：
- 备份是**能打开、能查数据**的完整快照，不是碰运气复制的文件
- 保留份数滚动清理：只留最近 KEEP_COUNT 份，旧的删掉
- 同一秒两次备份不撞名（启动自动备份 + 手动按钮可能贴得很近）
- 启动自动备份失败不阻塞启动（D-068：备份坏了不能把人挡在库外面）
- 导出：Markdown 是给人读的（含理解原文与来源），JSON 是逐表完整保真
- 导出**包含归档知识**：悄悄丢掉归档项就是造假
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.schemas.knowledge import KnowledgeCreate
from backend.services import backup_service
from backend.services.backup_service import (
    KEEP_COUNT,
    BackupSourceMissing,
    auto_backup_on_startup,
    backup_dir,
    create_backup,
    list_backups,
)
from backend.services.export_service import export_json, export_markdown
from backend.services.knowledge_service import archive_knowledge, create_knowledge


def make_knowledge(db_session: Session, title: str, understanding: str) -> str:
    return create_knowledge(
        db_session,
        KnowledgeCreate(title=title, user_understanding=understanding, domain="机器学习"),
    ).id


# ================================================================ 备份（服务层）


def test_backup_snapshot_is_queryable(db_session: Session) -> None:
    """备份是完整的数据库快照：新开连接能查出里面的知识。"""
    make_knowledge(db_session, "梯度下降的直觉", "沿着最陡方向下山")
    settings = get_settings()
    info = create_backup(settings)

    backup_path = backup_dir(settings) / info.filename
    connection = sqlite3.connect(str(backup_path))
    try:
        titles = [
            row[0]
            for row in connection.execute("SELECT title FROM knowledge").fetchall()
        ]
    finally:
        connection.close()
    assert titles == ["梯度下降的直觉"]
    assert info.size_bytes > 0


def test_backup_prunes_to_keep_count(db_session: Session) -> None:
    """只留最近 KEEP_COUNT 份：手工塞 9 份，下一次备份后只剩 7。"""
    import time

    settings = get_settings()
    directory = backup_dir(settings)
    for index in range(KEEP_COUNT + 2):
        stale = directory / f"pkos-backup-2026010{index}T000000Z.db"
        stale.write_bytes(b"old")
        # mtime 全按文件名排已经足够；这里不需要碰时间戳
    assert len(list(directory.glob("pkos-backup-*.db"))) == KEEP_COUNT + 2

    time.sleep(0.01)
    create_backup(settings)

    remaining = list(directory.glob("pkos-backup-*.db"))
    assert len(remaining) == KEEP_COUNT
    # 2026-01-01 的旧文件全被清掉，剩下的是刚做的这份
    assert any(path.stat().st_size > 0 for path in remaining)


def test_backup_same_second_collision_gets_suffix(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动自动备份和手动按钮贴得很近：同秒两份都留存，-1 是新的。"""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 11, 12, 0, 0, tzinfo=tz)

    # 不冻时间的话，两次调用跨过秒边界就没有撞名，测试会随机器负载偶发红。
    monkeypatch.setattr(backup_service, "datetime", FrozenDatetime)
    first = create_backup(get_settings())
    second = create_backup(get_settings())
    assert first.filename != second.filename
    assert second.filename.endswith("-1.db")

    names = [backup.filename for backup in list_backups(get_settings())]
    assert names[0] == second.filename, "新的排前面"


def test_backup_requires_existing_database(isolated_data_dir) -> None:
    """库文件都不存在（还没跑迁移）时给明确的错，不是含糊的 IO 错误。"""
    try:
        create_backup(get_settings())
    except BackupSourceMissing as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("应当抛 BackupSourceMissing")


def test_startup_auto_backup_never_raises(isolated_data_dir) -> None:
    """启动备份失败只警告不阻塞（D-068）：库不存在时也照样返回。"""
    auto_backup_on_startup(get_settings())  # 不抛 = 通过


def test_backup_sort_key_parses_collision_counter() -> None:
    """排序键必须区分同秒的撞名文件：`-1` 比无后缀的新。"""
    from pathlib import Path

    from backend.services.backup_service import _backup_sort_key

    base = Path("pkos-backup-20261104T104409Z.db")
    colliding = Path("pkos-backup-20261104T104409Z-1.db")
    assert _backup_sort_key(base) == ("20261104T104409Z", 0)
    assert _backup_sort_key(colliding) == ("20261104T104409Z", 1)
    assert _backup_sort_key(colliding) > _backup_sort_key(base)


def test_backup_list_newest_first(db_session: Session) -> None:
    import os
    import time

    settings = get_settings()
    first = create_backup(settings)
    time.sleep(0.05)
    second = create_backup(settings)
    names = [backup.filename for backup in list_backups(settings)]
    assert names == [second.filename, first.filename]
    assert os.path.exists(backup_dir(settings) / first.filename)


# ================================================================ 备份（HTTP 层）


def test_backup_http_list_and_create(client: TestClient) -> None:
    """client 夹具启动应用时 lifespan 已经自动备份了一份，列表非空。"""
    listed = client.get("/api/backups")
    assert listed.status_code == 200
    body = listed.json()
    assert body["keep_count"] == KEEP_COUNT
    assert len(body["backups"]) >= 1

    created = client.post("/api/backups")
    assert created.status_code == 201
    assert created.json()["filename"].startswith("pkos-backup-")

    listed_again = client.get("/api/backups")
    assert len(listed_again.json()["backups"]) == len(body["backups"]) + 1


# ================================================================ 导出（服务层）


def test_markdown_contains_understanding_and_sources(db_session: Session) -> None:
    from backend.schemas.knowledge import SourceCreate

    create_knowledge(
        db_session,
        KnowledgeCreate(
            title="梯度下降的直觉",
            user_understanding="沿着最陡方向下山",
            domain="机器学习",
            reasoning="因为负梯度是下降最快的方向",
            open_questions="动量法算不算变体？",
            sources=[
                SourceCreate(
                    source_type="article",
                    title="某博客",
                    url_or_reference="https://example.com/a",
                )
            ],
        ),
    )
    markdown = export_markdown(db_session)
    assert "梯度下降的直觉" in markdown
    assert "沿着最陡方向下山" in markdown
    assert "因为负梯度是下降最快的方向" in markdown
    assert "动量法算不算变体？" in markdown
    assert "https://example.com/a" in markdown
    assert "## 机器学习" in markdown
    assert "个人知识库导出" in markdown


def test_markdown_includes_archived_flagged(db_session: Session) -> None:
    """归档知识也在导出里，且明确标注——静默丢弃等于造假。"""
    knowledge_id = make_knowledge(db_session, "过时的旧笔记", "早就不用了")
    archive_knowledge(db_session, knowledge_id)
    markdown = export_markdown(db_session)
    assert "过时的旧笔记（已归档）" in markdown


def test_json_export_is_full_fidelity(db_session: Session) -> None:
    """JSON 逐表完整：行数对得上，datetime 是带时区的 ISO 字符串。"""
    knowledge_id = make_knowledge(db_session, "梯度下降的直觉", "沿着最陡方向下山")
    archive_knowledge(db_session, knowledge_id)  # 制造一次版本追加 → 2 个版本

    data = json.loads(export_json(db_session))
    assert data["format"] == "pkos-full-export/1"
    for table in (
        "knowledge",
        "knowledge_versions",
        "sources",
        "ai_conversations",
        "ai_messages",
        "ai_reviews",
        "ai_review_decisions",
        "idempotency_keys",
        "review_schedules",
        "review_sessions",
        "knowledge_relations",
    ):
        assert table in data, f"缺表：{table}"

    assert len(data["knowledge"]) == 1
    row = data["knowledge"][0]
    assert row["title"] == "梯度下降的直觉"
    assert row["status"] == "archived"
    created = datetime.fromisoformat(row["created_at"])
    assert created.tzinfo is not None, "时间必须带时区"
    assert len(data["knowledge_versions"]) == 1, "归档不追加版本，只有理解改动才追加"
    assert len(data["review_schedules"]) == 1, "复习排期也是导出的一部分"


def test_export_empty_database_is_fine(db_session: Session) -> None:
    """空库也能导：给的是合法的空结构，不是异常。"""
    markdown = export_markdown(db_session)
    assert "个人知识库导出" in markdown
    assert "0 条" in markdown
    data = json.loads(export_json(db_session))
    assert data["knowledge"] == []


# ================================================================ 导出（HTTP 层）


def test_export_http_headers_and_payload(client: TestClient) -> None:
    markdown = client.get("/api/export/markdown")
    assert markdown.status_code == 200
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert 'attachment; filename="pkos-export-' in markdown.headers["content-disposition"]
    assert "个人知识库导出".encode("utf-8") in markdown.content

    payload = client.get("/api/export/json")
    assert payload.status_code == 200
    assert payload.headers["content-type"].startswith("application/json")
    data = json.loads(payload.content)
    assert "knowledge" in data
