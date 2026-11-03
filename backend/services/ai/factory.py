"""从配置构造 Provider，以及一次最小成本的连通性测试。

放在 `services/ai/` 而不是 `api/` 里，是因为"凭据不全时该不该构造 Provider"
是一个有规则的决定：AI 没配好时核心功能必须照常可用（C4），
而 AI 功能本身要给出**明确的"还没配置"**，不是一个看不懂的 500。
规则属于业务层，接口层只负责把它翻译成 HTTP 状态码。

## 为什么每次请求都新建 Provider

Provider 持有 httpx 连接池，但配置可能在设置页被改过。
每次请求按当前 settings 构造，改完 base_url / key **立刻生效**，
不需要重启进程，也不会出现"界面显示新地址、请求还发旧地址"。
本地单用户应用的连接复用收益很小，这个一致性收益是确定的。

## key 不是必填项

`base_url` + `model` 就足以确定"往哪发、发什么"。
**API Key 故意不列为必需**：本地的 Ollama / LM Studio 根本不校验密钥
（D-038 明确要求支持本地端点），把它设成必填等于把这条路线挡在门外。
所以"配置完整"和"密钥已填"是两个独立的事实，界面上要分开显示。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from backend.config import Settings
from backend.services.ai.errors import AiError
from backend.services.ai.openai_compat import OpenAICompatProvider
from backend.services.ai.provider import CHAT_ROLE_USER, ChatMessage

# 只有 OpenAI Chat Completions 兼容协议一种。看起来像"锁死一家"，
# 实际上 DeepSeek / 智谱 / 月之暗面 / 通义 / Ollama 都兼容这个协议，
# 换供应商只改 .env 里的 base_url 和 model，代码不动（D-038）。
# 空串是 .env 里 PKOS_AI_PROVIDER 留空的默认写法，等同于 openai。
SUPPORTED_PROVIDERS: tuple[str, ...] = ("", "openai")

# settings 字段名 → 环境变量名。报错时要给用户**能照着改的那个名字**，
# 而不是 Python 属性名——他去设置的是 .env 文件。
_REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("ai_base_url", "PKOS_AI_BASE_URL"),
    ("ai_model", "PKOS_AI_MODEL"),
)


class AiNotConfigured(Exception):
    """AI 还没配好，或者配的是分派不到的 Provider 类型。

    ⚠️ 刻意**不是** `AiError` 的子类。`AiError` 那五个类对应
    `ai_messages.error_kind` 的五个取值，而那一段词表描述的是
    "**上游调用失败了**"。本地配置不全既没发出请求、也没有 kind 可落库，
    混进去会破坏 `tests/test_ai_provider.py` 锁住的"异常类 ↔ 列词表一一对应"。
    """

    def __init__(self, missing: tuple[str, ...], hint: str | None = None) -> None:
        self.missing = tuple(missing)
        self.hint = hint
        super().__init__(self.user_message)

    @property
    def user_message(self) -> str:
        if self.hint:
            return self.hint
        return "还没有配置 AI 服务，请到设置页填写 API 地址和模型名。"


def is_configured(settings: Settings) -> bool:
    """能不能真正发出去请求。只看必填的两项，key 不参与判断（见文件顶部）。"""
    return bool(settings.ai_base_url.strip() and settings.ai_model.strip())


def build_provider(settings: Settings) -> OpenAICompatProvider:
    """按当前配置构造 Provider。配置不全时抛 `AiNotConfigured`，不返回 None。

    返回 None 会迫使每个调用点写 `if provider is None`；漏一个就是 AttributeError。
    抛异常则让"忘了判断"在测试里当场炸。
    """
    provider_name = settings.ai_provider.strip().lower()
    if provider_name not in SUPPORTED_PROVIDERS:
        raise AiNotConfigured(
            ("PKOS_AI_PROVIDER",),
            hint=f"暂不支持 PKOS_AI_PROVIDER={settings.ai_provider!r}，"
            "目前只支持 openai 兼容协议（DeepSeek、智谱、Ollama 等都用这个）。"
            "请把该项留空或填 openai。",
        )

    missing = tuple(
        env_name for field, env_name in _REQUIRED_FIELDS if not getattr(settings, field).strip()
    )
    if missing:
        raise AiNotConfigured(missing)

    return OpenAICompatProvider(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        json_mode=settings.ai_json_mode,
    )


@dataclass(frozen=True)
class ProbeResult:
    """连通性测试的结果。`error_kind` 用的是 `ai_messages.error_kind` 那套词表。"""

    ok: bool
    model: str
    duration_ms: int
    error_kind: str | None
    message: str


# 测试连接用的提示词。刻意极短——这项检查的目的在于"链路通不通"，
# 内容一个字都不需要，所以读到一个碎片就关闭流（见 probe_connection）。
_PROBE_PROMPT = "连通性测试，请只回复：收到"


def probe_connection(provider: OpenAICompatProvider) -> ProbeResult:
    """发一次最小请求，验证地址、模型名、密钥这三样对不对。

    ⚠️ 用 `stream_chat()` 而不是 `chat()`：
    - `chat()` 必须给一个 Pydantic schema 才能解析，而这里不需要任何结构化结果；
    - 流式读到第一段就能确认"鉴权通过 + 模型名有效 + 链路通"，
      然后立刻关闭——读满一整条回答对测试连接毫无意义，那是要花钱的。

    Provider 的关闭由调用方（接口层的依赖）负责，这里不关。
    """
    started = time.perf_counter()
    stream = provider.stream_chat([ChatMessage(role=CHAT_ROLE_USER, content=_PROBE_PROMPT)])
    try:
        for _chunk in stream:
            break
    except AiError as exc:
        # exc.user_message 是固定文案，不含 key、不含响应体（C6）
        return ProbeResult(
            ok=False,
            model=provider.model_id,
            duration_ms=_elapsed_ms(started),
            error_kind=exc.error_kind,
            message=exc.user_message,
        )
    finally:
        # 显式关闭，不等引用计数。真实实现的 stream_chat 里面是
        # `with client.stream(...) as response`，靠"生成器被回收"来释放响应体
        # 在 CPython 上碰巧可行，但那是在赌实现而不是在描述行为。
        stream.close()

    return ProbeResult(
        ok=True,
        model=provider.model_id,
        duration_ms=_elapsed_ms(started),
        error_kind=None,
        message=f"已连上 {provider.model_id}，可以开始使用。",
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
