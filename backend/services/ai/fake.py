"""可编程的假 Provider：让全部测试**不联网、不需要 key**（DECISIONS D-038）。

关键设计：排队的原始文本走的是 `parsing.parse_structured`，
**和生产代码同一份解析校验逻辑**。所以"C3 畸形输出不许写库"这类测试
测的是真的解析代码，而不是一个自己实现了一遍校验的桩。

用法：

    provider = FakeProvider()
    provider.queue('{"title": "梯度下降"}')      # 原始文本 → 真的走校验
    provider.queue({"title": "梯度下降"})         # dict 便捷写法，等价于上面
    provider.queue(ReviewResult(...))             # 已经是模型实例，直接返回
    provider.queue(AiTimeout())                   # 抛出去
    provider.queue_stream(["你", "好"])            # 流式碎片
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import TypeVar

from pydantic import BaseModel

from backend.services.ai.parsing import parse_structured
from backend.services.ai.provider import AiResult, ChatMessage

ModelT = TypeVar("ModelT", bound=BaseModel)


class FakeProvider:
    """满足 `AiProvider` 协议的测试替身。

    放在后端包里而不是 tests/ 下，是因为第⑦步的接口测试和第⑧步之后的
    "演示模式"都可能要用它；更重要的是它必须能复用 parsing.py，
    写在测试文件里就会诱导读的人另抄一份校验。
    """

    def __init__(
        self,
        model_id: str = "fake-model",
        duration_ms: int = 1,
        prompt_tokens: int | None = 12,
        completion_tokens: int | None = 34,
    ) -> None:
        self._model_id = model_id
        self._duration_ms = duration_ms
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

        # 排队的结果，先进先出。元素是下面几种之一：
        #   BaseException —— 抛出        str/dict —— 走 parse_structured
        #   BaseModel 实例 —— 直接返回
        self._chat_queue: list[object] = []
        self._chat_usage: list[tuple[int | None, int | None]] = []
        self._stream_queue: list[object] = []

        # 记账，供断言。存的是**副本**：调用方后来改了列表不该影响记录。
        self.chat_calls: list[list[ChatMessage]] = []
        self.stream_calls: list[list[ChatMessage]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    def queue(
        self,
        outcome: object,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> "FakeProvider":
        """排一个 chat() 的结果。异常、原始文本、dict、模型实例都收。"""
        self._chat_queue.append(outcome)
        self._chat_usage.append(
            (
                self._prompt_tokens if prompt_tokens is None else prompt_tokens,
                self._completion_tokens if completion_tokens is None else completion_tokens,
            )
        )
        return self

    def queue_stream(self, outcome: object) -> "FakeProvider":
        """排一个 stream_chat() 的结果：碎片列表，或者一个异常。"""
        self._stream_queue.append(outcome)
        return self

    def chat(self, messages: Sequence[ChatMessage], schema: type[ModelT]) -> AiResult[ModelT]:
        self.chat_calls.append(list(messages))
        if not self._chat_queue:
            raise AssertionError(
                "FakeProvider 的结构化结果队列空了——测试忘了 queue，"
                "或者被测代码比预期多调了一次 AI"
            )

        outcome = self._chat_queue.pop(0)
        prompt_tokens, completion_tokens = self._chat_usage.pop(0)

        if isinstance(outcome, BaseException):
            raise outcome

        if isinstance(outcome, BaseModel):
            value: ModelT = schema.model_validate(outcome.model_dump())
        elif isinstance(outcome, str):
            value = parse_structured(outcome, schema)
        elif isinstance(outcome, dict):
            value = parse_structured(json.dumps(outcome, ensure_ascii=False), schema)
        else:
            raise AssertionError(f"无法排进队列的结果类型：{type(outcome).__name__}")

        return AiResult(
            value=value,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=self._duration_ms,
        )

    def stream_chat(self, messages: Sequence[ChatMessage]) -> Iterator[str]:
        """和真实实现一样是生成器：第一次 next() 才检查队列。

        所以测试里的断言要写在"迭代它"这件事外面，行为才和真机一致。
        """
        self.stream_calls.append(list(messages))
        if not self._stream_queue:
            raise AssertionError(
                "FakeProvider 的流式结果队列空了——测试忘了 queue_stream"
            )

        outcome = self._stream_queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if not isinstance(outcome, (list, tuple)):
            raise AssertionError(f"流式结果必须是碎片列表，收到 {type(outcome).__name__}")

        for chunk in outcome:
            if not isinstance(chunk, str):
                raise AssertionError(f"流式碎片必须是字符串，收到 {type(chunk).__name__}")
            yield chunk

    def remaining(self) -> int:
        """还没消费掉的排队结果数。测试末尾断言它为 0，
        能抓住"被测代码其实没调用 AI，却因为排了队而看起来调用了"这种假通过。
        """
        return len(self._chat_queue) + len(self._stream_queue)

    def close(self) -> None:
        """和真实实现同签名，让上层可以无差别地关闭任何一种 Provider。"""
