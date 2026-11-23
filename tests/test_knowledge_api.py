"""知识单元 HTTP 接口的验收测试。

这些测试对应 docs/ROADMAP.md 里阶段 1 的验收清单，其中两条是本项目最容易
**悄悄坏掉**、坏了又不容易看出来的行为，单独用整个测试类锁死：

1. **只有理解类字段真变了才建新版本**（DECISIONS D-030）
   坏掉的表现：用户点保存但什么都没改，版本号也 +1，版本历史里全是噪音。

2. **FTS5 独立索引必须跟着数据同步**（DECISIONS D-006）
   坏掉的表现：改了标题或理解，搜索还是命中旧内容。这个 bug 在数据库里
   完全看不出来（`knowledge_versions` 是对的），只体现在索引上，
   所以必须通过搜索接口来断言，不能只查表。

另外用原生 `sqlite3` 直连做了一路"数据库真相"的交叉验证：
SQLAlchemy 的会话有身份映射和 `expire_on_commit=False`，
ORM 读到的东西未必等于库里存的东西（阶段 1 就踩过这个坑）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.db.models import KNOWLEDGE_SOURCE_TYPES, SOURCE_KINDS

BASE_PAYLOAD = {
    "title": "梯度下降",
    "user_understanding": "沿着损失函数下降最快的方向，一小步一小步地挪。",
    "domain": "机器学习",
}


@pytest.fixture
def raw_db(migrated_database: Path) -> Iterator[sqlite3.Connection]:
    """绕过 SQLAlchemy 直连数据库，作为"库里到底存了什么"的独立证据。"""
    connection = sqlite3.connect(migrated_database)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def create_knowledge(client: TestClient, **overrides: object) -> dict:
    payload = {**BASE_PAYLOAD, **overrides}
    response = client.post("/api/knowledge", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def update_knowledge(client: TestClient, knowledge_id: str, payload: dict) -> dict:
    response = client.put(f"/api/knowledge/{knowledge_id}", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def versions_by_number(body: dict) -> dict[int, dict]:
    return {version["version_number"]: version for version in body["versions"]}


# ===== 路由顺序 =====


class TestRouteOrdering:
    """FastAPI 按声明顺序匹配路由。

    `/domains`、`/search`、`/similar` 必须声明在 `/{knowledge_id}` 之前，
    否则会被当成知识 id 去查，全部返回 404。这类问题改一次代码顺序就会复发。
    """

    def test_domains_is_not_swallowed_by_id_route(self, client: TestClient) -> None:
        assert client.get("/api/knowledge/domains").status_code == 200

    def test_search_is_not_swallowed_by_id_route(self, client: TestClient) -> None:
        response = client.get("/api/knowledge/search", params={"q": "梯度"})
        assert response.status_code == 200

    def test_similar_is_not_swallowed_by_id_route(self, client: TestClient) -> None:
        response = client.get("/api/knowledge/similar", params={"title": "梯度"})
        assert response.status_code == 200

    def test_unknown_id_returns_readable_404(self, client: TestClient) -> None:
        response = client.get("/api/knowledge/no-such-id")
        assert response.status_code == 404
        # 必须是中文可读信息，不是 SQLAlchemy 的原始报错
        assert response.json() == {"detail": "找不到知识单元：no-such-id"}

    def test_update_unknown_id_returns_404(self, client: TestClient) -> None:
        response = client.put("/api/knowledge/no-such-id", json={"title": "x"})
        assert response.status_code == 404

    def test_archive_unknown_id_returns_404(self, client: TestClient) -> None:
        response = client.post("/api/knowledge/no-such-id/archive")
        assert response.status_code == 404


# ===== 创建 =====


class TestCreate:
    def test_creates_first_version(self, client: TestClient) -> None:
        body = create_knowledge(client)
        assert len(body["versions"]) == 1
        assert body["versions"][0]["version_number"] == 1
        assert body["versions"][0]["user_understanding"] == BASE_PAYLOAD["user_understanding"]

    def test_current_version_id_points_at_version_one(self, client: TestClient) -> None:
        """前端靠 `current_version_id` 高亮"当前生效的是哪一版"。

        它必须在返回体里就有值，且和唯一的版本 id 一致。
        """
        body = create_knowledge(client)
        assert body["current_version_id"] == body["versions"][0]["id"]

    def test_first_version_is_marked_as_user_written(self, client: TestClient) -> None:
        """`created_by` 必须是 `user`。

        词表里**没有 `ai`** 这个取值：AI 不能直接创建版本（Blueprint 原则 2）。
        """
        body = create_knowledge(client)
        assert body["versions"][0]["created_by"] == "user"

    def test_first_change_reason_defaults_to_initial_record(self, client: TestClient) -> None:
        assert create_knowledge(client)["versions"][0]["change_reason"] == "初次记录"

    def test_explicit_change_reason_is_kept(self, client: TestClient) -> None:
        body = create_knowledge(client, change_reason="看完 3Blue1Brown 后记的")
        assert body["versions"][0]["change_reason"] == "看完 3Blue1Brown 后记的"

    def test_default_status_is_active(self, client: TestClient) -> None:
        assert create_knowledge(client)["status"] == "active"

    def test_default_source_type_is_manual(self, client: TestClient) -> None:
        assert create_knowledge(client)["source_type"] == "manual"

    def test_sources_are_persisted(self, client: TestClient) -> None:
        body = create_knowledge(
            client,
            sources=[
                {
                    "source_type": "video",
                    "title": "3Blue1Brown 神经网络系列",
                    "url_or_reference": "https://example.com/nn",
                },
                {"source_type": "other", "title": "某次讨论", "note": "和同事午饭时聊的"},
            ],
        )
        assert len(body["sources"]) == 2
        kinds = {source["source_type"] for source in body["sources"]}
        assert kinds == {"video", "other"}

    def test_sources_default_to_empty_list(self, client: TestClient) -> None:
        assert create_knowledge(client)["sources"] == []

    def test_detail_endpoint_round_trips(self, client: TestClient) -> None:
        created = create_knowledge(client)
        fetched = client.get(f"/api/knowledge/{created['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == created

    def test_rows_actually_reach_the_database(
        self, client: TestClient, raw_db: sqlite3.Connection
    ) -> None:
        """交叉验证：接口返回 201 不等于数据真落库了。"""
        created = create_knowledge(client)
        knowledge_rows = raw_db.execute("SELECT * FROM knowledge").fetchall()
        version_rows = raw_db.execute("SELECT * FROM knowledge_versions").fetchall()
        assert len(knowledge_rows) == 1
        assert len(version_rows) == 1
        assert knowledge_rows[0]["id"] == created["id"]
        assert knowledge_rows[0]["current_version_id"] == version_rows[0]["id"]


# ===== 校验拦截 =====


class TestValidationRejection:
    """这些都必须返回 422，而不是 500 或"假装成功"。"""

    def test_understanding_is_required(self, client: TestClient) -> None:
        response = client.post("/api/knowledge", json={"title": "梯度下降"})
        assert response.status_code == 422

    @pytest.mark.parametrize("junk", [".", "，", "???", "。。。", "-", "、", "   "])
    def test_punctuation_only_understanding_is_rejected(
        self, client: TestClient, junk: str
    ) -> None:
        """`min_length=1` 拦不住应付式填写，必须要求至少一个字母/数字/汉字。"""
        response = client.post(
            "/api/knowledge", json={"title": "梯度下降", "user_understanding": junk}
        )
        assert response.status_code == 422

    def test_short_but_real_understanding_is_accepted(self, client: TestClient) -> None:
        """反过来也不能矫枉过正：「混乱程度」4 个字是合格的理解。"""
        body = create_knowledge(client, title="熵", user_understanding="混乱程度")
        assert body["versions"][0]["user_understanding"] == "混乱程度"

    def test_source_kind_other_requires_note(self, client: TestClient) -> None:
        """选「其他」却不写说明，半年后这条来源等于白记（D-029）。"""
        response = client.post(
            "/api/knowledge",
            json={
                "title": "梯度下降",
                "user_understanding": "一步步挪",
                "sources": [{"source_type": "other", "title": "某个地方看到的"}],
            },
        )
        assert response.status_code == 422
        assert "备注必填" in response.text

    def test_source_kind_other_accepts_note(self, client: TestClient) -> None:
        body = create_knowledge(
            client,
            sources=[
                {"source_type": "other", "title": "某个地方看到的", "note": "某 up 主的直播"}
            ],
        )
        assert body["sources"][0]["note"] == "某 up 主的直播"

    @pytest.mark.parametrize(
        "kind", ["book", "article", "video", "course", "podcast", "paper", "conversation"]
    )
    def test_non_other_source_kinds_do_not_require_note(
        self, client: TestClient, kind: str
    ) -> None:
        body = create_knowledge(client, sources=[{"source_type": kind, "title": "某资料"}])
        assert body["sources"][0]["source_type"] == kind

    def test_unknown_source_kind_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/knowledge",
            json={
                "title": "梯度下降",
                "user_understanding": "一步步挪",
                "sources": [{"source_type": "weibo", "title": "x", "note": "y"}],
            },
        )
        assert response.status_code == 422

    def test_misspelled_field_is_rejected_not_ignored(self, client: TestClient) -> None:
        """`extra="forbid"`：拼错字段名必须报错。

        静默忽略的话，用户会以为标题改成功了，实际上什么也没发生。
        """
        response = client.post(
            "/api/knowledge",
            json={"titel": "梯度下降", "user_understanding": "一步步挪"},
        )
        assert response.status_code == 422

    def test_status_cannot_be_changed_via_update(self, client: TestClient) -> None:
        """归档不是"编辑"，必须走独立接口。"""
        created = create_knowledge(client)
        response = client.put(
            f"/api/knowledge/{created['id']}", json={"status": "archived"}
        )
        assert response.status_code == 422
        assert client.get(f"/api/knowledge/{created['id']}").json()["status"] == "active"

    def test_blank_search_query_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/knowledge/search", params={"q": ""}).status_code == 422

    def test_whitespace_search_query_returns_nothing(self, client: TestClient) -> None:
        """纯空白能过 `min_length=1`，但绝不能变成"搜全库"。"""
        response = client.get("/api/knowledge/search", params={"q": "   "})
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_overlong_title_is_rejected_before_reaching_the_database(
        self, client: TestClient
    ) -> None:
        """列宽 200，超了必须是 422 而不是 SQLite 报错变 500。"""
        response = client.post(
            "/api/knowledge",
            json={"title": "x" * 201, "user_understanding": "一步步挪"},
        )
        assert response.status_code == 422

    def test_overlong_domain_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/knowledge",
            json={"title": "t", "user_understanding": "一步步挪", "domain": "d" * 101},
        )
        assert response.status_code == 422


# ===== 版本触发规则（D-030）=====


class TestVersionTrigger:
    """核心：改「自己的理解 / 理解依据 / 尚存疑问」才建版本，改别的都不建。"""

    @pytest.mark.parametrize("field", ["title", "domain", "summary"])
    def test_non_understanding_change_creates_no_version(
        self, client: TestClient, field: str
    ) -> None:
        created = create_knowledge(client)
        body = update_knowledge(client, created["id"], {field: f"改过的{field}"})
        assert len(body["versions"]) == 1
        assert body[field] == f"改过的{field}"

    def test_sources_replacement_creates_no_version(self, client: TestClient) -> None:
        created = create_knowledge(client)
        body = update_knowledge(
            client,
            created["id"],
            {"sources": [{"source_type": "book", "title": "《深度学习》"}]},
        )
        assert len(body["versions"]) == 1
        assert len(body["sources"]) == 1

    def test_empty_sources_clears_all_sources(self, client: TestClient) -> None:
        """传空数组 = 清空；不传 = 保持原样。两者语义不同。"""
        created = create_knowledge(
            client, sources=[{"source_type": "book", "title": "《深度学习》"}]
        )
        assert len(created["sources"]) == 1

        cleared = update_knowledge(client, created["id"], {"sources": []})
        assert cleared["sources"] == []

    def test_absent_sources_are_left_alone(self, client: TestClient) -> None:
        created = create_knowledge(
            client, sources=[{"source_type": "book", "title": "《深度学习》"}]
        )
        body = update_knowledge(client, created["id"], {"title": "新标题"})
        assert len(body["sources"]) == 1
        assert body["sources"][0]["title"] == "《深度学习》"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("user_understanding", "改成：其实是在参数空间里找下坡方向。"),
            ("reasoning", "看 3Blue1Brown 的可视化推出来的"),
            ("open_questions", "学习率到底该怎么定？"),
        ],
    )
    def test_understanding_field_change_creates_a_version(
        self, client: TestClient, field: str, value: str
    ) -> None:
        created = create_knowledge(client)
        body = update_knowledge(client, created["id"], {field: value})

        assert len(body["versions"]) == 2
        assert versions_by_number(body)[2][field] == value
        assert body["current_version_id"] == versions_by_number(body)[2]["id"]

    @pytest.mark.parametrize("field", ["user_understanding", "reasoning", "open_questions"])
    def test_rewriting_the_same_text_creates_no_version(
        self, client: TestClient, field: str
    ) -> None:
        """用户点了保存但内容没变，不该制造假历史。"""
        original = {"user_understanding": "一步步挪", "reasoning": "看图", "open_questions": "无"}
        created = create_knowledge(client, **original)
        body = update_knowledge(client, created["id"], {field: original[field]})
        assert len(body["versions"]) == 1

    @pytest.mark.parametrize("padding", [" ", "   ", "\n", "\t", " \n\t "])
    def test_whitespace_only_difference_creates_no_version(
        self, client: TestClient, padding: str
    ) -> None:
        created = create_knowledge(client, user_understanding="一步步挪")
        body = update_knowledge(client, created["id"], {"user_understanding": f"{padding}一步步挪{padding}"})
        assert len(body["versions"]) == 1

    def test_clearing_a_field_counts_as_a_change(self, client: TestClient) -> None:
        """显式传 null 表示"我要清空它"，这是一次真实的修改，必须留痕。"""
        created = create_knowledge(client, reasoning="看图推出来的")
        body = update_knowledge(client, created["id"], {"reasoning": None})

        assert len(body["versions"]) == 2
        assert versions_by_number(body)[1]["reasoning"] == "看图推出来的"
        assert versions_by_number(body)[2]["reasoning"] is None

    def test_absent_field_is_not_treated_as_clearing(self, client: TestClient) -> None:
        """不传 ≠ 传 null。这个区别只能靠 `model_fields_set` 判断。"""
        created = create_knowledge(client, reasoning="看图推出来的")
        body = update_knowledge(client, created["id"], {"title": "新标题"})

        assert len(body["versions"]) == 1
        assert versions_by_number(body)[1]["reasoning"] == "看图推出来的"

    def test_no_version_is_created_but_the_change_still_lands(
        self, client: TestClient
    ) -> None:
        created = create_knowledge(client)
        body = update_knowledge(client, created["id"], {"title": "梯度下降（修订）"})
        assert body["title"] == "梯度下降（修订）"
        assert len(body["versions"]) == 1

    def test_auto_generated_change_reason_names_the_fields(
        self, client: TestClient
    ) -> None:
        """版本历史要能自我解释：半年后翻到某条，得看出这次改的是什么。"""
        created = create_knowledge(client)
        body = update_knowledge(
            client,
            created["id"],
            {"user_understanding": "新的理解", "open_questions": "还有疑问"},
        )
        assert versions_by_number(body)[2]["change_reason"] == "修改了：自己的理解、尚存疑问"

    def test_explicit_change_reason_wins_over_generated_one(
        self, client: TestClient
    ) -> None:
        created = create_knowledge(client)
        body = update_knowledge(
            client,
            created["id"],
            {"user_understanding": "新的理解", "change_reason": "复习时发现之前想错了"},
        )
        assert versions_by_number(body)[2]["change_reason"] == "复习时发现之前想错了"

    def test_empty_update_changes_nothing(self, client: TestClient) -> None:
        created = create_knowledge(client)
        body = update_knowledge(client, created["id"], {})
        assert body == created

    def test_version_numbers_increase_and_are_never_reused(
        self, client: TestClient
    ) -> None:
        """版本号取「已有最大值 + 1」而不是「行数 + 1」，将来支持回滚也不会重号。"""
        created = create_knowledge(client)
        for index in range(3):
            created = update_knowledge(
                client, created["id"], {"user_understanding": f"第 {index} 次修订的理解"}
            )
        assert [v["version_number"] for v in created["versions"]] == [1, 2, 3, 4]

    def test_current_version_id_tracks_the_latest_version(
        self, client: TestClient
    ) -> None:
        created = create_knowledge(client)
        created = update_knowledge(client, created["id"], {"user_understanding": "第二次理解"})
        created = update_knowledge(client, created["id"], {"user_understanding": "第三次理解"})

        latest = versions_by_number(created)[3]
        assert created["current_version_id"] == latest["id"]
        assert latest["user_understanding"] == "第三次理解"


# ===== 原文不被覆盖（Blueprint 原则 2）=====


class TestOriginalTextIsNeverOverwritten:
    def test_old_version_survives_an_update(self, client: TestClient) -> None:
        original = "我一开始以为梯度下降就是简单地往下走。"
        created = create_knowledge(client, user_understanding=original)

        body = update_knowledge(
            client, created["id"], {"user_understanding": "其实是在参数空间里找下坡方向。"}
        )

        assert versions_by_number(body)[1]["user_understanding"] == original
        assert versions_by_number(body)[2]["user_understanding"] != original

    def test_repeated_updates_keep_every_version(self, client: TestClient) -> None:
        texts = ["第一版理解", "第二版理解", "第三版理解"]
        created = create_knowledge(client, user_understanding=texts[0])
        for text in texts[1:]:
            created = update_knowledge(client, created["id"], {"user_understanding": text})

        stored = [
            versions_by_number(created)[number]["user_understanding"] for number in (1, 2, 3)
        ]
        assert stored == texts

    def test_versions_are_never_updated_in_the_database(
        self, client: TestClient, raw_db: sqlite3.Connection
    ) -> None:
        """从数据库层面证明是追加式：老行的内容一个字节都没变。"""
        original = "我一开始以为梯度下降就是简单地往下走。"
        created = create_knowledge(client, user_understanding=original)
        first_row = dict(
            raw_db.execute(
                "SELECT * FROM knowledge_versions WHERE knowledge_id = ?", (created["id"],)
            ).fetchone()
        )

        update_knowledge(client, created["id"], {"user_understanding": "修订后的理解"})

        rows = raw_db.execute(
            "SELECT * FROM knowledge_versions WHERE knowledge_id = ? ORDER BY version_number",
            (created["id"],),
        ).fetchall()
        assert len(rows) == 2
        assert dict(rows[0]) == first_row
        assert rows[1]["user_understanding"] == "修订后的理解"

    def test_no_version_row_is_ever_attributed_to_ai(
        self, client: TestClient, raw_db: sqlite3.Connection
    ) -> None:
        """阶段 1 还没有 AI 入库路径；这条断言防止将来悄悄加进一个 `ai`。"""
        create_knowledge(client)
        authors = {
            row["created_by"]
            for row in raw_db.execute("SELECT created_by FROM knowledge_versions").fetchall()
        }
        assert authors == {"user"}


# ===== FTS5 索引同步（D-006）=====


class TestIndexStaysInSync:
    """`knowledge_fts` 是独立表，不会自动跟着数据变。

    漏调 `sync_index` 的症状是：数据库里一切正常，搜索却命中过时内容。
    阶段 1 实际踩过一次——`expire_on_commit=False` 下只改外键不会让已加载的
    `current_version` 关系失效，索引里写进去的是旧版本的正文。
    """

    def test_search_finds_the_new_title(self, client: TestClient) -> None:
        created = create_knowledge(client, title="梯度下降", user_understanding="一步步挪")
        update_knowledge(client, created["id"], {"title": "随机梯度下降"})

        found = client.get("/api/knowledge/search", params={"q": "随机梯度下降"})
        assert found.status_code == 200
        assert [item["title"] for item in found.json()["items"]] == ["随机梯度下降"]

    def test_search_no_longer_finds_the_old_title(self, client: TestClient) -> None:
        """这是漏同步最典型的表现，单独断言。"""
        created = create_knowledge(client, title="梯度下降", user_understanding="一步步挪")
        update_knowledge(client, created["id"], {"title": "反向传播"})

        stale = client.get("/api/knowledge/search", params={"q": "梯度下降"})
        assert stale.json()["total"] == 0

    def test_search_finds_the_new_understanding(self, client: TestClient) -> None:
        created = create_knowledge(client, user_understanding="我以为是简单地往下走")
        update_knowledge(client, created["id"], {"user_understanding": "其实是在参数空间里找下坡方向"})

        found = client.get("/api/knowledge/search", params={"q": "参数空间"})
        assert found.json()["total"] == 1
        assert found.json()["items"][0]["matched_by"] == "body"

    def test_search_no_longer_finds_the_old_understanding(self, client: TestClient) -> None:
        """直接锁死 `knowledge.current_version = version` 那一行。

        少了它，索引里写的是旧版本正文，数据库却是对的——只查表看不出来。
        """
        created = create_knowledge(client, user_understanding="我以为是简单地往下走")
        update_knowledge(client, created["id"], {"user_understanding": "其实是在参数空间里找下坡方向"})

        stale = client.get("/api/knowledge/search", params={"q": "简单地往下走"})
        assert stale.json()["total"] == 0

    def test_understanding_change_after_several_versions_still_syncs(
        self, client: TestClient
    ) -> None:
        created = create_knowledge(client, user_understanding="第一版")
        created = update_knowledge(client, created["id"], {"user_understanding": "第二版理解内容"})
        created = update_knowledge(client, created["id"], {"user_understanding": "第三版理解内容"})

        assert client.get("/api/knowledge/search", params={"q": "第三版理解内容"}).json()["total"] == 1
        assert client.get("/api/knowledge/search", params={"q": "第二版理解内容"}).json()["total"] == 0
        assert client.get("/api/knowledge/search", params={"q": "第一版"}).json()["total"] == 0

    def test_index_holds_exactly_one_row_per_knowledge(
        self, client: TestClient, raw_db: sqlite3.Connection
    ) -> None:
        """`sync_index` 先删后插，重复调用不该堆出多行。"""
        created = create_knowledge(client)
        for index in range(3):
            update_knowledge(client, created["id"], {"user_understanding": f"第 {index} 次修订"})

        assert raw_db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0] == 1

    def test_index_body_matches_the_current_version(
        self, client: TestClient, raw_db: sqlite3.Connection
    ) -> None:
        created = create_knowledge(client, user_understanding="旧的理解正文")
        update_knowledge(client, created["id"], {"user_understanding": "新的理解正文"})

        row = raw_db.execute(
            "SELECT title, body FROM knowledge_fts WHERE knowledge_id = ?", (created["id"],)
        ).fetchone()
        assert row["body"] == "新的理解正文"
        assert row["title"] == BASE_PAYLOAD["title"]

    def test_index_row_survives_archiving(
        self, client: TestClient, raw_db: sqlite3.Connection
    ) -> None:
        """归档不删索引：恢复归档时不需要重建，搜索靠 status 过滤。"""
        created = create_knowledge(client)
        client.post(f"/api/knowledge/{created['id']}/archive")

        assert raw_db.execute("SELECT COUNT(*) FROM knowledge_fts").fetchone()[0] == 1


# ===== 搜索 =====


class TestSearch:
    def test_three_char_query_uses_fts(self, client: TestClient) -> None:
        create_knowledge(client, title="梯度下降法", user_understanding="一步步挪")
        result = client.get("/api/knowledge/search", params={"q": "梯度下降"}).json()
        assert result["strategy"] == "fts"
        assert result["total"] == 1

    def test_two_char_chinese_query_uses_like_and_still_matches(
        self, client: TestClient
    ) -> None:
        """trigram 分词器要求至少 3 字符，「梯度」这种 2 字词必须靠 LIKE 兜底。"""
        create_knowledge(client, title="梯度下降法", user_understanding="一步步挪")
        result = client.get("/api/knowledge/search", params={"q": "梯度"}).json()
        assert result["strategy"] == "like"
        assert result["total"] == 1
        assert result["items"][0]["matched_by"] == "title"

    def test_one_char_chinese_query_matches_body(self, client: TestClient) -> None:
        create_knowledge(client, title="熵是什么", user_understanding="衡量混乱程度的量")
        result = client.get("/api/knowledge/search", params={"q": "熵"}).json()
        assert result["strategy"] == "like"
        assert result["total"] == 1

    def test_two_char_query_finds_body_match(self, client: TestClient) -> None:
        create_knowledge(client, title="热力学笔记", user_understanding="熵增是不可逆的")
        result = client.get("/api/knowledge/search", params={"q": "熵增"}).json()
        assert result["strategy"] == "like"
        assert result["items"][0]["matched_by"] == "body"

    def test_matched_by_both(self, client: TestClient) -> None:
        create_knowledge(client, title="反向传播", user_understanding="反向传播就是链式法则")
        item = client.get("/api/knowledge/search", params={"q": "反向传播"}).json()["items"][0]
        assert item["matched_by"] == "both"

    def test_snippet_contains_the_query(self, client: TestClient) -> None:
        create_knowledge(
            client,
            title="笔记",
            user_understanding="前面说了一大堆铺垫，然后才讲到反向传播的核心是链式法则。",
        )
        item = client.get("/api/knowledge/search", params={"q": "反向传播"}).json()["items"][0]
        assert item["snippet"] is not None
        assert "反向传播" in item["snippet"]

    def test_no_match_returns_empty_result(self, client: TestClient) -> None:
        create_knowledge(client)
        result = client.get("/api/knowledge/search", params={"q": "完全无关的词"}).json()
        assert result["total"] == 0
        assert result["items"] == []

    def test_empty_database_search_returns_200(self, client: TestClient) -> None:
        result = client.get("/api/knowledge/search", params={"q": "梯度"})
        assert result.status_code == 200
        assert result.json()["total"] == 0

    @pytest.mark.parametrize(
        "hostile",
        ["%", "_", '"', "*", "-", "a\"b", "a*", "NEAR(a b)", "梯度 OR 熵", "{", "\\"],
    )
    def test_query_syntax_characters_do_not_break_search(
        self, client: TestClient, hostile: str
    ) -> None:
        """用户输入必须被当成数据，不是查询语法。

        没转义的话：一个 `%` 会匹配全库，一个 `"` 会让 FTS5 抛语法错误变 500。
        """
        create_knowledge(client, title="梯度下降", user_understanding="一步步挪")
        response = client.get("/api/knowledge/search", params={"q": hostile})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 0

    def test_like_wildcards_do_not_match_everything(self, client: TestClient) -> None:
        create_knowledge(client, title="梯度下降", user_understanding="一步步挪")
        create_knowledge(client, title="熵", user_understanding="混乱程度")
        assert client.get("/api/knowledge/search", params={"q": "%"}).json()["total"] == 0

    def test_pagination(self, client: TestClient) -> None:
        for index in range(5):
            create_knowledge(client, title=f"梯度下降笔记 {index}", user_understanding=f"第 {index} 份")

        page = client.get(
            "/api/knowledge/search", params={"q": "梯度下降", "limit": 2, "offset": 0}
        ).json()
        assert page["total"] == 5
        assert len(page["items"]) == 2

        rest = client.get(
            "/api/knowledge/search", params={"q": "梯度下降", "limit": 2, "offset": 4}
        ).json()
        assert len(rest["items"]) == 1

    def test_archived_is_excluded_by_default(self, client: TestClient) -> None:
        created = create_knowledge(client, title="梯度下降")
        client.post(f"/api/knowledge/{created['id']}/archive")
        assert client.get("/api/knowledge/search", params={"q": "梯度下降"}).json()["total"] == 0

    def test_status_all_includes_archived(self, client: TestClient) -> None:
        created = create_knowledge(client, title="梯度下降")
        client.post(f"/api/knowledge/{created['id']}/archive")

        result = client.get(
            "/api/knowledge/search", params={"q": "梯度下降", "status": "all"}
        ).json()
        assert result["total"] == 1
        assert result["items"][0]["status"] == "archived"

    def test_status_archived_returns_only_archived(self, client: TestClient) -> None:
        archived = create_knowledge(client, title="梯度下降归档版")
        create_knowledge(client, title="梯度下降在用版")
        client.post(f"/api/knowledge/{archived['id']}/archive")

        result = client.get(
            "/api/knowledge/search", params={"q": "梯度下降", "status": "archived"}
        ).json()
        assert result["total"] == 1
        assert result["items"][0]["title"] == "梯度下降归档版"

    def test_invalid_status_filter_is_rejected(self, client: TestClient) -> None:
        assert (
            client.get("/api/knowledge/search", params={"q": "梯度", "status": "deleted"}).status_code
            == 422
        )


# ===== 列表 =====


class TestList:
    def test_empty_list(self, client: TestClient) -> None:
        body = client.get("/api/knowledge").json()
        assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_list_returns_created_item(self, client: TestClient) -> None:
        create_knowledge(client)
        body = client.get("/api/knowledge").json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == BASE_PAYLOAD["title"]

    def test_list_item_carries_version_count_and_current_understanding(
        self, client: TestClient
    ) -> None:
        """这两个字段由 service 填充，不是数据库列。前端列表页要显示它们。"""
        created = create_knowledge(client, user_understanding="第一版理解")
        update_knowledge(client, created["id"], {"user_understanding": "第二版理解"})

        item = client.get("/api/knowledge").json()["items"][0]
        assert item["version_count"] == 2
        assert item["current_understanding"] == "第二版理解"

    def test_list_item_omits_full_version_history(self, client: TestClient) -> None:
        """列表接口不该把每条的全部版本都拖出来。"""
        create_knowledge(client)
        assert "versions" not in client.get("/api/knowledge").json()["items"][0]

    def test_pagination(self, client: TestClient) -> None:
        for index in range(5):
            create_knowledge(client, title=f"笔记 {index}", user_understanding=f"理解 {index}")

        body = client.get("/api/knowledge", params={"limit": 2, "offset": 0}).json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 0

    def test_pages_do_not_overlap_or_skip(self, client: TestClient) -> None:
        """排序必须稳定（updated_at 相同时补 id 做次级键），否则翻页会重复或漏项。"""
        for index in range(5):
            create_knowledge(client, title=f"笔记 {index}", user_understanding=f"理解 {index}")

        seen = []
        for offset in (0, 2, 4):
            page = client.get("/api/knowledge", params={"limit": 2, "offset": offset}).json()
            seen.extend(item["id"] for item in page["items"])
        assert len(seen) == len(set(seen)) == 5

    def test_domain_filter(self, client: TestClient) -> None:
        create_knowledge(client, title="梯度下降", domain="机器学习")
        create_knowledge(client, title="熵", domain="热力学", user_understanding="混乱程度")

        body = client.get("/api/knowledge", params={"domain": "热力学"}).json()
        assert body["total"] == 1
        assert body["items"][0]["title"] == "熵"

    def test_archived_excluded_by_default(self, client: TestClient) -> None:
        created = create_knowledge(client)
        client.post(f"/api/knowledge/{created['id']}/archive")
        assert client.get("/api/knowledge").json()["total"] == 0

    def test_status_all_includes_archived(self, client: TestClient) -> None:
        created = create_knowledge(client)
        client.post(f"/api/knowledge/{created['id']}/archive")
        assert client.get("/api/knowledge", params={"status": "all"}).json()["total"] == 1

    @pytest.mark.parametrize("limit", [0, 201, -1])
    def test_out_of_range_limit_is_rejected(self, client: TestClient, limit: int) -> None:
        assert client.get("/api/knowledge", params={"limit": limit}).status_code == 422

    def test_negative_offset_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/knowledge", params={"offset": -1}).status_code == 422


# ===== 领域自动补全（D-031）=====


class TestDomainSuggestions:
    def test_empty_when_nothing_recorded(self, client: TestClient) -> None:
        assert client.get("/api/knowledge/domains").json() == []

    def test_dedupes_and_counts(self, client: TestClient) -> None:
        for index in range(3):
            create_knowledge(
                client, title=f"机器学习笔记 {index}", domain="机器学习",
                user_understanding=f"理解 {index}",
            )
        create_knowledge(client, title="熵", domain="热力学", user_understanding="混乱程度")

        body = client.get("/api/knowledge/domains").json()
        assert {"domain": "机器学习", "count": 3} in body
        assert {"domain": "热力学", "count": 1} in body
        assert len(body) == 2

    def test_sorted_by_count_descending(self, client: TestClient) -> None:
        create_knowledge(client, title="熵", domain="热力学", user_understanding="混乱程度")
        for index in range(2):
            create_knowledge(
                client, title=f"机器学习笔记 {index}", domain="机器学习",
                user_understanding=f"理解 {index}",
            )

        assert [item["domain"] for item in client.get("/api/knowledge/domains").json()] == [
            "机器学习",
            "热力学",
        ]

    @pytest.mark.parametrize("blank", [None, ""])
    def test_missing_or_blank_domain_is_not_suggested(
        self, client: TestClient, blank: str | None
    ) -> None:
        create_knowledge(client, domain=blank, user_understanding="一步步挪")
        assert client.get("/api/knowledge/domains").json() == []

    def test_whitespace_only_domain_is_normalized_to_none(self, client: TestClient) -> None:
        create_knowledge(client, domain="   ", user_understanding="一步步挪")
        assert client.get("/api/knowledge/domains").json() == []

    def test_limit_is_honoured(self, client: TestClient) -> None:
        for index in range(5):
            create_knowledge(
                client, title=f"笔记 {index}", domain=f"领域{index}",
                user_understanding=f"理解 {index}",
            )
        assert len(client.get("/api/knowledge/domains", params={"limit": 2}).json()) == 2

    def test_archived_knowledge_still_contributes_its_domain(self, client: TestClient) -> None:
        """归档的知识随时可能恢复，领域词表不该因此抖动。"""
        created = create_knowledge(client, domain="机器学习")
        client.post(f"/api/knowledge/{created['id']}/archive")
        assert client.get("/api/knowledge/domains").json() == [{"domain": "机器学习", "count": 1}]


# ===== 相似标题提示 =====


class TestSimilarTitles:
    def test_finds_existing_knowledge_by_two_char_title(self, client: TestClient) -> None:
        """这个提示对 2 字标题也要有效，所以走 LIKE 而不是 FTS。"""
        create_knowledge(client, title="熵是什么", user_understanding="混乱程度")
        assert client.get("/api/knowledge/similar", params={"title": "熵是"}).json() == ["熵是什么"]

    def test_no_duplicate_returns_empty(self, client: TestClient) -> None:
        create_knowledge(client, title="梯度下降", user_understanding="一步步挪")
        assert client.get("/api/knowledge/similar", params={"title": "熵增"}).json() == []

    def test_one_char_title_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/knowledge/similar", params={"title": "熵"}).status_code == 422

    def test_archived_is_excluded(self, client: TestClient) -> None:
        created = create_knowledge(client, title="梯度下降", user_understanding="一步步挪")
        client.post(f"/api/knowledge/{created['id']}/archive")
        assert client.get("/api/knowledge/similar", params={"title": "梯度"}).json() == []

    def test_limit_is_honoured(self, client: TestClient) -> None:
        for index in range(5):
            create_knowledge(
                client, title=f"梯度下降 {index}", user_understanding=f"理解 {index}"
            )
        assert len(client.get("/api/knowledge/similar", params={"title": "梯度下降", "limit": 2}).json()) == 2


# ===== 归档与恢复 =====


class TestArchiveAndRestore:
    def test_archive_sets_status_without_deleting(self, client: TestClient) -> None:
        """Blueprint 5.1：不物理删除，数据完整保留。"""
        created = create_knowledge(client)
        body = client.post(f"/api/knowledge/{created['id']}/archive").json()

        assert body["status"] == "archived"
        assert client.get(f"/api/knowledge/{created['id']}").status_code == 200

    def test_archive_keeps_versions_and_sources(self, client: TestClient) -> None:
        created = create_knowledge(
            client,
            user_understanding="第一版",
            sources=[{"source_type": "book", "title": "《深度学习》"}],
        )
        update_knowledge(client, created["id"], {"user_understanding": "第二版"})

        body = client.post(f"/api/knowledge/{created['id']}/archive").json()
        assert len(body["versions"]) == 2
        assert len(body["sources"]) == 1

    def test_archive_is_idempotent(self, client: TestClient) -> None:
        created = create_knowledge(client)
        client.post(f"/api/knowledge/{created['id']}/archive")
        body = client.post(f"/api/knowledge/{created['id']}/archive").json()
        assert body["status"] == "archived"
        assert len(body["versions"]) == 1

    def test_restore_brings_it_back(self, client: TestClient) -> None:
        created = create_knowledge(client)
        client.post(f"/api/knowledge/{created['id']}/archive")
        body = client.post(f"/api/knowledge/{created['id']}/restore").json()

        assert body["status"] == "active"
        assert client.get("/api/knowledge").json()["total"] == 1

    def test_restore_keeps_version_history(self, client: TestClient) -> None:
        created = create_knowledge(client, user_understanding="第一版")
        update_knowledge(client, created["id"], {"user_understanding": "第二版"})
        client.post(f"/api/knowledge/{created['id']}/archive")
        body = client.post(f"/api/knowledge/{created['id']}/restore").json()

        assert len(body["versions"]) == 2
        assert versions_by_number(body)[1]["user_understanding"] == "第一版"

    def test_archived_item_is_still_searchable_with_status_all(
        self, client: TestClient
    ) -> None:
        """恢复归档后立刻能被搜到，不需要重建索引。"""
        created = create_knowledge(client, title="梯度下降")
        client.post(f"/api/knowledge/{created['id']}/archive")
        client.post(f"/api/knowledge/{created['id']}/restore")

        assert client.get("/api/knowledge/search", params={"q": "梯度下降"}).json()["total"] == 1


# ===== 非回归 =====


class TestNonRegression:
    """阶段 0 的行为不能被阶段 1 的路由改动破坏。"""

    def test_health_still_ok(self, client: TestClient) -> None:
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["database"]["reachable"] is True

    def test_unknown_api_path_returns_json_not_html(self, client: TestClient) -> None:
        """`/api/*` 下不存在的路径必须返回 JSON，不能被 SPA 回落吞成 index.html。"""
        response = client.get("/api/nope")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"detail": "Not Found"}

    def test_spa_fallback_still_serves_index_html(self, client: TestClient) -> None:
        response = client.get("/knowledge/some-deep-link")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    def test_index_html_is_no_cache(self, client: TestClient) -> None:
        """index.html 必须每次重新校验（D-077 配套）：前端重建后 chunk 换名，
        浏览器用缓存的旧 index.html 会去拉已删的旧哈希 → 404，桌面 app 窗口就卡旧版。
        no-cache + FileResponse 自带 ETag：未变走 304，几乎零成本。"""
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"

    def test_directory_traversal_is_still_blocked(self, client: TestClient) -> None:
        for path in ["/../../pyproject.toml", "/%2e%2e%2fpyproject.toml"]:
            response = client.get(path)
            assert response.status_code == 200
            assert "[project]" not in response.text

    def test_openapi_documents_the_vocabularies(self, client: TestClient) -> None:
        """枚举写进 schema 而不只是常量，就是为了 `/docs` 里能看到并直接试。

        Pydantic 把 `Literal` 内联进各字段而不是生成独立的 component，
        所以要从具体字段上取。断言取值集合与模型常量一致，防止文档与实际漂移。
        """
        schemas = client.get("/openapi.json").json()["components"]["schemas"]

        assert schemas["SourceCreate"]["properties"]["source_type"]["enum"] == list(SOURCE_KINDS)
        assert schemas["KnowledgeCreate"]["properties"]["source_type"]["enum"] == list(
            KNOWLEDGE_SOURCE_TYPES
        )
        assert schemas["SearchResult"]["properties"]["strategy"]["enum"] == ["fts", "like"]
