"""AI 接口的验收测试（阶段 2 第⑦步）。

全部测试用 `backend/services/ai/fake.py` 的假 Provider，**不联网、不需要密钥**
（DECISIONS D-038）。替换点是 `api/deps.get_provider` 这个依赖，
所以被测的仍是生产路径上"接口层 → service → provider"那段真代码。

四条"坏了不容易看出来"的行为单独锁死：

1. **流式结束时那句助手消息必须真的落库**（`test_done_arrives_after_the_commit`）。
   坏掉的表现：回答在浏览器里看得见，刷新一次就没了。
   根因是 FastAPI 可能在响应还在流的时候就把 `Depends(get_session)` 关掉——
   这个实现细节这里用一次实测钉住，而不是写进文档当承诺。

2. **`done` 事件一定在提交之后**（同一条测试）。
   提前发会让前端在数据没提交时就去重新拉历史，读到半截。

3. **处置接口的响应必须包含刚写入的那条**（`test_decision_response_contains_new_decision`）。
   `expire_on_commit=False` 下同一 Session 里已加载的集合不会自动刷新，
   少一次 refresh 就是"用户点了采纳但按钮没反应"。阶段 1 在这个坑上摔过三次。

4. **确认草稿幂等**（`test_confirm_draft_is_idempotent_over_http`）。
   前端按钮 disabled 只挡得住手滑，挡不住超时重发和刷新后重点。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from backend.api.deps import get_provider
from backend.config import get_settings
from backend.db.models import ERROR_KINDS
from backend.db.session import dispose_engine, init_engine
from backend.main import AI_ERROR_STATUS, create_app
from backend.services.ai.errors import (
    AiAuthRejected,
    AiBadRequest,
    AiSchemaInvalid,
    AiTimeout,
    AiUnavailable,
)
from backend.services.ai.fake import FakeProvider
from backend.services.ai.prompts import PROMPT_VERSION, system_message

IDEMPOTENCY_KEY = "repeated-key-value-0001"

DRAFT_JSON = {
    "title": "梯度下降",
    "domain": "机器学习",
    "summary": "",
    "user_understanding": "每步往下降最快的方向走一小步",
    "ai_supplements": ["AI 补的一句：学习率决定步长"],
    "reasoning": None,
    "open_questions": "为什么一定要可导？",
    "suggested_sources": [
        {
            "source_type": "course",
            "title": "3Blue1Brown 神经网络第 1 章",
            "url_or_reference": "https://example.com/v1",
            "note": None,
        }
    ],
}

REVIEW_JSON = {
    "correct_points": [{"point": "下降方向取负梯度是对的", "why": "负梯度是局部下降最快方向"}],
    "errors": [
        {
            "claim": "学习率越大收敛越快",
            "correction": "过大反而会在最优点两侧震荡",
            "evidence": "更新式 x ← x − η·∇f 中 η 直接放大步长",
        }
    ],
    "omissions": [],
    "follow_up_questions": ["为什么需要损失函数可导？"],
    "needs_verification": [],
}


# ==================================================================== 夹具


@pytest.fixture
def fake_ai(migrated_database: Path) -> Iterator[tuple[TestClient, FakeProvider]]:
    """生产路由 + 假 Provider。返回 (client, provider)，provider 供排结果与记账。"""
    init_engine(get_settings().database_url)
    provider = FakeProvider()
    app = create_app(get_settings())
    app.dependency_overrides[get_provider] = lambda: provider
    with TestClient(app) as test_client:
        yield test_client, provider
    dispose_engine()


@pytest.fixture
def raw_db(migrated_database: Path) -> Iterator[sqlite3.Connection]:
    """绕过 ORM 直连，作为"库里到底存了什么"的独立证据。"""
    connection = sqlite3.connect(migrated_database)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


# ==================================================================== 小工具


def make_conversation(client: TestClient, text: str = "什么是梯度下降？") -> str:
    response = client.post("/api/ai/conversations", json={"text": text})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_knowledge(client: TestClient, **overrides: str) -> str:
    payload = {
        "title": "梯度下降",
        "user_understanding": "每步往下降最快的方向走一小步",
        "domain": "机器学习",
        **overrides,
    }
    response = client.post("/api/knowledge", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def conversation_detail(client: TestClient, conversation_id: str) -> dict:
    response = client.get(f"/api/ai/conversations/{conversation_id}")
    assert response.status_code == 200, response.text
    return response.json()


def sse_events(body: str) -> list[dict]:
    """按 SSE 的解析约定切事件：一条事件是 `data:` 行 + 空行。"""
    events: list[dict] = []
    for block in body.split("\n\n"):
        stripped = block.strip()
        if stripped.startswith("data:"):
            events.append(json.loads(stripped[len("data:") :].strip()))
    return events


def post_message(client: TestClient, conversation_id: str, text: str) -> Response:
    return client.post(f"/api/ai/conversations/{conversation_id}/messages", json={"text": text})


def chat_once(
    client: TestClient, provider: FakeProvider, conversation_id: str, text: str, chunks: list[str]
) -> list[dict]:
    """排好碎片、发一句、返回解析后的事件列表。"""
    provider.queue_stream(chunks)
    response = post_message(client, conversation_id, text)
    assert response.status_code == 200, response.text
    return sse_events(response.text)


def make_review(client: TestClient, provider: FakeProvider, knowledge_id: str) -> str:
    provider.queue(REVIEW_JSON)
    response = client.post("/api/ai/reviews", json={"knowledge_id": knowledge_id})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def decide(client: TestClient, review_id: str, category: str, index: int, decision: str) -> dict:
    response = client.put(
        f"/api/ai/reviews/{review_id}/decisions",
        json={"category": category, "item_index": index, "decision": decision},
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client: TestClient, draft: dict, *, key: str = IDEMPOTENCY_KEY):
    return client.post("/api/ai/drafts/confirm", json={"draft": draft, "idempotency_key": key})


def extracted_draft(client: TestClient, provider: FakeProvider) -> tuple[str, dict]:
    """跑完"开一场对话 → 聊一句 → 提取草稿"，返回 (会话 id, 草稿)。"""
    conversation_id = make_conversation(client)
    chat_once(client, provider, conversation_id, "它和导数是什么关系", ["答"])
    provider.queue(DRAFT_JSON)
    response = client.post(f"/api/ai/conversations/{conversation_id}/extract")
    assert response.status_code == 200, response.text
    return conversation_id, response.json()


# ================================================================ 会话：创建与读取


def test_create_conversation_titles_itself_from_user_text(fake_ai) -> None:
    """标题取自用户第一句话，**没有调用 AI**（D-042：不为起名花一次钱）。"""
    client, provider = fake_ai
    body = conversation_detail(client, make_conversation(client, "梯度下降到底在算什么？"))

    assert body["title"] == "梯度下降到底在算什么？"
    assert provider.remaining() == 0
    assert provider.chat_calls == [] and provider.stream_calls == []


def test_create_conversation_truncates_title_to_column_width(fake_ai) -> None:
    """列宽 String(100)：超长必须截断，而不是让 SQLite 在写入时炸成 500。"""
    client, _ = fake_ai
    assert len(conversation_detail(client, make_conversation(client, "长" * 300))["title"]) == 100


def test_create_conversation_rejects_blank_text(fake_ai) -> None:
    client, _ = fake_ai
    assert client.post("/api/ai/conversations", json={"text": "   "}).status_code == 422


def test_create_conversation_rejects_unknown_field(fake_ai) -> None:
    """请求侧 extra=forbid：前端拼错字段名要当场报，不能静默丢掉。"""
    client, _ = fake_ai
    response = client.post("/api/ai/conversations", json={"text": "问一句", "titel": "拼错了"})
    assert response.status_code == 422
    assert "titel" in response.text


def test_list_conversations_is_newest_first(fake_ai) -> None:
    client, _ = fake_ai
    first = make_conversation(client, "第一个问题")
    second = make_conversation(client, "第二个问题")

    listed = client.get("/api/ai/conversations").json()
    assert [item["title"] for item in listed] == ["第二个问题", "第一个问题"]
    assert {item["id"] for item in listed} == {second, first}


def test_detail_orders_messages_by_seq(fake_ai) -> None:
    client, provider = fake_ai
    conversation_id = make_conversation(client)

    events = chat_once(client, provider, conversation_id, "什么是梯度下降？", ["你好", "，我们", "开始"])
    assert [e["text"] for e in events if e["type"] == "chunk"] == ["你好", "，我们", "开始"]

    messages = conversation_detail(client, conversation_id)["messages"]
    assert [m["seq"] for m in messages] == [1, 2]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    # 库里存的是拼好的完整文本，不是碎片
    assert messages[1]["content"] == "你好，我们开始"


def test_detail_keeps_content_verbatim_including_line_breaks(fake_ai) -> None:
    """多段理解不能被接成一行：换行是用户写东西时的结构。"""
    client, provider = fake_ai
    conversation_id = make_conversation(client)
    chat_once(client, provider, conversation_id, "问", ["第一段\n\n第二段"])

    assert conversation_detail(client, conversation_id)["messages"][1]["content"] == "第一段\n\n第二段"


def test_detail_with_unknown_id_is_404(fake_ai) -> None:
    client, _ = fake_ai
    assert client.get("/api/ai/conversations/no-such-id").status_code == 404


# ================================================================ 流式对话


def test_stream_sets_event_stream_type_and_disables_cache(fake_ai) -> None:
    """`Cache-Control: no-cache`：流式响应被中间层缓存下来会整段卡住。"""
    client, provider = fake_ai
    provider.queue_stream(["一句"])
    response = post_message(client, make_conversation(client), "问一句")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"


def test_stream_persists_assistant_message_after_the_last_chunk(fake_ai) -> None:
    """⚠️ 本文件最重要的一条：Session 必须在整条流期间活着。

    `get_session` 是 dependency-with-yield。如果框架在开始发送响应体之前
    就执行完 `finally: session.close()`，最后那次 commit 会写不进去——
    现象是"回答看得见、刷新就没了"，而直接调 service 的单元测试永远发现不了。
    """
    client, provider = fake_ai
    provider.queue_stream(["第一段", "第二段"])
    conversation_id = make_conversation(client)

    post_message(client, conversation_id, "问一句")

    rows = conversation_detail(client, conversation_id)["messages"]
    assert [r["content"] for r in rows] == ["问一句", "第一段第二段"]
    assert rows[1]["model"] == "fake-model"
    assert rows[1]["error_kind"] is None
    # duration_ms 由 service 量：ai_messages 那一列不该永远空着
    assert rows[1]["duration_ms"] is not None
    assert provider.remaining() == 0


def test_done_arrives_after_the_commit(fake_ai, raw_db: sqlite3.Connection) -> None:
    """看到 `done` 的那一刻，助手消息必须已经在库里。

    前端把"收到 done"当作"可以去重新拉历史"的信号。顺序反了，
    拉回来的就是半截对话——而且只在真机上偶发，测试还很难复现，
    所以这里逐行读流，在 done 那一行当场查库。
    """
    client, provider = fake_ai
    provider.queue_stream(["答"])
    conversation_id = make_conversation(client)

    seen_done = False
    with client.stream(
        "POST", f"/api/ai/conversations/{conversation_id}/messages", json={"text": "问"}
    ) as response:
        for line in response.iter_lines():
            if line != 'data: {"type": "done"}':
                continue
            seen_done = True
            stored = raw_db.execute(
                "SELECT content FROM ai_messages WHERE role = 'assistant'"
            ).fetchall()
            assert [row[0] for row in stored] == ["答"], "done 发出来时数据还没提交"
    assert seen_done, "流里没有 done 事件"


def test_chunks_arrive_as_events_before_done(fake_ai) -> None:
    client, provider = fake_ai
    provider.queue_stream(["甲", "乙"])
    conversation_id = make_conversation(client)

    types = [event["type"] for event in sse_events(post_message(client, conversation_id, "问").text)]
    assert types == ["chunk", "chunk", "done"]


def test_stream_body_is_not_ascii_escaped(fake_ai) -> None:
    """ensure_ascii=False：中文转 \\uXXXX 会让流变大好几倍，还毫无收益。"""
    client, provider = fake_ai
    provider.queue_stream(["梯度"])
    conversation_id = make_conversation(client)

    assert "梯度" in post_message(client, conversation_id, "问").text


def test_system_prompt_is_sent_but_never_persisted(fake_ai) -> None:
    """请求里有 system（它能从 prompts.py 重建，落库违反 C6 最小必要），
    但历史接口里绝不能出现它。"""
    client, provider = fake_ai
    conversation_id = make_conversation(client)
    chat_once(client, provider, conversation_id, "问", ["答"])

    request = provider.stream_calls[-1]
    assert request[0].role == "system"
    assert request[0].content == system_message("study_chat").content
    assert {m["role"] for m in conversation_detail(client, conversation_id)["messages"]} == {
        "user",
        "assistant",
    }


def test_second_round_sends_previous_round_as_context(fake_ai) -> None:
    client, provider = fake_ai
    conversation_id = make_conversation(client)
    provider.queue_stream(["一答"]).queue_stream(["二答"])

    post_message(client, conversation_id, "第一问")
    post_message(client, conversation_id, "第二问")

    sent = [(m.role, m.content) for m in provider.stream_calls[-1][1:]]
    assert sent == [("user", "第一问"), ("assistant", "一答"), ("user", "第二问")]
    assert [m["seq"] for m in conversation_detail(client, conversation_id)["messages"]] == [
        1,
        2,
        3,
        4,
    ]


def test_stream_failure_keeps_the_user_message_and_records_the_kind(fake_ai) -> None:
    """AI 挂了不能把用户那句一起丢掉；失败也要落一条带 error_kind 的助手消息。"""
    client, provider = fake_ai
    provider.queue_stream(AiTimeout())
    conversation_id = make_conversation(client)

    response = post_message(client, conversation_id, "我的问题原文")
    assert response.status_code == 200  # 响应头早发出去了，改不成 504
    error = sse_events(response.text)[-1]
    assert error["type"] == "error"
    assert error["error_kind"] == "timeout"
    assert error["message"] == AiTimeout.user_message

    rows = conversation_detail(client, conversation_id)["messages"]
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "我的问题原文"
    assert rows[1]["error_kind"] == "timeout"
    assert rows[1]["content"] == AiTimeout.user_message


def test_failed_reply_is_not_sent_back_as_context(fake_ai) -> None:
    """三轮：失败 → 成功 → 检查上下文。

    正反两个方向都要断言。只查"不含失败的那条"和什么都不过滤长得一样——
    阶段 2 第⑥步的变异测试正是靠"缺正面断言"抓到过漏过滤。
    """
    client, provider = fake_ai
    conversation_id = make_conversation(client)
    provider.queue_stream(AiUnavailable()).queue_stream(["二答"]).queue_stream(["三答"])

    for text in ("第一问", "第二问", "第三问"):
        post_message(client, conversation_id, text)

    sent = [(m.role, m.content) for m in provider.stream_calls[-1][1:]]
    assert all(content != AiUnavailable.user_message for _, content in sent), (
        "错误文案被当成对话历史发回去了"
    )
    assert sent == [
        # 失败那轮**用户那句仍然进上下文**：它没得到回答，问题还在那儿
        ("user", "第一问"),
        ("user", "第二问"),
        ("assistant", "二答"),
        ("user", "第三问"),
    ]


def test_unexpected_error_still_closes_the_stream_with_an_event(fake_ai) -> None:
    """非 AiError 的意外（假 Provider 队列排空 = 测试/代码不同步）也要给出事件。

    半途掐断而不说话，用户只会看到"转圈转了很久然后没了"。
    """
    client, _ = fake_ai
    conversation_id = make_conversation(client)

    events = sse_events(post_message(client, conversation_id, "问").text)
    assert events[-1]["type"] == "error"
    assert events[-1]["error_kind"] == "internal"
    # 用户那句仍然留下了：失败不该把他的输入一起吞掉
    assert [m["role"] for m in conversation_detail(client, conversation_id)["messages"]] == ["user"]


def test_message_to_unknown_conversation_is_plain_404(fake_ai) -> None:
    """会话不存在必须是 404，而不是"200 + 流里报 internal"：
    状态头一旦发出就没有 404 了，所以存在性检查放在进入流之前。"""
    client, provider = fake_ai
    provider.queue_stream(["不该被调用"])

    assert post_message(client, "nope", "问").status_code == 404
    assert provider.remaining() == 1


def test_message_rejects_empty_text_without_calling_ai(fake_ai) -> None:
    client, provider = fake_ai
    provider.queue_stream(["不该被调用"])
    conversation_id = make_conversation(client)

    assert post_message(client, conversation_id, "").status_code == 422
    assert provider.remaining() == 1


# ================================================================ 清空历史


def test_clear_history_requires_explicit_confirm(fake_ai) -> None:
    """唯一的硬删除入口（D-041）必须在接口层就要求显式确认。"""
    client, _ = fake_ai
    make_conversation(client)

    assert client.delete("/api/ai/conversations").status_code == 422
    assert client.delete("/api/ai/conversations", params={"confirm": "no"}).status_code == 422

    response = client.delete("/api/ai/conversations", params={"confirm": "yes"})
    assert response.status_code == 200
    assert response.json() == {"deleted_conversations": 1}
    assert client.get("/api/ai/conversations").json() == []


def test_clear_history_leaves_knowledge_and_reviews(fake_ai, raw_db: sqlite3.Connection) -> None:
    """删的是对话，不是知识。审查记录挂在知识上，也不该跟着会话走。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)
    make_conversation(client)

    client.delete("/api/ai/conversations", params={"confirm": "yes"})

    assert raw_db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 1
    assert client.get(f"/api/ai/reviews/{review_id}").status_code == 200
    # 消息行靠数据库级 ON DELETE CASCADE 跟着会话走，这里用裸 SQL 交叉验证
    assert raw_db.execute("SELECT COUNT(*) FROM ai_messages").fetchone()[0] == 0


# ================================================================ 工作流 B：提取与确认


def test_extract_returns_draft_without_persisting_anything(fake_ai, raw_db) -> None:
    """⚠️ C2：草稿不落库。用户中途关页面，什么都不该留。"""
    client, provider = fake_ai

    _, draft = extracted_draft(client, provider)

    assert draft["ai_supplements"] == ["AI 补的一句：学习率决定步长"]
    assert "id" not in draft, "草稿不该有自己的 id——它不是记录"
    for table in ("knowledge", "knowledge_versions", "sources", "idempotency_keys"):
        assert raw_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_extract_on_empty_conversation_is_409_and_costs_nothing(fake_ai) -> None:
    """空对话提取注定没有内容，而这一次调用是要花钱的（D-042）。"""
    client, provider = fake_ai
    provider.queue(DRAFT_JSON)
    conversation_id = make_conversation(client)

    response = client.post(f"/api/ai/conversations/{conversation_id}/extract")
    assert response.status_code == 409
    assert "先聊几句" in response.json()["detail"]
    assert provider.remaining() == 1


def test_extract_sends_only_this_conversation(fake_ai) -> None:
    """C5 最小必要（对话侧）：发出去的就是这一段，别的会话一个字都不带。"""
    client, provider = fake_ai
    make_conversation(client, "别的话题：复变函数")
    conversation_id = make_conversation(client, "梯度下降")
    provider.queue_stream(["答"])
    post_message(client, conversation_id, "它和导数什么关系")

    provider.queue(DRAFT_JSON)
    assert client.post(f"/api/ai/conversations/{conversation_id}/extract").status_code == 200

    sent = " ".join(m.content for m in provider.chat_calls[-1])
    assert "它和导数什么关系" in sent
    assert "复变函数" not in sent


def test_extract_rejects_unknown_conversation(fake_ai) -> None:
    """不存在必须是 404，而不是"这段对话还没有内容"那种误导性的 409。"""
    client, provider = fake_ai
    provider.queue(DRAFT_JSON)
    assert client.post("/api/ai/conversations/nope/extract").status_code == 404


def test_confirm_draft_creates_knowledge_as_201(fake_ai) -> None:
    client, provider = fake_ai
    _, draft = extracted_draft(client, provider)

    response = confirm(client, draft)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["replayed"] is False
    assert body["knowledge"]["source_type"] == "conversation"
    assert body["knowledge"]["versions"][0]["created_by"] == "user"
    assert body["knowledge"]["sources"][0]["source_type"] == "course"


def test_confirm_draft_never_carries_ai_supplements_into_the_record(fake_ai) -> None:
    """⚠️ C1 在接口层的表现：AI 补的话**没有任何一列**能收下它。

    用户要采纳那句就得自己打字写进 user_understanding——
    程序拼接出来的文本说不清是谁说的。
    """
    client, provider = fake_ai
    _, draft = extracted_draft(client, provider)
    draft["user_understanding"] = "我自己说的话，只写了一半"

    version = confirm(client, draft, key="c1-supplements-key").json()["knowledge"]["versions"][0]

    assert "AI 补的一句" not in version["user_understanding"]
    assert version["user_understanding"] == "我自己说的话，只写了一半"
    assert version["ai_revision"] is None


def test_confirm_draft_is_idempotent_over_http(fake_ai, raw_db) -> None:
    """同一个幂等键提交两次：一次 201、一次 200，库里只有一条知识。"""
    client, _ = fake_ai
    draft = dict(DRAFT_JSON)

    first = confirm(client, draft)
    second = confirm(client, draft)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, "重放不能再报 201，那等于说'我又新建了一条'"
    assert second.json()["replayed"] is True
    assert second.json()["knowledge"]["id"] == first.json()["knowledge"]["id"]
    assert raw_db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 1


def test_confirm_draft_with_two_different_keys_creates_two_records(fake_ai) -> None:
    """幂等键是**每次提交一个**，不是每个会话一个、更不是全局一个。

    写错的地方很具体：接口层如果把键名硬编码或忘了往后传，
    用户"再存一条不同的"就会被当成重放，界面显示成功但库里没新增。
    """
    client, _ = fake_ai

    first = confirm(client, dict(DRAFT_JSON), key="first-key-value-0001")
    second = confirm(client, dict(DRAFT_JSON), key="second-key-value-0002")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["knowledge"]["id"] != second.json()["knowledge"]["id"]


def test_confirm_draft_rejects_key_too_long_for_the_column(fake_ai) -> None:
    """`idempotency_keys.key` 是 String(64)：超长要在边界挡住，
    而不是让 SQLite 写入时炸成 500。"""
    client, _ = fake_ai
    assert confirm(client, dict(DRAFT_JSON), key="k" * 65).status_code == 422


def test_confirm_empty_draft_is_422(fake_ai) -> None:
    """AI 从没聊清楚的对话里提不出理解时确实会返回空——
    这种情况不许存成一条空记录，要让用户自己补一句。"""
    client, _ = fake_ai
    draft = dict(DRAFT_JSON)
    draft["user_understanding"] = ""

    response = confirm(client, draft, key="empty-draft-key")
    assert response.status_code == 422
    assert "请自己写一句" in response.json()["detail"]


def test_confirm_rejects_typed_typo_in_the_request_wrapper(fake_ai) -> None:
    """外层是 extra=forbid：拼错的 `idempotencykey` 必须当场 422。"""
    client, _ = fake_ai
    response = client.post(
        "/api/ai/drafts/confirm",
        json={"draft": DRAFT_JSON, "idempotencykey": "拼错了"},
    )
    assert response.status_code == 422


def test_confirm_still_enforces_the_knowledge_rules(fake_ai) -> None:
    """最后一道闸门是 `KnowledgeCreate`：草稿层面宽松，落库一样都不松。
    这里用"只有标点符号的理解"证明它没有被绕过去。"""
    client, _ = fake_ai
    draft = dict(DRAFT_JSON)
    draft["user_understanding"] = "。。。"

    assert confirm(client, draft, key="punctuation-key").status_code == 422


# ================================================================ 工作流 C：审查


def test_review_lands_with_result_and_version_pointer(fake_ai) -> None:
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    body = client.get(f"/api/ai/reviews/{review_id}").json()
    assert body["knowledge_id"] == knowledge_id
    assert body["version_number"] == 1
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["model"] == "fake-model"
    assert body["decisions"] == []
    assert body["is_empty"] is False
    assert body["result"]["errors"][0]["correction"].startswith("过大反而会")
    # token 数从 provider 的 AiResult 一路传下来，那两列不该永远空着
    assert body["prompt_tokens"] == 12 and body["completion_tokens"] == 34
    assert provider.remaining() == 0


def test_empty_review_is_reported_as_empty_not_as_praise(fake_ai) -> None:
    """五个类别全空是合法结果。前端要能说"AI 没提意见"，
    而不是显示一个空白区，更不能显示成"AI 说你全对"。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    provider.queue({})
    empty_id = client.post("/api/ai/reviews", json={"knowledge_id": knowledge_id}).json()["id"]

    assert client.get(f"/api/ai/reviews/{review_id}").json()["is_empty"] is False
    assert client.get(f"/api/ai/reviews/{empty_id}").json()["is_empty"] is True


def test_review_of_unknown_knowledge_is_404_without_calling_ai(fake_ai) -> None:
    client, provider = fake_ai
    provider.queue(REVIEW_JSON)

    assert client.post("/api/ai/reviews", json={"knowledge_id": "nope"}).status_code == 404
    assert provider.remaining() == 1, "知识都不存在，请求不该已经发出去"


def test_review_sends_only_the_current_version(fake_ai) -> None:
    """C5 最小必要（审查侧）：历史版本不发，别的知识也不发。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client, user_understanding="第一版的说法")
    client.put(
        f"/api/knowledge/{knowledge_id}",
        json={"user_understanding": "第二版的说法", "change_reason": "想清楚了"},
    )

    make_review(client, provider, knowledge_id)

    sent = " ".join(m.content for m in provider.chat_calls[-1])
    assert "第二版的说法" in sent
    assert "第一版的说法" not in sent


def test_list_reviews_requires_knowledge_id_and_keeps_history(fake_ai) -> None:
    """⚠️ 旧审查不删：处置按 (category, item_index) 定位，
    删掉旧审查会让已有的指针对到别的条目上。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)

    assert client.get("/api/ai/reviews").status_code == 422
    assert client.get("/api/ai/reviews", params={"knowledge_id": knowledge_id}).json() == []

    first = make_review(client, provider, knowledge_id)
    second = make_review(client, provider, knowledge_id)

    listed = client.get("/api/ai/reviews", params={"knowledge_id": knowledge_id}).json()
    assert {item["id"] for item in listed} == {first, second}


def test_list_reviews_does_not_leak_other_knowledges_reviews(fake_ai) -> None:
    client, provider = fake_ai
    mine = make_knowledge(client)
    theirs = make_knowledge(client, title="过拟合", user_understanding="模型记住了噪声")
    make_review(client, provider, theirs)
    mine_review = make_review(client, provider, mine)

    listed = client.get("/api/ai/reviews", params={"knowledge_id": mine}).json()
    assert [item["id"] for item in listed] == [mine_review]


def test_decision_response_contains_new_decision(fake_ai) -> None:
    """⚠️ 本文件第二重要的一条：处置接口返回的必须是刷新后的集合。

    `decide_review_item` 内部已经加载过一次 decisions，同一 Session 里
    `expire_on_commit=False` 不会自动刷新那个集合。少一次显式 refresh，
    响应里就看不到刚写入的那条，用户看到的现象是"点了采纳但按钮没反应"。
    """
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    decide(client, review_id, "errors", 0, "accepted")
    body = decide(client, review_id, "correct_points", 0, "disagreed")

    pairs = {(d["category"], d["item_index"], d["decision"]) for d in body["decisions"]}
    assert pairs == {("errors", 0, "accepted"), ("correct_points", 0, "disagreed")}


def test_decision_overwrites_instead_of_inserting(fake_ai, raw_db) -> None:
    """改主意是覆盖。唯一约束 (review_id, category, item_index) 不许被绕开。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    for decision in ("accepted", "ignored", "disagreed", "accepted"):
        assert [d["decision"] for d in decide(client, review_id, "errors", 0, decision)["decisions"]] == [
            decision
        ]

    assert raw_db.execute("SELECT COUNT(*) FROM ai_review_decisions").fetchone()[0] == 1


def test_three_decisions_are_equal_not_defaulted(fake_ai) -> None:
    """C7：忽略和不同意都是普通操作。
    三个类别、三种处置都能写、都能覆盖，没有任何一个需要额外理由。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    for category, decision in (
        ("correct_points", "accepted"),
        ("errors", "ignored"),
        ("follow_up_questions", "disagreed"),
    ):
        body = decide(client, review_id, category, 0, decision)
        matched = [d for d in body["decisions"] if d["category"] == category]
        assert [d["decision"] for d in matched] == [decision]


def test_decision_rejects_unknown_category_and_index(fake_ai) -> None:
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    assert client.put(
        f"/api/ai/reviews/{review_id}/decisions",
        json={"category": "wrong_things", "item_index": 0, "decision": "accepted"},
    ).status_code == 422
    assert client.put(
        f"/api/ai/reviews/{review_id}/decisions",
        json={"category": "errors", "item_index": 1, "decision": "accepted"},
    ).status_code == 404
    assert client.put(
        f"/api/ai/reviews/{review_id}/decisions",
        json={"category": "errors", "item_index": -1, "decision": "accepted"},
    ).status_code == 422
    assert client.put(
        f"/api/ai/reviews/{review_id}/decisions",
        json={"category": "errors", "item_index": 0, "decision": "adopted"},
    ).status_code == 422


def test_decision_on_unknown_review_is_404(fake_ai) -> None:
    client, _ = fake_ai
    assert client.put(
        "/api/ai/reviews/no-such-review/decisions",
        json={"category": "errors", "item_index": 0, "decision": "accepted"},
    ).status_code == 404


def test_accept_creates_a_version_from_user_words_only(fake_ai) -> None:
    """⚠️ C1：新版本的用户理解只来自请求里用户打的那句；
    AI 的话只能待在 `ai_revision` 这一列。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)
    decide(client, review_id, "errors", 0, "accepted")

    response = client.post(
        f"/api/ai/reviews/{review_id}/accept",
        json={
            "new_understanding": "学习率太大会在最优点两侧来回震荡，得取小一点",
            "change_reason": "采纳了关于学习率的那条更正",
        },
    )
    assert response.status_code == 200, response.text

    version = response.json()["versions"][-1]
    assert version["version_number"] == 2
    assert version["created_by"] == "ai_confirmed"
    assert version["user_understanding"] == "学习率太大会在最优点两侧来回震荡，得取小一点"
    # 留档的是用户点过采纳的那条原话，只进 ai_revision
    assert "过大反而会在最优点两侧震荡" in version["ai_revision"]
    assert version["change_reason"] == "采纳了关于学习率的那条更正"


def test_accept_without_rewriting_understanding_creates_no_version(fake_ai) -> None:
    """D-030 在这条路径上同样成立：内容没变时版本号不许 +1（假历史）。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)
    current = client.get(f"/api/knowledge/{knowledge_id}").json()["current_version_id"]

    body = client.post(
        f"/api/ai/reviews/{review_id}/accept",
        json={"new_understanding": "每步往下降最快的方向走一小步"},
    ).json()

    assert len(body["versions"]) == 1
    assert body["current_version_id"] == current


def test_accept_ignores_items_the_user_never_clicked(fake_ai) -> None:
    """采纳了哪些条目由服务端读数据库里的处置，不接受调用方传列表——
    否则前端可以顺手塞进一条从没展示过的建议。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)
    decide(client, review_id, "correct_points", 0, "accepted")

    version = client.post(
        f"/api/ai/reviews/{review_id}/accept",
        json={"new_understanding": "换成我自己组织的一句话来说它"},
    ).json()["versions"][-1]

    assert "下降方向取负梯度是对的" in version["ai_revision"]
    # errors[0] 一个字都没被采纳，就不该出现在留档里
    assert "学习率越大收敛越快" not in version["ai_revision"]


def test_accept_cannot_be_retargeted_at_another_knowledge(fake_ai) -> None:
    """路由只认 review_id，`knowledge_id` 由服务端从审查记录取。
    所以"把 A 的审查采纳进 B"这个请求在接口上根本表达不出来。"""
    client, provider = fake_ai
    mine = make_knowledge(client)
    review_id = make_review(client, provider, mine)

    response = client.post(
        f"/api/ai/reviews/{review_id}/accept",
        json={"new_understanding": "我自己重写的一句理解", "knowledge_id": "别人的 id"},
    )
    assert response.status_code == 422, "多传一个 knowledge_id 想改道，必须被拒"


def test_accept_rejects_punctuation_only_understanding(fake_ai) -> None:
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    response = client.post(f"/api/ai/reviews/{review_id}/accept", json={"new_understanding": "，。"})
    assert response.status_code == 422


def test_accept_after_edited_understanding_updates_search_index(fake_ai) -> None:
    """正文变了索引必须跟着同步（D-006）。这个 bug 在库里看不出来，
    只体现在搜不到——阶段 1 实测踩过，所以每次都通过搜索接口断言。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    client.post(
        f"/api/ai/reviews/{review_id}/accept",
        json={"new_understanding": "赫芬达尔指数衡量集中度的那个东西"},
    )

    hit = client.get("/api/knowledge/search", params={"q": "赫芬达尔指数"}).json()
    assert [item["knowledge_id"] for item in hit["items"]] == [knowledge_id]


# ================================================================ 失败映射与隐私


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (AiUnavailable(), 502),
        (AiAuthRejected(), 502),
        (AiBadRequest(status_code=400), 502),
        (AiSchemaInvalid(("errors",)), 502),
        (AiTimeout(), 504),
    ],
)
def test_ai_failures_map_to_status_and_carry_their_kind(fake_ai, error, expected_status) -> None:
    """五类失败各有状态码 + `error_kind`。统一报 500 的话，
    用户分不清该重试、该改网络、还是该去设置页换 key（AI_WORKFLOWS 第八节）。"""
    client, provider = fake_ai
    conversation_id, _ = extracted_draft(client, provider)
    provider.queue(error)

    response = client.post(f"/api/ai/conversations/{conversation_id}/extract")
    assert response.status_code == expected_status, response.text

    body = response.json()
    assert body["error_kind"] == error.error_kind
    assert body["detail"] == error.user_message


def test_status_table_exactly_covers_the_database_vocabulary() -> None:
    """和 `ai_messages.error_kind` 一一对应。少一行的后果是那一类失败冒成 500。"""
    assert set(AI_ERROR_STATUS) == set(ERROR_KINDS)


def test_failure_response_carries_no_diagnostic_detail(fake_ai) -> None:
    """`exc.detail`（状态码、字段名这些排查信息）进日志不进响应：
    对外按最小必要给，而且少一个将来会带错东西的地方。"""
    client, provider = fake_ai
    conversation_id, _ = extracted_draft(client, provider)
    provider.queue(AiBadRequest(status_code=400, detail="服务端返回状态码 400"))

    response = client.post(f"/api/ai/conversations/{conversation_id}/extract")
    assert "服务端返回状态码" not in response.text


def test_malformed_ai_output_writes_nothing(fake_ai, raw_db) -> None:
    """C3：AI 给的东西过不了校验就一行都不留。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    provider.queue('{"correct_points": "不是数组"}')

    response = client.post("/api/ai/reviews", json={"knowledge_id": knowledge_id})
    assert response.status_code == 502
    assert response.json()["error_kind"] == "schema_invalid"
    assert raw_db.execute("SELECT COUNT(*) FROM ai_reviews").fetchone()[0] == 0


def test_unconfigured_ai_is_409_not_500(client: TestClient) -> None:
    """C4：没配 AI 时核心功能照常，而 AI 功能给出可操作的下一步。

    这里用没覆盖依赖的普通 `client` 夹具——conftest 已经把三个 PKOS_AI_* 清空，
    所以走的是真实的 `factory.build_provider`。
    """
    conversation_id = make_conversation(client)
    response = post_message(client, conversation_id, "问一句")
    assert response.status_code == 409, response.text

    body = response.json()
    assert body["error_kind"] == "not_configured"
    assert set(body["missing"]) == {"PKOS_AI_BASE_URL", "PKOS_AI_MODEL"}
    assert "设置页" in body["detail"]


def test_knowledge_endpoints_still_work_with_ai_unconfigured(client: TestClient) -> None:
    """⚠️ C4 的硬证据：没配 AI 也照样能记知识、能搜。阶段 2 不许弄坏阶段 1。"""
    knowledge_id = make_knowledge(client)
    assert client.get(f"/api/knowledge/{knowledge_id}").status_code == 200
    assert client.get("/api/knowledge/search", params={"q": "梯度"}).status_code == 200
    assert client.get("/api/health").json()["ai_configured"] is False


def test_extract_and_review_without_ai_are_409_and_persist_nothing(client: TestClient) -> None:
    """C4 的另一半：未配置 AI 不只挡发消息。提取、审查也得给出
    同一个可操作的 409（指名缺哪个环境变量），并且审查一行都不落。
    之前只锁了发消息端点——⑨补的缺口。"""
    conversation_id = make_conversation(client)
    knowledge_id = make_knowledge(client)

    extract_response = client.post(f"/api/ai/conversations/{conversation_id}/extract")
    review_response = client.post("/api/ai/reviews", json={"knowledge_id": knowledge_id})

    assert extract_response.status_code == 409, extract_response.text
    assert review_response.status_code == 409, review_response.text
    assert extract_response.json()["error_kind"] == "not_configured"
    assert review_response.json()["error_kind"] == "not_configured"
    assert client.get(
        "/api/ai/reviews", params={"knowledge_id": knowledge_id}
    ).json() == []


def test_the_three_not_found_cases_say_different_things(fake_ai) -> None:
    """三种"找不到"状态码都是 404，但**文案必须不一样**：
    找不到会话、找不到审查、审查里没有那个条目，用户下一步做的事完全不同。
    合并成一句"不存在"就等于没报（这条纪律和 error_kind 分开是一个道理）。"""
    client, provider = fake_ai
    knowledge_id = make_knowledge(client)
    review_id = make_review(client, provider, knowledge_id)

    conversation_copy = client.get("/api/ai/conversations/no-such-id").json()["detail"]
    review_copy = client.get("/api/ai/reviews/no-such-review").json()["detail"]
    item_copy = client.put(
        f"/api/ai/reviews/{review_id}/decisions",
        json={"category": "errors", "item_index": 5, "decision": "accepted"},
    ).json()["detail"]

    assert len({conversation_copy, review_copy, item_copy}) == 3, (
        f"三种 404 的文案撞成了一句：{conversation_copy!r}"
    )
    # 光"互不相同"还不够：合并成两句照样能过。要钉的是**每句都说出自己那一件事**。
    assert "对话" in conversation_copy
    assert "审查" in review_copy and "条目" in item_copy


def test_ai_and_knowledge_404s_do_not_shadow_each_other(fake_ai) -> None:
    """handler 都注册在 app 上，别互相盖掉：
    审查记录不存在 → 404，AI 调用失败 → 502，知识不存在 → 404。"""
    client, provider = fake_ai
    conversation_id, _ = extracted_draft(client, provider)
    provider.queue(AiUnavailable())

    assert client.get("/api/ai/reviews/no-such-review").status_code == 404
    assert client.get("/api/knowledge/no-such-knowledge").status_code == 404
    assert client.post(f"/api/ai/conversations/{conversation_id}/extract").status_code == 502


def test_error_messages_are_the_fixed_copy_not_exception_strings(fake_ai) -> None:
    """给用户的是那五句固定文案，不是 Python 异常信息：
    后者可能带 base_url、字段名，甚至用户正文。"""
    client, provider = fake_ai
    conversation_id, _ = extracted_draft(client, provider)
    provider.queue(AiSchemaInvalid(("errors", "follow_up_questions"), detail="字段校验失败"))

    body = client.post(f"/api/ai/conversations/{conversation_id}/extract").json()
    assert body["detail"] == AiSchemaInvalid.user_message
    assert "errors" not in body["detail"]
