"""OpenAI Chat Completions 兼容协议的 HTTP 实现。

一份代码适配所有兼容端点：DeepSeek / OpenAI / 智谱 / 月之暗面 / 通义
以及本地的 Ollama、LM Studio、vLLM（DECISIONS D-038）。

## URL 是手工拼的，不交给 httpx 的 base_url

`httpx.Client(base_url=...)` 拼路径的规则是**字节串直接相加**，
`base_url="https://x/v1"` + `post("/chat/completions")` 会得到
`https://x/v1chat/completions`（少了斜杠）——这是个会静默发生、
又只有真机联调才能发现的坑。所以这里自己 rstrip("/") + "/chat/completions"，
行为完全可预测，也测得出来。

## 从不自动重试

提取草稿会产生知识行，重试就是重复记录（Blueprint 阶段 2 验收项明确禁止）。
读操作同样不重试：个人本地应用里，「一次失败 + 一句明确报错」
比「悄悄重试三次才报错」好解释得多，也不会让用户怀疑自己到底点了几次。

## 隐私（C6）

- API Key 只进请求头，**不存成可被 repr 出来的形式**，见 `__repr__`
- 任何异常的 detail 只允许含 URL、状态码、异常类名、超时秒数，
  **绝不含响应体**，也绝不含请求体
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from typing import TypeVar

import httpx
from pydantic import BaseModel

from backend.services.ai.errors import (
    AiAuthRejected,
    AiBadRequest,
    AiError,
    AiSchemaInvalid,
    AiTimeout,
    AiUnavailable,
)
from backend.services.ai.parsing import (
    extract_message_content,
    extract_usage,
    parse_structured,
)
from backend.services.ai.provider import AiResult, ChatMessage

ModelT = TypeVar("ModelT", bound=BaseModel)

CHAT_COMPLETIONS_SUFFIX = "/chat/completions"

# 建连超时单独收紧：主机不可达时不该让「无法连接」这句话等满 60 秒。
CONNECT_TIMEOUT_CAP_SECONDS = 10.0


class OpenAICompatProvider:
    """满足 `provider.AiProvider` 协议的真实实现。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        json_mode: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        cleaned_base = base_url.strip().rstrip("/")
        cleaned_model = model.strip()
        if not cleaned_base:
            # 这是配置缺失，不是 AI 调用失败：上层应该先看 ai_configured 再决定
            # 要不要构造 provider。走到这里说明调用方逻辑错了，抛 ValueError
            # 而不是伪装成"无法连接"——后者会把排查方向带偏到网络上。
            raise ValueError("构造 AI Provider 缺少 base_url")
        if not cleaned_model:
            raise ValueError("构造 AI Provider 缺少 model")

        self._base_url = cleaned_base
        self._model = cleaned_model
        self._json_mode = json_mode
        # 超时会随每次请求重新计算，所以用普通 str 存即可。
        # 刻意不给这个实例加任何持有 key 的公开属性：见 __repr__。
        self._timeout_seconds = timeout_seconds
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                timeout_seconds, connect=min(timeout_seconds, CONNECT_TIMEOUT_CAP_SECONDS)
            ),
            headers=_auth_headers(api_key),
            transport=transport,
        )

    @property
    def model_id(self) -> str:
        return self._model

    def __repr__(self) -> str:
        """永远不含 API Key。

        这个类会被 logger.exception 的 traceback 局部变量表带出来，
        默认 repr 只打印对象地址，但那要靠运气。写成显式的、白名单式的 repr，
        让"key 不会被打印"变成一个**声明**而不是一个巧合。
        """
        return (
            f"OpenAICompatProvider(base_url={self._base_url!r}, "
            f"model={self._model!r}, json_mode={self._json_mode!r}, "
            f"key_set={bool(self._client.headers.get('Authorization'))})"
        )

    def chat(self, messages: Sequence[ChatMessage], schema: type[ModelT]) -> AiResult[ModelT]:
        started = time.perf_counter()
        body = self._body(messages, stream=False)

        try:
            response = self._client.post(self._url(), json=body)
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from None
        except httpx.HTTPError as exc:
            raise self._unavailable_error(exc) from None

        self._raise_for_status(response)
        payload = self._read_json_object(response)
        content = extract_message_content(payload)
        value = parse_structured(content, schema)
        prompt_tokens, completion_tokens = extract_usage(payload)

        return AiResult(
            value=value,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=_elapsed_ms(started),
        )

    def stream_chat(self, messages: Sequence[ChatMessage]) -> Iterator[str]:
        """流式产出文本碎片。是生成器，第一次 `next()` 才真正发请求。

        失败分类和 `chat()` 一样，但抛出时机不同——调用方必须从第一次迭代
        开始就包在 try 里（见 provider.py 里的 ⚠️ 说明）。
        """
        body = self._body(messages, stream=True)

        try:
            with self._client.stream("POST", self._url(), json=body) as response:
                if response.status_code >= 300:
                    # 流式响应还没读 body，但状态码已经可用，够分类了。
                    # 这里不调 response.read()：错误正文可能含内容，一个字节都不取。
                    self._raise_for_status(response)
                for line in response.iter_lines():
                    chunk = _sse_content(line)
                    if chunk is None:
                        continue
                    if chunk == _DONE:
                        return
                    yield chunk
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from None
        except httpx.HTTPError as exc:
            raise self._unavailable_error(exc) from None

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ 内部

    def _url(self) -> str:
        return self._base_url + CHAT_COMPLETIONS_SUFFIX

    def _body(self, messages: Sequence[ChatMessage], *, stream: bool) -> dict:
        body: dict = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if stream:
            body["stream"] = True
        elif self._json_mode:
            # 只在非流式的结构化调用上开。而且这个开关**默认关着**：
            # 兼容端点对 response_format 的支持参差不齐，贸然带上会让所有
            # 请求一起 400；在没有真实 key 无法验证兼容性的时候，
            # 保守默认比"优先用"更重要（设计文档的偏离，见 DECISIONS）。
            body["response_format"] = {"type": "json_object"}
        return body

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise AiAuthRejected(detail=f"{response.request.url} 拒绝了凭据（HTTP {status}）")
        if 400 <= status < 500:
            # 404 和 400 都归这里：新手最常见的两个成因是
            # 「模型名写错」和「base_url 少了 /v1」，解法都是改设置页，
            # 所以绝不能并进 AiUnavailable 让用户去查网络。
            raise AiBadRequest(status_code=status, detail=f"{response.request.url} 返回 HTTP {status}")
        raise AiUnavailable(detail=f"{response.request.url} 返回 HTTP {status}")

    def _read_json_object(self, response: httpx.Response) -> object:
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            raise AiSchemaInvalid(("$response",), detail="服务端返回的不是 JSON") from None

    def _timeout_error(self, exc: Exception) -> AiError:
        # ConnectTimeout 同时继承 TimeoutException 和 NetworkError，
        # 必须**先**判它：建连失败的真实含义是"连不上"，不是"对方慢"，
        # 归进 AiTimeout 会让用户去做一件没用的事（调大超时）。
        if isinstance(exc, httpx.ConnectTimeout):
            return AiUnavailable(
                detail=f"{self._base_url} 建连超时（连接上限 {CONNECT_TIMEOUT_CAP_SECONDS:g} 秒）"
            )
        return AiTimeout(detail=f"{self._base_url} 在 {self._timeout_seconds} 秒内没有返回完整响应")

    def _unavailable_error(self, exc: Exception) -> AiUnavailable:
        # 只取异常**类名**，不取 str(exc)：httpx 的一些异常文本会带上请求细节。
        return AiUnavailable(detail=f"{self._base_url} 连接失败：{type(exc).__name__}")


def _auth_headers(api_key: str) -> dict[str, str]:
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"}


_DONE = "[DONE]"

# SSE 行的注释前缀：OpenAI 兼容端点常用 ": keep-alive" 之类的心跳保活，
# 不是 data 行，直接跳过。
_SSE_DATA_PREFIX = "data:"


def _sse_content(line: str) -> str | None:
    """从一行 SSE 里取出这次的文本碎片；不是 data 行就返回 None。"""
    text = line.strip()
    if not text or not text.startswith(_SSE_DATA_PREFIX):
        return None

    payload = text[len(_SSE_DATA_PREFIX) :].strip()
    if payload == _DONE:
        return _DONE

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise AiSchemaInvalid(("$stream",), detail="流式响应的一个数据块不是 JSON") from None

    choices = event.get("choices") if isinstance(event, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    delta = first.get("delta") if isinstance(first, dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) and content else None


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
