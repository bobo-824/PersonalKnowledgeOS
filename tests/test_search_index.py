"""FTS5 索引本身的测试：同步、幂等、损坏后的修复，以及短查询的免疫性。

HTTP 层测试已经覆盖了"改完能搜到新内容"，这里补的是三件只在服务层才看得清的事：

1. **索引与数据是否一致**（`index_stats`）——出问题时靠它诊断。
2. **索引损坏后能不能修**（`rebuild_index`）——DECISIONS D-006 选了独立表，
   就必须提供修复工具，否则一旦漏同步就只能手改数据库。
3. **1~2 字查询为什么刻意查权威表**——索引全空时短查询仍然正确，
   这是那条设计决定的实际价值，不能只写在文档里。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.schemas.knowledge import KnowledgeCreate, KnowledgeUpdate
from backend.services import knowledge_service, search_service
from backend.services.search_service import (
    _fts_phrase,
    _like_pattern,
    _snippet,
)


def make_knowledge(session: Session, **overrides: object):
    payload = {
        "title": "梯度下降",
        "user_understanding": "沿着损失函数下降最快的方向，一小步一小步地挪。",
        "domain": "机器学习",
        **overrides,
    }
    return knowledge_service.create_knowledge(session, KnowledgeCreate(**payload))


# ===== 用户输入的转义 =====


class TestEscaping:
    """两条检索路径都把用户输入当数据，不当查询语法。"""

    def test_fts_phrase_wraps_in_double_quotes(self) -> None:
        assert _fts_phrase("梯度下降") == '"梯度下降"'

    def test_fts_phrase_doubles_inner_quotes(self) -> None:
        """FTS5 里双引号靠翻倍转义，否则 `a"b` 会让 MATCH 抛语法错误。"""
        assert _fts_phrase('a"b') == '"a""b"'

    def test_fts_phrase_neutralizes_operators(self) -> None:
        for hostile in ["NEAR(a b)", "a OR b", "a*", "-a", "col:a"]:
            phrase = _fts_phrase(hostile)
            assert phrase.startswith('"') and phrase.endswith('"')
            assert phrase.count('"') % 2 == 0

    def test_like_pattern_wraps_in_wildcards(self) -> None:
        assert _like_pattern("熵") == "%熵%"

    def test_like_pattern_escapes_percent_and_underscore(self) -> None:
        """没转义的话，用户输入一个 `%` 就会匹配全库。"""
        assert _like_pattern("%") == "%\\%%"
        assert _like_pattern("_") == "%\\_%"

    def test_like_pattern_escapes_backslash_first(self) -> None:
        """转义顺序要紧：先反斜杠，再 % 和 _。

        反过来的话，转义 % 时加的那个反斜杠会在下一步被再转义一次，
        变成 `\\\\%`，模式就错了。
        """
        assert _like_pattern("\\") == "%\\\\%"
        assert _like_pattern("\\%") == "%\\\\\\%%"

    def test_like_pattern_leaves_chinese_alone(self) -> None:
        assert _like_pattern("反向传播") == "%反向传播%"


class TestSnippet:
    def test_returns_none_for_empty_body(self) -> None:
        assert _snippet(None, "梯度") is None
        assert _snippet("", "梯度") is None

    def test_centers_on_the_match(self) -> None:
        body = "前面铺垫了很多字" * 10 + "梯度下降" + "后面还有很多字" * 10
        result = _snippet(body, "梯度下降")
        assert result is not None
        assert "梯度下降" in result
        assert result.startswith("…") and result.endswith("…")

    def test_short_body_is_returned_whole(self) -> None:
        assert _snippet("梯度下降", "梯度下降") == "梯度下降"

    def test_no_match_falls_back_to_the_beginning(self) -> None:
        assert _snippet("完全无关的一段文字", "梯度") == "完全无关的一段文字"

    def test_matching_is_case_insensitive(self) -> None:
        assert _snippet("Backpropagation is chain rule", "backprop") is not None


# ===== 索引一致性 =====


class TestIndexConsistency:
    def test_stats_match_after_creation(self, db_session: Session) -> None:
        make_knowledge(db_session)
        make_knowledge(db_session, title="熵", user_understanding="混乱程度")
        assert search_service.index_stats(db_session) == {"indexed": 2, "knowledge": 2}

    def test_stats_stay_matched_after_updates(self, db_session: Session) -> None:
        """每次更新都要先删后插，不能堆出多余行。"""
        knowledge = make_knowledge(db_session)
        for index in range(4):
            knowledge_service.update_knowledge(
                db_session,
                knowledge.id,
                KnowledgeUpdate(user_understanding=f"第 {index} 次修订"),
            )
        assert search_service.index_stats(db_session) == {"indexed": 1, "knowledge": 1}

    def test_stats_stay_matched_after_archiving(self, db_session: Session) -> None:
        """归档保留索引行：恢复时不需要重建，搜索靠 status 过滤。"""
        knowledge = make_knowledge(db_session)
        knowledge_service.archive_knowledge(db_session, knowledge.id)
        assert search_service.index_stats(db_session) == {"indexed": 1, "knowledge": 1}

    def test_sync_is_idempotent(self, db_session: Session) -> None:
        knowledge = make_knowledge(db_session)
        for _ in range(5):
            search_service.sync_index(db_session, knowledge)
        db_session.commit()

        rows = db_session.execute(
            text("SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_id = :kid"),
            {"kid": knowledge.id},
        ).scalar_one()
        assert rows == 1

    def test_index_body_tracks_the_current_version(self, db_session: Session) -> None:
        """直接锁死"改完理解后索引里是新正文"。

        踩过的坑：`expire_on_commit=False` 下只改 `current_version_id`
        不会让已加载的 `current_version` 关系失效，`sync_index` 会读到旧版本。
        """
        knowledge = make_knowledge(db_session, user_understanding="旧的理解正文")
        knowledge_service.update_knowledge(
            db_session, knowledge.id, KnowledgeUpdate(user_understanding="新的理解正文")
        )

        row = db_session.execute(
            text("SELECT body FROM knowledge_fts WHERE knowledge_id = :kid"),
            {"kid": knowledge.id},
        ).fetchone()
        assert row[0] == "新的理解正文"

    def test_remove_from_index(self, db_session: Session) -> None:
        knowledge = make_knowledge(db_session)
        search_service.remove_from_index(db_session, knowledge.id)
        db_session.commit()
        assert search_service.index_stats(db_session) == {"indexed": 0, "knowledge": 1}


# ===== 索引损坏与修复 =====


class TestIndexRepair:
    def _corrupt(self, session: Session) -> None:
        """模拟"某条写入路径漏调了 sync_index"的后果。"""
        session.execute(text("DELETE FROM knowledge_fts"))
        session.commit()

    def test_rebuild_restores_a_wiped_index(self, db_session: Session) -> None:
        make_knowledge(db_session)
        make_knowledge(db_session, title="熵", user_understanding="混乱程度")
        self._corrupt(db_session)
        assert search_service.index_stats(db_session) == {"indexed": 0, "knowledge": 2}

        rebuilt = search_service.rebuild_index(db_session)
        db_session.commit()

        assert rebuilt == 2
        assert search_service.index_stats(db_session) == {"indexed": 2, "knowledge": 2}
        assert search_service.search(db_session, "梯度下降").total == 1
        assert search_service.search(db_session, "混乱程度").total == 1

    def test_rebuild_replaces_stale_bodies(self, db_session: Session) -> None:
        """重建不只是补漏行，还要把过时正文换成当前版本的。"""
        knowledge = make_knowledge(db_session, user_understanding="过时的正文")
        db_session.execute(
            text("UPDATE knowledge_fts SET body = '完全错误的内容' WHERE knowledge_id = :kid"),
            {"kid": knowledge.id},
        )
        db_session.commit()

        search_service.rebuild_index(db_session)
        db_session.commit()

        row = db_session.execute(
            text("SELECT body FROM knowledge_fts WHERE knowledge_id = :kid"),
            {"kid": knowledge.id},
        ).fetchone()
        assert row[0] == "过时的正文"

    def test_rebuild_dedupes_double_indexed_rows(self, db_session: Session) -> None:
        """万一历史 bug 让一条知识在索引里有两行，重建能清干净。"""
        knowledge = make_knowledge(db_session)
        db_session.execute(
            text(
                "INSERT INTO knowledge_fts (knowledge_id, title, body) "
                "VALUES (:kid, :title, :body)"
            ),
            {"kid": knowledge.id, "title": knowledge.title, "body": "重复行"},
        )
        db_session.commit()
        assert search_service.index_stats(db_session)["indexed"] == 2

        search_service.rebuild_index(db_session)
        db_session.commit()
        assert search_service.index_stats(db_session)["indexed"] == 1

    def test_short_query_is_immune_to_index_damage(self, db_session: Session) -> None:
        """这就是短查询刻意查权威表的理由（DECISIONS D-007）。

        索引全空时，≥3 字的 FTS 查询会一无所获，
        而 1~2 字的 LIKE 查询照样给出正确结果——索引故障不会变成全站搜不到。
        """
        make_knowledge(db_session, title="梯度下降法", user_understanding="一步步挪")
        self._corrupt(db_session)

        assert search_service.search(db_session, "梯度下降").total == 0
        assert search_service.search(db_session, "梯度").total == 1
        assert search_service.search(db_session, "挪").total == 1

    def test_similar_titles_is_immune_to_index_damage(self, db_session: Session) -> None:
        make_knowledge(db_session, title="梯度下降法")
        self._corrupt(db_session)
        assert search_service.find_similar_titles(db_session, "梯度") == ["梯度下降法"]


# ===== 领域建议 =====


class TestDomainSuggestions:
    def test_returns_pairs_sorted_by_count(self, db_session: Session) -> None:
        make_knowledge(db_session, title="熵", domain="热力学", user_understanding="混乱程度")
        make_knowledge(db_session, title="梯度下降", domain="机器学习")
        make_knowledge(db_session, title="反向传播", domain="机器学习", user_understanding="链式法则")

        assert search_service.suggest_domains(db_session) == [
            ("机器学习", 2),
            ("热力学", 1),
        ]

    def test_items_without_domain_are_skipped(self, db_session: Session) -> None:
        make_knowledge(db_session, domain=None)
        assert search_service.suggest_domains(db_session) == []

    def test_limit_is_applied_in_sql(self, db_session: Session) -> None:
        for index in range(5):
            make_knowledge(
                db_session, title=f"笔记{index}", domain=f"领域{index}",
                user_understanding=f"理解{index}",
            )
        assert len(search_service.suggest_domains(db_session, limit=2)) == 2


class TestSimilarTitles:
    def test_single_char_title_returns_empty(self, db_session: Session) -> None:
        """一个字太容易误报，直接不查。API 层还会用 min_length=2 挡住。"""
        make_knowledge(db_session, title="熵是什么", user_understanding="混乱程度")
        assert search_service.find_similar_titles(db_session, "熵") == []

    def test_whitespace_is_trimmed(self, db_session: Session) -> None:
        make_knowledge(db_session, title="熵是什么", user_understanding="混乱程度")
        assert search_service.find_similar_titles(db_session, "  熵是  ") == ["熵是什么"]

    def test_wildcards_in_the_title_are_not_interpreted(self, db_session: Session) -> None:
        make_knowledge(db_session, title="梯度下降", user_understanding="一步步挪")
        assert search_service.find_similar_titles(db_session, "%") == []


@pytest.mark.parametrize(
    "status", ["active", "archived", None], ids=["active", "archived", "no-filter"]
)
def test_search_status_filter(db_session: Session, status: str | None) -> None:
    kept = make_knowledge(db_session, title="梯度下降在用")
    gone = make_knowledge(db_session, title="梯度下降归档", user_understanding="另一份理解")
    knowledge_service.archive_knowledge(db_session, gone.id)

    titles = [
        item.title
        for item in search_service.search(db_session, "梯度下降", status=status).items
    ]
    if status == "active":
        assert titles == [kept.title]
    elif status == "archived":
        assert titles == [gone.title]
    else:
        assert sorted(titles) == sorted([kept.title, gone.title])
