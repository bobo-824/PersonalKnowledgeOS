"""复习接口的验收测试（阶段 3 第④步）。

用生产路由 + 真 service，不 mock——复习路径没有 AI，全部离线可测。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_provider
from backend.config import get_settings
from backend.db.base import utcnow
from backend.db.models import RATINGS
from backend.db.session import dispose_engine, init_engine, open_session
from backend.main import AI_ERROR_STATUS, create_app
from backend.services import review_service
from backend.services.ai.errors import AiTimeout
from backend.services.ai.fake import FakeProvider
from backend.schemas.review_api import (
    ReviewMode,
    ReviewRating,
    ScheduleRead,
    ScheduleState,
    ScheduleStatus,
)


@pytest.fixture
def client(migrated_database: Path) -> TestClient:
    init_engine(get_settings().database_url)
    app = create_app(get_settings())
    with TestClient(app) as test_client:
        yield test_client
    dispose_engine()


def make_knowledge(client: TestClient, title: str = "梯度下降") -> str:
    response = client.post(
        "/api/knowledge",
        json={
            "title": title,
            "user_understanding": "每步往降最快的方向走一小步",
            "domain": "机器学习",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_due(client: TestClient, title: str = "梯度下降") -> str:
    """造一条"现在就该复习"的知识。

    入库后首复排在明天（D-074），到期场景要手动把 due 拨到过去。
    HTTP 层没有注入时间的入口，所以直接开一个会话改排期行。
    """
    knowledge_id = make_knowledge(client, title)
    with open_session() as session:
        schedule = review_service.get_schedule(session, knowledge_id)
        schedule.due = utcnow() - timedelta(hours=1)
        session.commit()
    return knowledge_id


# ================================================================ 到期


def test_new_knowledge_is_enrolled_but_not_due_until_tomorrow(
    client: TestClient,
) -> None:
    """D-060 + D-074 的 HTTP 证据：记一条知识自动有排期，但首复在明天——
    刚压缩完就要求复习，考的是短时记忆，不是理解。"""
    knowledge_id = make_knowledge(client, title="注意力机制")

    assert client.get("/api/review/due").json()["count"] == 0
    assert client.get("/api/review/due/count").json()["count"] == 0

    schedule = client.get(f"/api/review/{knowledge_id}/schedule").json()
    assert schedule["state"] == "learning"
    assert schedule["status"] == "active"
    due = datetime.fromisoformat(schedule["due"])
    assert timedelta(hours=23) <= due - utcnow() <= timedelta(days=1, minutes=1)


def test_due_knowledge_appears_in_due_list_without_the_answer(
    client: TestClient,
) -> None:
    """到期列表有它，且不带理解正文。"""
    knowledge_id = make_due(client, title="注意力机制")

    body = client.get("/api/review/due").json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["knowledge_id"] == knowledge_id
    assert item["title"] == "注意力机制"
    assert "走一小步" not in body.__str__(), "到期列表泄露了理解正文"

    assert client.get("/api/review/due/count").json()["count"] == 1


def test_rate_then_disappear_from_due(client: TestClient) -> None:
    knowledge_id = make_knowledge(client)

    response = client.post(
        f"/api/review/{knowledge_id}/rate",
        json={"rating": "good", "mode": "self_test"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rating"] == "good"
    assert body["stats"]["reps"] == 1

    assert client.get("/api/review/due/count").json()["count"] == 0


def test_rate_request_rejects_unknown_fields_and_values(client: TestClient) -> None:
    knowledge_id = make_knowledge(client)

    response = client.post(
        f"/api/review/{knowledge_id}/rate",
        json={"rating": "good", "mode": "self_test", "note": "多余字段"},
    )
    assert response.status_code == 422

    response = client.post(
        f"/api/review/{knowledge_id}/rate",
        json={"rating": "还行", "mode": "self_test"},
    )
    assert response.status_code == 422


def test_rate_suggested_rating_is_stored_but_never_read_by_scheduler(
    client: TestClient,
) -> None:
    """C7 的 HTTP 证据：AI 建议（哪怕和用户评分相反）不改变下一次到期。"""
    first_id = make_knowledge(client, title="第一份")
    second_id = make_knowledge(client, title="第二份")

    plain = client.post(
        f"/api/review/{first_id}/rate", json={"rating": "good", "mode": "self_test"}
    ).json()
    with_suggestion = client.post(
        f"/api/review/{second_id}/rate",
        json={
            "rating": "good",
            "mode": "ai_chat",
            "suggested_rating": "easy",
            "ai_comment": "答得不错",
        },
    ).json()

    # HTTP 层没法注入 now，两次请求差几毫秒。比较完整时间戳、容差 1 秒：
    # 建议若真改了排期，差的是分钟/天级，1 秒容差抓得住；又不像"截断到秒"
    # 那样在两次请求恰好跨秒时假失败。同秒精确相等的证据在 test_review_service
    # 的 C7 测试里（固定 now）。
    def due_dt(payload: dict) -> datetime:
        return datetime.fromisoformat(payload["due"])

    assert abs(due_dt(plain) - due_dt(with_suggestion)) <= timedelta(seconds=1)

    history = client.get(f"/api/review/{second_id}/history").json()
    assert history[0]["suggested_rating"] == "easy"
    assert history[0]["ai_comment"] == "答得不错"


def test_rate_unknown_knowledge_is_404_and_archived_is_409(client: TestClient) -> None:
    make_knowledge(client)

    response = client.post(
        "/api/review/不存在的ID/rate", json={"rating": "good", "mode": "self_test"}
    )
    assert response.status_code == 404

    knowledge_id = make_knowledge(client, title="要归档的")
    assert client.post(f"/api/knowledge/{knowledge_id}/archive").status_code == 200
    response = client.post(
        f"/api/review/{knowledge_id}/rate", json={"rating": "good", "mode": "self_test"}
    )
    assert response.status_code == 409
    assert "归档" in response.json()["detail"]


# ================================================================ 暂停 / 排期


def test_suspension_roundtrip(client: TestClient) -> None:
    knowledge_id = make_due(client)

    response = client.put(
        f"/api/review/{knowledge_id}/suspension", json={"suspended": True}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "suspended"
    assert client.get("/api/review/due/count").json()["count"] == 0

    schedule = client.get(f"/api/review/{knowledge_id}/schedule").json()
    assert schedule["status"] == "suspended"

    response = client.put(
        f"/api/review/{knowledge_id}/suspension", json={"suspended": False}
    )
    assert response.json()["status"] == "active"
    assert client.get("/api/review/due/count").json()["count"] == 1


def test_schedule_of_unknown_knowledge_is_404(client: TestClient) -> None:
    assert client.get("/api/review/不存在的ID/schedule").status_code == 404


# ================================================================ 词表锁定


@pytest.mark.parametrize("rating", RATINGS)
def test_rating_vocabulary_is_accepted_end_to_end(
    client: TestClient, rating: ReviewRating
) -> None:
    """四个大白话评级都能走通全链路，不多不少（D-061）。"""
    knowledge_id = make_knowledge(client, title=f"评级{rating}")

    response = client.post(
        f"/api/review/{knowledge_id}/rate", json={"rating": rating, "mode": "self_test"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["rating"] == rating


@pytest.mark.parametrize("mode", ["self_test", "ai_chat"])
def test_mode_vocabulary_is_accepted_end_to_end(
    client: TestClient, mode: ReviewMode
) -> None:
    knowledge_id = make_knowledge(client, title=f"模式{mode}")

    response = client.post(
        f"/api/review/{knowledge_id}/rate", json={"rating": "good", "mode": mode}
    )
    assert response.status_code == 200, response.text


def test_status_table_exactly_covers_the_database_vocabulary() -> None:
    """Literal 词表和数据库常量必须一致（D-003 的交叉锁，复习版）。"""
    from backend.db.models import (
        RATINGS,
        REVIEW_MODES,
        SCHEDULE_STATES,
        SCHEDULE_STATUSES,
    )

    assert set(RATINGS) == set(ReviewRating.__args__)
    assert set(REVIEW_MODES) == set(ReviewMode.__args__)
    assert set(SCHEDULE_STATES) == set(ScheduleState.__args__)
    assert set(SCHEDULE_STATUSES) == set(ScheduleStatus.__args__)


# ================================================================ 对话式复习（⑤）

# 约定：下面这些测试用假 Provider 走生产 HTTP 路径，不联网（D-038）。

AI_QUESTION = "按照你的理解，学习率设太大会发生什么？"
AI_FOLLOW_UP = "那什么时候会不收敛？"
USER_ANSWER = "会在最优点附近来回跳，甚至发散"


@pytest.fixture
def fake_ai_client(migrated_database: Path) -> Iterator[tuple[TestClient, FakeProvider]]:
    """带假 Provider 的 client：对话式复习的端点要花 AI 请求费，测试不真花。"""
    init_engine(get_settings().database_url)
    provider = FakeProvider()
    app = create_app(get_settings())
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as test_client:
        yield test_client, provider
    dispose_engine()


def start_review_chat(client: TestClient, knowledge_id: str) -> dict:
    response = client.post(f"/api/review/{knowledge_id}/session")
    assert response.status_code == 201, response.text
    return response.json()


def answer_over_http(client: TestClient, conversation_id: str, text: str) -> None:
    """用学习室同一条 SSE 通道答一轮，断言追问成功返回。"""
    with client.stream(
        "POST",
        f"/api/ai/conversations/{conversation_id}/messages",
        json={"text": text},
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]
    kinds = [event["type"] for event in events]
    assert kinds[-1] == "done"
    assert any(
        event["type"] == "chunk" and event["text"] for event in events
    ), "追问必须真的有内容"


def test_ai_review_session_full_flow_over_http(fake_ai_client) -> None:
    """D-063 的完整 HTTP 走通：出题 → 答题 → 建议建议 → 用户确认评级。"""
    client, provider = fake_ai_client
    knowledge_id = make_knowledge(client)

    provider.queue_stream([AI_QUESTION])
    started = start_review_chat(client, knowledge_id)
    assert started["first_question"] == AI_QUESTION

    provider.queue_stream([AI_FOLLOW_UP])
    answer_over_http(client, started["conversation_id"], USER_ANSWER)

    provider.queue('{"suggested_rating": "hard", "evaluation": "大意对，细节漏"}')
    suggestion = client.post(
        f"/api/review/{knowledge_id}/rating-suggestion",
        json={"conversation_id": started["conversation_id"]},
    )
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["suggested_rating"] == "hard"
    assert suggestion.json()["evaluation"] == "大意对，细节漏"

    # 用户没听 AI 的：自己评了 good。排期只认这个。
    final = client.post(
        f"/api/review/{knowledge_id}/rate",
        json={
            "rating": "good",
            "mode": "ai_chat",
            "suggested_rating": suggestion.json()["suggested_rating"],
            "ai_comment": suggestion.json()["evaluation"],
            "conversation_id": started["conversation_id"],
            "duration_ms": 65_000,
        },
    )
    assert final.status_code == 200, final.text

    history = client.get(f"/api/review/{knowledge_id}/history").json()
    assert history[0]["rating"] == "good"
    assert history[0]["suggested_rating"] == "hard"
    assert history[0]["ai_comment"] == "大意对，细节漏"
    assert history[0]["conversation_id"] == started["conversation_id"]
    assert history[0]["duration_ms"] == 65_000


def test_ai_review_conversation_is_hidden_from_study_room(fake_ai_client) -> None:
    client, provider = fake_ai_client
    knowledge_id = make_knowledge(client)

    provider.queue_stream([AI_QUESTION])
    started = start_review_chat(client, knowledge_id)

    listed = client.get("/api/ai/conversations").json()
    assert started["conversation_id"] not in [c["id"] for c in listed]
    # 但会话本身还在，详情页按 id 能取到问答原文
    detail = client.get(f"/api/ai/conversations/{started['conversation_id']}")
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["content"] == AI_QUESTION


def test_start_session_ai_failure_returns_error_and_leaves_nothing(
    fake_ai_client,
) -> None:
    """D-062 的 HTTP 半边：AI 挂了给结构化错误，库里没有空会话。"""
    client, provider = fake_ai_client
    knowledge_id = make_due(client)

    provider.queue_stream(AiTimeout())
    response = client.post(f"/api/review/{knowledge_id}/session")
    assert response.status_code == AI_ERROR_STATUS[AiTimeout.error_kind], response.text
    assert response.json()["error_kind"] == AiTimeout.error_kind

    assert client.get("/api/review/due/count").json()["count"] == 1, (
        "复习没做成，这条知识还得留在到期列表里"
    )
    assert client.get("/api/ai/conversations").json() == []


def test_rating_suggestion_rejects_empty_and_unknown_conversation(
    fake_ai_client,
) -> None:
    client, provider = fake_ai_client
    knowledge_id = make_knowledge(client)

    response = client.post(
        f"/api/review/{knowledge_id}/rating-suggestion",
        json={"conversation_id": "不存在的对话"},
    )
    assert response.status_code == 404

    started = client.post("/api/ai/conversations", json={"text": "跟复习无关"}).json()
    response = client.post(
        f"/api/review/{knowledge_id}/rating-suggestion",
        json={"conversation_id": started["id"]},
    )
    assert response.status_code == 409
    assert provider.chat_calls == [], "空对话不该花 AI 请求费"


def test_rate_with_unknown_conversation_is_404(client: TestClient) -> None:
    knowledge_id = make_knowledge(client)
    response = client.post(
        f"/api/review/{knowledge_id}/rate",
        json={
            "rating": "good",
            "mode": "ai_chat",
            "conversation_id": "不存在的对话",
        },
    )
    assert response.status_code == 404


# ================================================================ 约束锁定（⑦ 补）


def test_self_test_review_works_without_any_ai_configured(client: TestClient) -> None:
    """C4：复习是核心功能，完全没配 AI 也必须全链路可用。

    这个 client 没注入假 Provider，环境里密钥为空——AI 根本不可用，
    纯自测照样出结果、落历史、进排期。"""
    health = client.get("/api/health").json()
    assert health["ai_configured"] is False, "测试前提：AI 未配置"

    knowledge_id = make_knowledge(client, title="不用 AI 也能复习")
    response = client.post(
        f"/api/review/{knowledge_id}/rate",
        json={"rating": "good", "mode": "self_test", "duration_ms": 15_000},
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "self_test"

    history = client.get(f"/api/review/{knowledge_id}/history").json()
    assert len(history) == 1
    assert history[0]["rating"] == "good"
    assert history[0]["duration_ms"] == 15_000

    assert client.get("/api/review/due/count").json()["count"] == 0
