"""阶段 2 第③步：解析校验、HTTP 实现、假 Provider。

全部用 `httpx.MockTransport` 打假响应，**不联网、不需要 key**（D-038）。
这里验的是"哪种真实故障映射成哪个异常类"——第②步只定了契约，映射在本步才成立。
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel, Field

from backend.services.ai.errors import (
    AiAuthRejected,
    AiBadRequest,
    AiError,
    AiSchemaInvalid,
    AiTimeout,
    AiUnavailable,
)
from backend.services.ai.fake import FakeProvider
from backend.services.ai.openai_compat import OpenAICompatProvider
from backend.services.ai.parsing import (
    extract_message_content,
    extract_usage,
    parse_structured,
)
from backend.services.ai.provider import AiProvider, ChatMessage

# 测试哨兵：任何一条报错信息里都不许出现它（C6）
SENTINEL_KEY = "sk-TEST-DO-NOT-LEAK-1234567890"


class TinySchema(BaseModel):
    answer: str
    score: int = Field(ge=0, le=100)


def envelope(content: str, *, usage: dict | None = None) -> dict:
    body: dict = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def make_provider(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url=kwargs.pop("base_url", "https://api.example.invalid/v1"),
        api_key=kwargs.pop("api_key", SENTINEL_KEY),
        model=kwargs.pop("model", "test-model"),
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


# ----------------------------------------------------------------- parsing


def test_extract_content_requires_choices() -> None:
    with pytest.raises(AiSchemaInvalid) as excinfo:
        extract_message_content({"message": {}})
    assert "choices" in excinfo.value.invalid_fields


def test_extract_content_rejects_non_string_content() -> None:
    with pytest.raises(AiSchemaInvalid) as excinfo:
        extract_message_content({"choices": [{"message": {"content": ["一段数组"]}}]})
    assert excinfo.value.invalid_fields == ("choices.message.content",)


def test_extract_content_accepts_the_normal_envelope() -> None:
    assert extract_message_content(envelope("你好")) == "你好"


def test_extract_usage_is_lenient_about_absent_usage() -> None:
    """usage 在很多兼容端点上是可选的，缺了不该报错——它只是诊断信息。"""
    assert extract_usage(envelope("你好")) == (None, None)
    assert extract_usage(envelope("你好", usage={"prompt_tokens": 7, "completion_tokens": 3})) == (
        7,
        3,
    )
    assert extract_usage({"choices": [], "usage": "不是对象"}) == (None, None)


def test_extract_usage_ignores_booleans() -> None:
    """Python 里 True 是 int 的子类。把 True 当成 1 个 token 是错的记账。"""
    assert extract_usage(envelope("你好", usage={"prompt_tokens": True, "completion_tokens": 5})) == (
        None,
        5,
    )


def test_parse_structured_happy_path() -> None:
    value = parse_structured('{"answer": "沿负梯度方向更新参数", "score": 80}', TinySchema)
    assert value.answer == "沿负梯度方向更新参数"
    assert value.score == 80


def test_parse_structured_strips_markdown_fence() -> None:
    """让模型输出纯 JSON 时套围栏是各家端点最常见的偏差，直接 loads 会炸。"""
    fenced = '```json\n{"answer": "甲", "score": 1}\n```'
    assert parse_structured(fenced, TinySchema).answer == "甲"


def test_parse_structured_rejects_not_json() -> None:
    with pytest.raises(AiSchemaInvalid):
        parse_structured("我觉得这个概念很有意思", TinySchema)


def test_parse_structured_rejects_empty() -> None:
    with pytest.raises(AiSchemaInvalid):
        parse_structured("   ", TinySchema)


def test_parse_structured_reports_only_field_paths_not_values() -> None:
    """⚠️ C6 的关键一条：ValidationError 的 errors() 里 `input` 装着实际收到的值，
    那可能是用户的知识正文。异常里只允许出现字段路径。"""
    secret_text = "我关于过拟合的私人理解，绝对不该出现在日志里"
    with pytest.raises(AiSchemaInvalid) as excinfo:
        parse_structured('{"answer": 123, "score": "%s"}' % secret_text, TinySchema)
    error = excinfo.value
    assert any("score" in name or "answer" in name for name in error.invalid_fields)
    assert secret_text not in str(error)
    assert secret_text not in repr(error)
    assert secret_text not in ",".join(error.invalid_fields)


# ------------------------------------------------- openai_compat: 成功路径


def test_chat_returns_validated_value_and_usage() -> None:
    provider = make_provider(
        lambda request: httpx.Response(
            200,
            json=envelope(
                '{"answer": "梯度下降", "score": 90}',
                usage={"prompt_tokens": 11, "completion_tokens": 22},
            ),
        )
    )
    result = provider.chat([ChatMessage(role="user", content="解释梯度下降")], TinySchema)

    assert result.value.answer == "梯度下降"
    assert (result.prompt_tokens, result.completion_tokens) == (11, 22)
    assert result.duration_ms >= 0
    provider.close()


def test_chat_sends_the_openai_compatible_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=envelope('{"answer": "甲", "score": 1}'))

    provider = make_provider(handler)
    provider.chat(
        [
            ChatMessage(role="system", content="引导用户自己说清楚理解"),
            ChatMessage(role="user", content="什么是过拟合"),
        ],
        TinySchema,
    )

    assert captured["body"]["model"] == "test-model"
    assert [m["role"] for m in captured["body"]["messages"]] == ["system", "user"]
    assert captured["auth"] == f"Bearer {SENTINEL_KEY}"
    provider.close()


def test_json_mode_is_off_by_default_and_addable() -> None:
    """默认不带 response_format：兼容端点支持参差不齐，贸然带上会让全部请求 400。"""

    def handler_factory(capture: dict) -> Callable:
        def handler(request: httpx.Request) -> httpx.Response:
            capture["body"] = json.loads(request.read())
            return httpx.Response(200, json=envelope('{"answer": "甲", "score": 1}'))

        return handler

    off: dict = {}
    provider = make_provider(handler_factory(off))
    provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert "response_format" not in off["body"]
    provider.close()

    on: dict = {}
    provider = make_provider(handler_factory(on), json_mode=True)
    provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert on["body"]["response_format"] == {"type": "json_object"}
    provider.close()


@pytest.mark.parametrize(
    "base",
    ["https://api.example.invalid/v1", "https://api.example.invalid/v1/"],
)
def test_url_is_built_by_hand_not_by_httpx_base_url(base: str) -> None:
    """httpx 的 base_url 拼路径是字节相加，"/v1" + "/chat/completions" 会变成
    "/v1chat/completions"。这里把正确行为钉死，含尾斜杠的输入也要一样。"""
    seen: list[str] = []
    provider = make_provider(_recorder(seen), base_url=base)
    provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert seen == ["https://api.example.invalid/v1/chat/completions"]
    provider.close()


def _recorder(into: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        into.append(str(request.url))
        return httpx.Response(200, json=envelope('{"answer": "甲", "score": 1}'))

    return handler


# ---------------------------------------------- openai_compat: 故障映射


def make_failing_provider(exc_factory: Callable[[httpx.Request], Exception]) -> OpenAICompatProvider:
    """让 transport 在发送阶段抛出传输层异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_factory(request)

    return make_provider(handler)


@pytest.mark.parametrize(
    "exc_factory,expected",
    [
        (lambda request: httpx.ConnectError("connection refused", request=request), AiUnavailable),
        (lambda request: httpx.ReadTimeout("read timed out", request=request), AiTimeout),
        (
            lambda request: httpx.RemoteProtocolError("peer closed", request=request),
            AiUnavailable,
        ),
    ],
)
def test_transport_errors_map_to_the_right_class(exc_factory, expected) -> None:
    provider = make_failing_provider(exc_factory)
    with pytest.raises(expected):
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    provider.close()


def test_connect_timeout_is_unavailable_not_timeout() -> None:
    """⚠️ 顺序敏感的一条：ConnectTimeout 同时是 TimeoutException 和 NetworkError。

    如果按"超时"分类，用户会去做一件没用的事（把超时调大），
    而真正的问题是连不上。
    """
    provider = make_failing_provider(
        lambda request: httpx.ConnectTimeout("connect timed out", request=request)
    )
    with pytest.raises(AiUnavailable) as excinfo:
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert excinfo.value.error_kind == "unavailable"
    provider.close()



@pytest.mark.parametrize(
    "status,expected",
    [
        (401, AiAuthRejected),
        (403, AiAuthRejected),
        (400, AiBadRequest),
        (404, AiBadRequest),
        (422, AiBadRequest),
        (500, AiUnavailable),
        (503, AiUnavailable),
    ],
)
def test_http_status_maps_to_the_right_class(status, expected) -> None:
    provider = make_provider(lambda request: httpx.Response(status, json={"error": "内部错误"}))
    with pytest.raises(expected) as excinfo:
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert isinstance(excinfo.value, AiError)
    provider.close()


def test_bad_request_carries_the_status_code_for_the_ui() -> None:
    """400 和 404 的排查方向不同，状态码要留给上层说话时用。"""
    provider = make_provider(lambda request: httpx.Response(404, json={"error": "nope"}))
    with pytest.raises(AiBadRequest) as excinfo:
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert excinfo.value.status_code == 404
    provider.close()


def test_non_json_success_body_is_schema_invalid() -> None:
    provider = make_provider(lambda request: httpx.Response(200, text="<html>不是 JSON</html>"))
    with pytest.raises(AiSchemaInvalid):
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    provider.close()


def test_malformed_structured_output_raises_schema_invalid_not_something_else() -> None:
    """C3 的落点：服务端 200，但内容不合契约。"""
    provider = make_provider(lambda request: httpx.Response(200, json=envelope("这不是 JSON")))
    with pytest.raises(AiSchemaInvalid):
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    provider.close()


@pytest.mark.parametrize(
    "handler,expected",
    [
        (lambda request: httpx.Response(401, json={"error": "bad"}), AiAuthRejected),
        (lambda request: httpx.Response(400, json={"error": "bad"}), AiBadRequest),
        (lambda request: httpx.Response(500, json={"error": "bad"}), AiUnavailable),
    ],
)
def test_no_error_message_ever_contains_the_key(handler, expected) -> None:
    """C6：异常会进日志，甚至可能被直接显示。key 一个片段都不许出现在里面。"""
    provider = make_provider(handler)
    with pytest.raises(expected) as excinfo:
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert SENTINEL_KEY not in str(excinfo.value)
    assert SENTINEL_KEY not in repr(excinfo.value)
    provider.close()


def test_repr_never_contains_the_key() -> None:
    provider = make_provider(lambda request: httpx.Response(200, json={}))
    assert SENTINEL_KEY not in repr(provider)
    assert "key_set=True" in repr(provider)
    provider.close()


def test_error_bodies_are_never_read_or_reproduced() -> None:
    """服务端的错误正文常会回显我们发过去的内容，所以一个字节都不取。"""
    echoed = "这是我发过去的私人知识正文，不该出现在任何地方"
    provider = make_provider(
        lambda request: httpx.Response(400, json={"error": {"message": echoed}})
    )
    with pytest.raises(AiBadRequest) as excinfo:
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert echoed not in str(excinfo.value)
    provider.close()


def test_missing_base_url_or_model_is_a_construction_error_not_an_ai_failure() -> None:
    """配置缺失不该伪装成"无法连接"，那会把排查方向从设置页带到网络上。"""
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatProvider(base_url="  ", api_key="k", model="m")
    with pytest.raises(ValueError, match="model"):
        OpenAICompatProvider(base_url="https://x.invalid/v1", api_key="k", model="")


# ----------------------------------------------------- openai_compat: 流式


def sse(chunks: list[str], *, with_done: bool = True, preamble: list[str] | None = None) -> str:
    lines = list(preamble or [])
    lines += [f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}' for c in chunks]
    if with_done:
        lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def test_stream_yields_chunks_in_order_and_stops_at_done() -> None:
    provider = make_provider(
        lambda request: httpx.Response(200, text=sse(["梯度", "下降", "是"], with_done=True))
    )
    got = list(provider.stream_chat([ChatMessage(role="user", content="甲")]))
    assert got == ["梯度", "下降", "是"]
    provider.close()


def test_stream_skips_keepalive_comment_lines() -> None:
    """兼容端点常用 ": keep-alive" 保活，它们不是 data 行，不该被当成内容。"""
    body = sse(["甲"], preamble=[": keep-alive", "", ": ping"])
    provider = make_provider(lambda request: httpx.Response(200, text=body))
    assert list(provider.stream_chat([ChatMessage(role="user", content="x")])) == ["甲"]
    provider.close()


def test_stream_request_sets_stream_true_and_no_response_format() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, text=sse(["甲"]))

    provider = make_provider(handler, json_mode=True)
    list(provider.stream_chat([ChatMessage(role="user", content="甲")]))
    assert captured["body"]["stream"] is True
    # 流式的目标是给人看的文本，不是 JSON，开了 response_format 反而误导服务端
    assert "response_format" not in captured["body"]
    provider.close()


def test_stream_assembling_gives_the_full_text() -> None:
    """落库存的是拼好的完整文本，不是碎片——这一步要保证拼起来无损。"""
    provider = make_provider(
        lambda request: httpx.Response(200, text=sse(["用", "户", "理", "解"]))
    )
    assembled = "".join(provider.stream_chat([ChatMessage(role="user", content="甲")]))
    assert assembled == "用户理解"
    provider.close()


def test_stream_error_surfaces_on_first_next_not_on_call() -> None:
    """⚠️ 生成器的抛出时机，接口层必须按这个来写 try 的范围。"""
    provider = make_provider(lambda request: httpx.Response(401, json={"error": "bad"}))
    iterator = provider.stream_chat([ChatMessage(role="user", content="甲")])
    with pytest.raises(AiAuthRejected):
        next(iterator)
    provider.close()


def test_stream_does_not_read_the_error_body() -> None:
    leaked = "服务端错误正文里回显的那段话"
    provider = make_provider(lambda request: httpx.Response(400, text=leaked))
    iterator = provider.stream_chat([ChatMessage(role="user", content="甲")])
    with pytest.raises(AiBadRequest) as excinfo:
        list(iterator)
    assert leaked not in str(excinfo.value)
    provider.close()


def test_stream_transport_failure_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset", request=request)

    provider = make_provider(handler)
    with pytest.raises(AiUnavailable):
        list(provider.stream_chat([ChatMessage(role="user", content="甲")]))
    provider.close()


def test_stream_malformed_data_line_is_schema_invalid() -> None:
    provider = make_provider(
        lambda request: httpx.Response(200, text='data: {不是 JSON\n\ndata: [DONE]\n\n')
    )
    with pytest.raises(AiSchemaInvalid):
        list(provider.stream_chat([ChatMessage(role="user", content="甲")]))
    provider.close()


# ------------------------------------------------------------------ fake


def test_fake_provider_satisfies_the_protocol() -> None:
    assert isinstance(FakeProvider(), AiProvider)


def test_fake_chat_goes_through_the_real_validation_code() -> None:
    """假 Provider 必须复用 parsing，否则"C3 不写库"的测试只是在测桩。"""
    provider = FakeProvider().queue('{"answer": "甲", "score": 900}')
    with pytest.raises(AiSchemaInvalid):
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)


def test_fake_records_the_exact_messages_sent() -> None:
    """C5 最小必要传输要靠这个断言来锁：多发了字段就该被抓到。"""
    provider = FakeProvider().queue({"answer": "甲", "score": 1})
    provider.chat([ChatMessage(role="system", content="提示词"), ChatMessage(role="user", content="只发这个")], TinySchema)
    assert len(provider.chat_calls) == 1
    assert [m.role for m in provider.chat_calls[0]] == ["system", "user"]
    assert provider.remaining() == 0


def test_fake_raises_queued_exception() -> None:
    provider = FakeProvider().queue(AiTimeout())
    with pytest.raises(AiTimeout):
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert provider.remaining() == 0


def test_fake_empty_queue_fails_loudly() -> None:
    """不静默返回空对象——那会让被测代码"看起来通过了"。"""
    provider = FakeProvider()
    with pytest.raises(AssertionError, match="队列空了"):
        provider.chat([ChatMessage(role="user", content="甲")], TinySchema)

    with pytest.raises(AssertionError, match="队列空了"):
        list(provider.stream_chat([ChatMessage(role="user", content="甲")]))


def test_fake_remaining_catches_unconsumed_expectations() -> None:
    """测完了还剩排队结果 = 被测代码没真的调用 AI，是假通过。"""
    provider = FakeProvider().queue({"answer": "甲", "score": 1}).queue_stream(["乙"])
    assert provider.remaining() == 2
    provider.chat([ChatMessage(role="user", content="甲")], TinySchema)
    assert provider.remaining() == 1


def test_fake_stream_is_also_lazy() -> None:
    """和真实实现一致的抛出时机：队列空要在迭代时才炸，不是调用时。"""
    provider = FakeProvider()
    iterator = provider.stream_chat([ChatMessage(role="user", content="甲")])
    with pytest.raises(AssertionError):
        next(iterator)


def test_fake_can_queue_a_non_list_for_stream_and_says_so() -> None:
    provider = FakeProvider().queue_stream("不该是裸字符串")
    with pytest.raises(AssertionError, match="碎片列表"):
        list(provider.stream_chat([ChatMessage(role="user", content="甲")]))
