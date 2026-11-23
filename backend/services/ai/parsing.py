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

    data = _loads_json_lenient(text)
    if not isinstance(data, dict):
        raise AiSchemaInvalid(("$body",), detail="返回内容里找不到合法的 JSON 对象")

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise AiSchemaInvalid(
            _invalid_field_names(exc), detail="返回内容不符合约定的字段结构"
        ) from None


def _loads_json_lenient(text: str) -> object | None:
    """先按整段就是 JSON 解析；不行再从里面抠第一个能独立成对象的平衡片段。

    只剥围栏处理不了「寒暄在前、JSON 在中间、收尾在后」这种输出——实测 DeepSeek
    官方会**间歇**这么答，中转站更常见，正是「同一个模型时好时坏」的来源。
    这里仍然**不猜内容**：只认模型真正吐出来的、能独立解析成对象的那一段，
    抠不到就返回 None，由调用方照实报 AiSchemaInvalid。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _first_json_object(text)


def _first_json_object(text: str) -> object | None:
    """扫描文本，返回第一个能独立解析成 JSON 对象的平衡 `{...}`（解析后的对象）。

    跟踪字符串与转义，避免把字符串值里的花括号算进配对；候选当场 `json.loads`，
    解析成功且确实是对象才采用，否则换下一个 `{` 起点继续找。全程不改动一个字节。
    """
    total = len(text)
    for start in range(total):
        if text[start] != "{":
            continue
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, total):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        break  # 这个起点配不出合法 JSON，换下一个 `{`
                    return parsed if isinstance(parsed, dict) else None
    return None


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
