"""应用服务层的测试：只测 HTTP 接口看不出来的性质。

具体是三件事：

1. **版本号取「已有最大值 + 1」而不是「行数 + 1」**
   `(knowledge_id, version_number)` 上有唯一约束。用行数算版本号，
   一旦版本号出现空洞就会撞约束，用户看到 500。阶段 1 产生不了空洞，
   但阶段 2/3 的回滚和 AI 建议版本会，所以现在就把它锁住。

2. **列表查询次数不随页大小增长**（防 N+1）
   列表页一页 50 条，逐行访问 `k.versions` 就是 100 多次查询。
   这个只有数 SQL 才看得出来，接口返回值完全正常。

3. **异常类型与消息**——API 层靠它映射 404。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from backend.db.session import get_engine
from backend.schemas.knowledge import KnowledgeCreate, KnowledgeUpdate
from backend.services import knowledge_service
from backend.services.knowledge_service import KnowledgeNotFound


@pytest.fixture
def recorded_statements(db_session: Session) -> Iterator[list[str]]:
    """记录发出的每一条 SQL，用来数查询次数。

    `before_cursor_execute` 是 Connection/Engine 级事件，Session 上没有
    （Session 只有 `do_orm_execute`，那个看不到 selectinload 发出的语句）。
    所以挂在引擎上；测试是单线程的，不会串到别的会话。
    """
    engine = get_engine()
    statements: list[str] = []

    def _record(_conn, _cursor, statement, *_rest) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def make_knowledge(session: Session, **overrides: object):
    payload = {
        "title": "梯度下降",
        "user_understanding": "沿着损失函数下降最快的方向，一小步一小步地挪。",
        "domain": "机器学习",
        **overrides,
    }
    return knowledge_service.create_knowledge(session, KnowledgeCreate(**payload))


class TestVersionNumbering:
    def test_first_version_is_number_one(self, db_session: Session) -> None:
        assert make_knowledge(db_session).versions[0].version_number == 1

    def test_numbers_are_contiguous_while_nothing_is_removed(self, db_session: Session) -> None:
        knowledge = make_knowledge(db_session)
        for index in range(3):
            knowledge = knowledge_service.update_knowledge(
                db_session, knowledge.id, KnowledgeUpdate(user_understanding=f"第 {index} 次修订")
            )
        assert [v.version_number for v in knowledge.versions] == [1, 2, 3, 4]

    def test_number_comes_from_the_maximum_not_the_row_count(self, db_session: Session) -> None:
        """人为制造一个版本号空洞，验证不会撞唯一约束。

        用行数的话这里会算出 3，而 v3 之后紧接的写入迟早和已有行重号；
        用最大值得到 11，永远不重号。
        """
        knowledge = make_knowledge(db_session)
        knowledge = knowledge_service.update_knowledge(
            db_session, knowledge.id, KnowledgeUpdate(user_understanding="第二版")
        )
        assert [v.version_number for v in knowledge.versions] == [1, 2]

        db_session.execute(
            text(
                "UPDATE knowledge_versions SET version_number = 10 "
                "WHERE knowledge_id = :kid AND version_number = 2"
            ),
            {"kid": knowledge.id},
        )
        db_session.commit()

        updated = knowledge_service.update_knowledge(
            db_session, knowledge.id, KnowledgeUpdate(user_understanding="第三版")
        )
        numbers = [v.version_number for v in updated.versions]
        assert numbers == [1, 10, 11]
        assert updated.current_version.version_number == 11

    def test_unique_constraint_on_version_number_exists(self, db_session: Session) -> None:
        """这条约束是上面那个测试的前提，被迁移改动时要能立刻发现。"""
        knowledge = make_knowledge(db_session)
        with pytest.raises(Exception, match="UNIQUE"):
            db_session.execute(
                text(
                    "INSERT INTO knowledge_versions "
                    "(id, knowledge_id, version_number, user_understanding, created_by, created_at) "
                    "VALUES ('dup-id', :kid, 1, '重复的版本号', 'user', '2026-01-01 00:00:00')"
                ),
                {"kid": knowledge.id},
            )
        db_session.rollback()


class TestNoPlusOneQueries:
    def test_list_query_count_is_constant_across_page_sizes(
        self, db_session: Session, recorded_statements: list[str]
    ) -> None:
        for index in range(12):
            make_knowledge(
                db_session, title=f"笔记 {index}", user_understanding=f"理解 {index}"
            )

        recorded_statements.clear()
        small, _ = knowledge_service.list_knowledge(db_session, limit=1)
        small_count = len(recorded_statements)

        recorded_statements.clear()
        large, _ = knowledge_service.list_knowledge(db_session, limit=50)
        large_count = len(recorded_statements)

        assert len(small) == 1
        assert len(large) == 12
        # 页大小从 1 涨到 50，查询次数必须一模一样。
        # 涨了就说明在逐行访问关联（N+1），列表页会随数据量线性变慢。
        assert large_count == small_count

    def test_list_query_count_matches_the_docstring(
        self, db_session: Session, recorded_statements: list[str]
    ) -> None:
        """把实际查询次数写死在这里，改代码时忘了同步文档就会红。"""
        for index in range(3):
            make_knowledge(db_session, title=f"笔记 {index}", user_understanding=f"理解 {index}")

        recorded_statements.clear()
        knowledge_service.list_knowledge(db_session, limit=50)
        # 计数：1 次 count + 1 次主查询 + 1 次 current_version 预加载 + 1 次版本号统计
        assert len(recorded_statements) == 4, recorded_statements

    def test_empty_list_skips_the_dependent_queries(
        self, db_session: Session, recorded_statements: list[str]
    ) -> None:
        recorded_statements.clear()
        items, total = knowledge_service.list_knowledge(db_session)
        assert (items, total) == ([], 0)
        # 只剩 count + 主查询：没有行就不必预加载 current_version，也不必统计版本号
        assert len(recorded_statements) == 2, recorded_statements

    def test_detail_query_count_is_constant(self, db_session: Session, recorded_statements: list[str]) -> None:
        knowledge = make_knowledge(db_session)
        for index in range(5):
            knowledge = knowledge_service.update_knowledge(
                db_session, knowledge.id, KnowledgeUpdate(user_understanding=f"第 {index} 次修订")
            )

        recorded_statements.clear()
        knowledge_service.get_knowledge(db_session, knowledge.id)
        # 1 次主查询 + 3 次 selectinload（current_version / versions / sources）
        assert len(recorded_statements) == 4, recorded_statements


class TestNotFound:
    def test_get_raises(self, db_session: Session) -> None:
        with pytest.raises(KnowledgeNotFound) as info:
            knowledge_service.get_knowledge(db_session, "不存在的 id")
        assert info.value.knowledge_id == "不存在的 id"
        assert "不存在的 id" in str(info.value)

    def test_update_raises(self, db_session: Session) -> None:
        with pytest.raises(KnowledgeNotFound):
            knowledge_service.update_knowledge(db_session, "不存在的 id", KnowledgeUpdate(title="x"))

    def test_archive_raises(self, db_session: Session) -> None:
        with pytest.raises(KnowledgeNotFound):
            knowledge_service.archive_knowledge(db_session, "不存在的 id")

    def test_restore_raises(self, db_session: Session) -> None:
        with pytest.raises(KnowledgeNotFound):
            knowledge_service.restore_knowledge(db_session, "不存在的 id")


class TestReturnedObjectsAreFullyLoaded:
    """service 返回的对象会被 API 层直接 `model_validate`。

    如果关联没预加载，序列化时会触发惰性加载——在会话已关闭的场景下直接报错，
    而且这类问题只在特定访问顺序下出现，很难排查。
    """

    def test_create_result_has_versions_and_sources_loaded(self, db_session: Session) -> None:
        knowledge = make_knowledge(
            db_session, sources=[{"source_type": "book", "title": "《深度学习》"}]
        )
        db_session.expunge_all()
        assert len(knowledge.versions) == 1
        assert len(knowledge.sources) == 1
        assert knowledge.current_version is not None

    def test_update_result_reflects_the_new_version(self, db_session: Session) -> None:
        knowledge = make_knowledge(db_session)
        updated = knowledge_service.update_knowledge(
            db_session, knowledge.id, KnowledgeUpdate(user_understanding="新的理解")
        )
        db_session.expunge_all()
        assert len(updated.versions) == 2
        assert updated.current_version.user_understanding == "新的理解"
        assert updated.current_version_id == updated.current_version.id
