"""Provider 契约与失败分类的保证。

这一步（阶段 2 第②步）还没有真实的 HTTP 实现，
所以这里测的是**契约本身**：分类齐全、互不重复、提示语各不相同、
协议形状能被静态检查抓住。
"哪种网络故障映射成哪个异常类"属于 openai_compat.py 的职责，
在第③步用 httpx.MockTransport 打假响应来测。
"""

from __future__ import annotations

import pytest

from backend.db.models import ERROR_KINDS
from backend.services.ai.errors import (
    AiAuthRejected,
    AiBadRequest,
    AiError,
    AiSchemaInvalid,
    AiTimeout,
    AiUnavailable,
)
from backend.services.ai.provider import (
    CHAT_ROLES,
    AiProvider,
    ChatMessage,
)

ALL_ERRORS = (AiUnavailable, AiTimeout, AiAuthRejected, AiSchemaInvalid, AiBadRequest)


def test_error_kinds_exactly_cover_the_database_vocabulary() -> None:
    """异常类 ↔ ai_messages.error_kind 必须一一对应，不多不少。

    少一个类：那一类失败会被当作未知错误吞掉。
    多一个类：写库时落进一个列词表里没有的值。
    """
    kinds = {cls.error_kind for cls in ALL_ERRORS}
    assert kinds == set(ERROR_KINDS), f"缺 {set(ERROR_KINDS) - kinds}，多 {kinds - set(ERROR_KINDS)}"


def test_error_kinds_are_distinct_per_class() -> None:
    """任何一个类都不能和另一个共用同一个 kind，否则分类等于没分。"""
    kinds = [cls.error_kind for cls in ALL_ERRORS]
    assert len(kinds) == len(set(kinds))


def test_user_messages_differ_across_the_failures() -> None:
    """处置不一样的失败，提示语就必须不一样。

    AiAuthRejected / AiBadRequest 和 AiUnavailable 混起来的话，用户会对一个
    「重试永远没用」的错误一直点重试。
    """
    messages = [cls.user_message for cls in ALL_ERRORS]
    assert len(messages) == len(set(messages)), "有两类失败用了同一句提示"
    assert all(msg.strip() for msg in messages)


def test_user_messages_never_carry_a_key_shaped_token() -> None:
    """提示语是固定文案，会原样显示给用户、也可能进日志，绝不能含凭据。"""
    for cls in ALL_ERRORS:
        assert "sk-" not in cls.user_message
        assert "=" not in cls.user_message, "提示语里出现了疑似参数拼接的内容"


def test_all_failures_are_catchable_as_ai_error() -> None:
    """service 层只需要一个 `except AiError` 就能兜住整条 AI 旁路。

    漏了共同基类的话，新加一类失败就会一路冒到 API 层变成 500。
    """
    for cls in ALL_ERRORS:
        assert issubclass(cls, AiError)
        assert isinstance(cls() if cls is not AiSchemaInvalid else cls(("errors",)), AiError)


def test_default_message_is_the_user_message() -> None:
    """不传 detail 时，异常自身的字符串就是给用户看的那句话，不丢信息。"""
    assert str(AiTimeout()) == AiTimeout.user_message
    assert str(AiAuthRejected()) == AiAuthRejected.user_message


def test_detail_is_kept_for_diagnostics() -> None:
    """base_url、超时秒数这类不敏感诊断要能带上。"""
    error = AiUnavailable(detail="https://example.invalid/v1")
    assert error.detail == "https://example.invalid/v1"
    assert "example.invalid" in str(error)


def test_schema_invalid_keeps_field_names_only() -> None:
    """C3 + C6：畸形 JSON 不落库、不进日志，异常里只允许有字段名。"""
    error = AiSchemaInvalid(invalid_fields=("errors", "needs_verification"))
    assert error.invalid_fields == ("errors", "needs_verification")
    assert "errors" in str(error)
    assert "needs_verification" in str(error)


def test_schema_invalid_normalizes_a_list_of_fields() -> None:
    """传 list 也要存成 tuple——可变载体可能被调用方事后改掉。"""
    fields = ["omissions"]
    error = AiSchemaInvalid(fields)  # 故意不按签名传 list，模拟调用方偷懒
    assert isinstance(error.invalid_fields, tuple)
    fields.append("errors")
    assert error.invalid_fields == ("omissions",), "invalid_fields 跟着源列表变了"


def test_every_error_kind_round_trips_to_the_column_value() -> None:
    """异常类上的 kind 就是写进 ai_messages.error_kind 的那个字符串。

    翻译表长在类上，service 层不需要再写 if/elif；这里锁住这个约定。
    """
    for cls in ALL_ERRORS:
        assert cls.error_kind in ERROR_KINDS


# --------------------------------------------------------------- ChatMessage


def test_chat_message_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="未知的消息角色"):
        ChatMessage(role="tool", content="这条不该被接受")


def test_system_role_is_allowed_on_the_request_side() -> None:
    """请求侧比持久化侧多一个 system：它只发给 AI，永不落库。"""
    assert "system" in CHAT_ROLES
    assert ChatMessage(role="system", content="引导用户自己说清楚理解") is not None


def test_chat_message_is_frozen() -> None:
    """重试要把同一个消息列表原样再发一遍，可变对象会让这说不清。"""
    from dataclasses import FrozenInstanceError

    message = ChatMessage(role="user", content="什么是梯度下降")
    with pytest.raises(FrozenInstanceError):
        message.content = "改成别的"  # type: ignore[misc]


# ------------------------------------------------------------------ 协议形状


class _GoodProvider:
    def __init__(self) -> None:
        self._model_id = "fake-model"

    @property
    def model_id(self) -> str:
        return self._model_id

    def chat(self, messages, schema):
        raise NotImplementedError

    def stream_chat(self, messages):
        raise NotImplementedError


class _NoStreaming:
    @property
    def model_id(self) -> str:
        return "fake-model"

    def chat(self, messages, schema):
        raise NotImplementedError


class _NoModelId:
    def chat(self, messages, schema):
        raise NotImplementedError

    def stream_chat(self, messages):
        raise NotImplementedError


def test_full_implementation_satisfies_the_protocol() -> None:
    assert isinstance(_GoodProvider(), AiProvider)


def test_missing_stream_chat_does_not_satisfy_the_protocol() -> None:
    """只做结构化输出、不实现流式的 Provider 不能冒充完整实现。"""
    assert not isinstance(_NoStreaming(), AiProvider)


def test_missing_model_id_does_not_satisfy_the_protocol() -> None:
    """model_id 要落进 ai_reviews.model（NOT NULL），缺了就是运行时炸。"""
    assert not isinstance(_NoModelId(), AiProvider)
