"""AI Provider 的契约：调用方拿到的永远是**已校验的 Pydantic 对象**，不是字符串。

把"发请求 → 解析 JSON → 校验 schema"这三步全部收在 provider 层里做，
好处是 C3（AI 输出必须过后端校验）只落在**一处**。
否则知识提取、知识审查、将来的关联判断会各写一遍解析，
漏掉任何一遍都是"畸形数据写进库"。

## 为什么只定义一个协议，而不做多家适配

DeepSeek、OpenAI、智谱、月之暗面、通义，以及本地的 Ollama / LM Studio / vLLM
**全都提供 OpenAI Chat Completions 兼容端点**，所以"只实现一种协议"
不等于"锁死一家"：换供应商只改 `.env` 里的 base_url 和 model，代码不动。
详见 docs/DECISIONS.md D-038。

## 两个方法各自服务哪个工作流（D-039）

- `chat()` —— 工作流 B（知识提取）、C（知识审查）。要的是**结构化 JSON**，
  必须拿到完整内容才能校验，所以**不流式**。
- `stream_chat()` —— 只服务工作流 A（自由对话）。给人看的自然语言，
  打字机效果收益最大。返回的是文本碎片，**不做 schema 校验**；
  对话结束时如果要提取知识，那是再走一次 `chat()`，不是把流式碎片当 JSON 解析。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

# 请求侧的角色词表，比 ai_messages 的 MESSAGE_ROLES 多一个 system。
# 两者**故意不同**：system 是我们注入的指令，只出现在发给 AI 的请求里，
# 永不落库（它能由 prompts.py + prompt_version 完整重建，存一份是白白占空间，
# 也违反 C6 最小必要）。别把它们混成一个列表。
CHAT_ROLE_SYSTEM = "system"
CHAT_ROLE_USER = "user"
CHAT_ROLE_ASSISTANT = "assistant"

CHAT_ROLES: tuple[str, ...] = (
    CHAT_ROLE_SYSTEM,
    CHAT_ROLE_USER,
    CHAT_ROLE_ASSISTANT,
)


@dataclass(frozen=True)
class ChatMessage:
    """发给 AI 的一条消息。

    frozen 是有意的：重试时要把同一个消息列表原样再发一遍，
    可变对象会让"第二次发的和第一次一不一样"变成说不清的问题。
    """

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in CHAT_ROLES:
            raise ValueError(f"未知的消息角色：{self.role!r}，只允许 {CHAT_ROLES}")


ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class AiResult(Generic[ModelT]):
    """一次结构化调用的结果：校验过的对象 + 这趟花了多少。

    为什么要包一层而不是直接返回模型：`ai_messages` 和 `ai_reviews` 都有
    `prompt_tokens` / `completion_tokens` / `duration_ms` 三列。
    token 数只在 HTTP 响应的 `usage` 里，service 层拿不到就永远只能写 NULL，
    那三列等于白建。耗时由 provider 自己量，免得每个调用方各写一遍计时。

    模型名**不放进来**——它由 `AiProvider.model_id` 单一提供，避免两个来源对不上。
    """

    value: ModelT
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int


@runtime_checkable
class AiProvider(Protocol):
    """AI 服务提供方。实现者负责网络、解析、校验和错误分类。

    实现必须：
    - 失败时抛 `errors.py` 里的异常类，**不抛裸异常**，
      否则上层没法区分"该重试"和"该去改 key"。
    - **不自动重试**（AI_WORKFLOWS 第一节）：提取草稿会产生记录，
      盲目重试就是重复知识。读操作也不重试——个人本地应用里
      「一次失败一次明确报错」比「悄悄重试三次才报错」更好向用户解释。
    """

    @property
    def model_id(self) -> str:
        """实际使用的模型名。

        落在 `ai_messages.model` 和 `ai_reviews.model` 上（两列都 NOT NULL）。
        由 provider 提供而不是让 service 去读 settings，是因为这两处必须
        和真正发出去的那个值一致——settings 有可能在请求之后又被改过。
        """
        ...

    def chat(
        self, messages: Sequence[ChatMessage], schema: type[ModelT]
    ) -> AiResult[ModelT]:
        """整体请求，返回校验过的对象加上 token/耗时。

        raises: AiUnavailable / AiTimeout / AiAuthRejected / AiSchemaInvalid
        """
        ...

    def stream_chat(self, messages: Sequence[ChatMessage]) -> Iterator[str]:
        """流式请求，产出文本碎片（不是 JSON，也不是校验过的结构）。

        ⚠️ 这是个**生成器**：连接失败不会在调用 `stream_chat()` 那一刻抛出，
        而是在第一次 `next()` 时才抛。调用方必须把"开始迭代"也放进 try 里，
        否则错误会冒到别处去。流到一半断开属于 AiUnavailable。

        调用方要负责把碎片拼成完整文本再落库（`ai_messages.content` 存的是
        最终完整文本，不是碎片）。流式响应多数端点不给 token 数，留 NULL。
        """
        ...
