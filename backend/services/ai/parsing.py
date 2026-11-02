"""把 AI 的文本回复变成校验过的结构化对象。

单独抽出来是为了让**真实实现和假实现共用同一套解析校验代码**。
如果假 Provider 自己绕过解析直接返回对象，那"畸形 JSON 不许写库"（C3）
的测试就只是在测试桩，测不到生产代码里那段真正会执行的东西。
"""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.services.ai.errors import AiSchemaInvalid

ModelT = TypeVar("ModelT", bound=BaseModel)


def extract_message_content(payload: object) -> str:
    """从 Chat Completions 的响应信封里取出助手说的话。

    信封结构由协议规定，不属于 AI 的自由输出，所以这里缺字段算
    **服务端不合规**，同样归 AiSchemaInvalid：调用方要的处理是一样的
    （重试或换端点），而不是"用户能看到的东西"。
    """
    if not isinstance(payload, dict):
        raise AiSchemaInvalid(("$response",), detail="响应不是 JSON 对象")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AiSchemaInvalid(("choices",), detail="响应里没有 choices 数组")

    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise AiSchemaInvalid(("choices.message",), detail="choices[0] 里没有 message 对象")

    content = message.get("content")
    if not isinstance(content, str):
        raise AiSchemaInvalid(("choices.message.content",), detail="content 不是字符串")

    return content


def extract_usage(payload: object) -> tuple[int | None, int | None]:
    """取 (prompt_tokens, completion_tokens)。

    用 `.get()` 而不是下标：`usage` 在很多兼容端点上是可选的
    （Ollama 的部分版本、流式响应基本都不给），缺了就是 NULL，
    不该为此报错——token 数只是诊断信息，不是功能依赖。
    """
    if not isinstance(payload, dict):
        return None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return _int_or_none(usage.get("prompt_tokens")), _int_or_none(
        usage.get("completion_tokens")
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_structured(content: str, schema: type[ModelT]) -> ModelT:
    """把助手说的话解析成 `schema` 的实例。失败一律抛 AiSchemaInvalid。

    ⚠️ 任何一个分支都**不把原始内容带进异常**。原始返回可能包含用户的知识正文，
    而异常会进日志（C6）。只带字段名和一句不含数据的说明。
    """
    text = _strip_code_fence(content)
    if not text:
        raise AiSchemaInvalid(("$body",), detail="AI 返回了空内容")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # exc.pos / exc.lineno 是位置数字，不含内容本身
        raise AiSchemaInvalid(
            ("$body",), detail=f"返回内容不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列）"
        ) from None

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise AiSchemaInvalid(
            _invalid_field_names(exc), detail="返回内容不符合约定的字段结构"
        ) from None


def _invalid_field_names(exc: ValidationError) -> tuple[str, ...]:
    """只取**字段路径**，一条错误值都不取。

    `exc.errors()` 里的 `input` 字段装着实际收到的值——那可能是用户的知识正文，
    绝对不能往异常里放。这里刻意只读 `loc`。
    """
    names: list[str] = []
    for error in exc.errors():
        loc = error.get("loc") or ()
        names.append(".".join(str(part) for part in loc) or "$root")
    return tuple(names)


def _strip_code_fence(content: str) -> str:
    """去掉 ```json ... ``` 这类围栏。

    不是防御性过度设计：让模型输出纯 JSON 时，"套一层 markdown 围栏"
    是各家兼容端点最常见的偏差之一，直接 json.loads 就会炸。
    只剥围栏，不猜内容——剥完仍不是合法 JSON 就照实报 AiSchemaInvalid。
    """
    text = content.strip()
    if not text.startswith("```"):
        return text

    first_newline = text.find("\n")
    if first_newline != -1:
        # 丢掉围栏行（可能是 ```json 也可能就是 ```）
        text = text[first_newline + 1 :]
    else:
        text = text[3:]

    text = text.strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return text
