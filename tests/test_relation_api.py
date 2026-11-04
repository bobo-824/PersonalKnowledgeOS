"""关系接口的验收测试（阶段 4 第⑤步）。

- 免费端点（/candidates /graph /stats /pending）不配 AI 也必须可用（C4 / D-066）
- /judge 是唯一的付费端点：走 FakeProvider，请求次数 == 对数（D-042 的后端半边）
- 处置与改类型的状态码：404 不存在 / 409 状态机拒绝 / 422 类型不合法
- 响应里 pending 候选必须带双方标题（队列要能看出"哪两条知识"）
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.db.session import dispose_engine, init_engine
from backend.main import create_app
from backend.services.ai.fake import FakeProvider
from backend.api.deps import get_provider


@pytest.fixture
def client(migrated_database: Path) -> Iterator[TestClient]:
    """不带 AI 的客户端：免费端点必须在"没配 AI"的世界里照常工作（C4）。"""
    init_engine(get_settings().database_url)
    app = create_app(get_settings())
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()


@pytest.fixture
def fake_ai(migrated_database: Path) -> Iterator[tuple[TestClient, FakeProvider]]:
    init_engine(get_settings().database_url)
    provider = FakeProvider()
    app = create_app(get_settings())
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as test_client:
        yield test_client, provider
    dispose_engine()


def make_knowledge(client: TestClient, title: str, understanding: str) -> str:
    response = client.post(
        "/api/knowledge",
        json={
            "title": title,
            "user_understanding": understanding,
            "domain": "机器学习",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def judge_suggestion() -> dict:
    return {
        "is_related": True,
        "relation_type": "prerequisite",
        "direction": "first",
        "explanation": "线性代数是理解矩阵运算的前提",
        "evidence": "双方理解都提到矩阵运算",
        "confidence": 0.8,
    }


# ================================================================ 免费端点（C4）


def test_candidates_graph_stats_work_without_any_ai(client: TestClient) -> None:
    """C4 / D-066：召回和图谱都不依赖 AI。没排任何 FakeProvider 结果，
    一次 AI 调用都不该发生。"""
    make_knowledge(client, "梯度下降", "沿最陡方向下山")
    make_knowledge(client, "梯度下降的数学", "对梯度下降做数学推导")

    assert client.get("/api/relations/candidates").status_code == 200
    body = client.get("/api/relations/candidates").json()
    assert len(body["candidates"]) >= 1

    assert client.get("/api/relations/graph").status_code == 200
    graph = client.get("/api/relations/graph").json()
    assert len(graph["nodes"]) == 2
    assert graph["edges"] == []  # 还没有任何 accepted

    assert client.get("/api/relations/stats").status_code == 200
    stats = client.get("/api/relations/stats").json()
    assert stats["total_knowledge"] == 2
    assert stats["total_relations"] == 0

    assert client.get("/api/relations/pending").status_code == 200
    assert client.get("/api/relations/pending").json() == []


def test_judge_requires_pairs(fake_ai: tuple[TestClient, FakeProvider]) -> None:
    client, _provider = fake_ai
    response = client.post("/api/relations/judge", json={"pairs": []})
    assert response.status_code == 422


# ================================================================ 判断 → 审核 全流程


def test_full_judge_and_decide_flow(fake_ai: tuple[TestClient, FakeProvider]) -> None:
    client, provider = fake_ai
    first = make_knowledge(client, "线性代数基础", "矩阵与向量")
    second = make_knowledge(client, "梯度下降", "沿最陡方向下山")

    provider.queue(judge_suggestion())
    response = client.post(
        "/api/relations/judge",
        json={"pairs": [{"source_id": first, "target_id": second}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
    assert provider.remaining() == 0

    pending = client.get("/api/relations/pending").json()
    assert len(pending) == 1
    row = pending[0]
    assert row["source_title"] == "线性代数基础"
    assert row["target_title"] == "梯度下降"
    assert row["status"] == "pending"
    assert row["model"] == "fake-model"

    # 图谱此时还没有这条边（pending 不显示，工作流 E 硬要求）
    assert client.get("/api/relations/graph").json()["edges"] == []

    relation_id = row["id"]
    decide = client.post(
        f"/api/relations/{relation_id}/decide", json={"action": "accepted"}
    )
    assert decide.status_code == 200, decide.text
    assert decide.json()["status"] == "accepted"

    graph = client.get("/api/relations/graph").json()
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["relation_type"] == "prerequisite"

    stats = client.get("/api/relations/stats").json()
    assert stats["total_relations"] == 1


def test_decide_unknown_relation_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/relations/no-such-id/decide", json={"action": "accepted"}
    )
    assert response.status_code == 404


def test_re_deciding_a_terminal_relation_is_409(fake_ai: tuple[TestClient, FakeProvider]) -> None:
    client, provider = fake_ai
    first = make_knowledge(client, "线性代数基础", "矩阵与向量")
    second = make_knowledge(client, "梯度下降", "沿最陡方向下山")
    provider.queue(judge_suggestion())
    client.post(
        "/api/relations/judge",
        json={"pairs": [{"source_id": first, "target_id": second}]},
    )
    relation_id = client.get("/api/relations/pending").json()[0]["id"]
    client.post(f"/api/relations/{relation_id}/decide", json={"action": "rejected"})

    response = client.post(
        f"/api/relations/{relation_id}/decide", json={"action": "accepted"}
    )
    assert response.status_code == 409


def test_change_type_on_pending_and_reject_dirty_type(
    fake_ai: tuple[TestClient, FakeProvider],
) -> None:
    client, provider = fake_ai
    first = make_knowledge(client, "线性代数基础", "矩阵与向量")
    second = make_knowledge(client, "梯度下降", "沿最陡方向下山")
    provider.queue(judge_suggestion())
    client.post(
        "/api/relations/judge",
        json={"pairs": [{"source_id": first, "target_id": second}]},
    )
    relation_id = client.get("/api/relations/pending").json()[0]["id"]

    changed = client.put(
        f"/api/relations/{relation_id}/type", json={"relation_type": "deeper"}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["relation_type"] == "deeper"

    dirty = client.put(
        f"/api/relations/{relation_id}/type", json={"relation_type": "带 空格"}
    )
    assert dirty.status_code == 422

    # 自定义类型（D-065）合法
    custom = client.put(
        f"/api/relations/{relation_id}/type", json={"relation_type": "同源概念"}
    )
    assert custom.status_code == 200
    assert custom.json()["relation_type"] == "同源概念"


def test_judge_reports_failures_per_pair_without_failing_the_request(
    fake_ai: tuple[TestClient, FakeProvider],
) -> None:
    """部分成功是正常结果：失败的对在报告里，HTTP 仍是 200。"""
    from backend.services.ai.errors import AiTimeout

    client, provider = fake_ai
    first = make_knowledge(client, "线性代数基础", "矩阵与向量")
    second = make_knowledge(client, "不存在标题的知识点占位", "随便什么理解")

    provider.queue(AiTimeout("超时"))
    response = client.post(
        "/api/relations/judge",
        json={"pairs": [{"source_id": first, "target_id": second}]},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["created"] == 0
    assert len(report["failed"]) == 1
    assert report["failed"][0]["error_kind"] == "timeout"


def test_related_endpoint_returns_accepted_and_candidates(
    fake_ai: tuple[TestClient, FakeProvider],
) -> None:
    client, provider = fake_ai
    # 两份理解内容相关、标题有重叠：召回才找得到这对（D-064 的纯本地召回）
    first = make_knowledge(client, "梯度下降", "沿着最陡的方向一小步一小步下山")
    second = make_knowledge(client, "梯度下降的数学", "对梯度下降做数学推导，梯度指最陡方向")

    # 还没判断时：related 只有候选
    related = client.get(f"/api/relations/knowledge/{first}/related")
    assert related.status_code == 200, related.text
    assert related.json()["relations"] == []
    assert len(related.json()["candidates"]) == 1

    provider.queue(judge_suggestion())
    client.post(
        "/api/relations/judge",
        json={"pairs": [{"source_id": first, "target_id": second}]},
    )
    relation_id = client.get("/api/relations/pending").json()[0]["id"]
    client.post(f"/api/relations/{relation_id}/decide", json={"action": "accepted"})

    related = client.get(f"/api/relations/knowledge/{first}/related").json()
    assert len(related["relations"]) == 1
    assert related["relations"][0]["other_title"] == "梯度下降的数学"
    # 已确认的对不再出现在候选里（召回排除任何既有关系）
    related_after = client.get(f"/api/relations/knowledge/{second}/related").json()
    assert related_after["candidates"] == []


def test_related_for_archived_knowledge_is_409(client: TestClient) -> None:
    kid = make_knowledge(client, "线性代数基础", "矩阵与向量")
    assert client.post(f"/api/knowledge/{kid}/archive").status_code == 200
    response = client.get(f"/api/relations/knowledge/{kid}/related")
    assert response.status_code == 409
