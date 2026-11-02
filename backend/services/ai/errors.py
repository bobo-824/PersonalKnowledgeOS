"""AI 旁路失败的分类。

每类失败给用户的提示**必须不一样**（AI_WORKFLOWS 第一节）：
统一报「AI 出错了」等于没报——用户分不清该重试、该改网络、该改模型名、还是该去设置页换 key。

⚠️ 原本设计只有四类（网络不通 / 超时 / Key 无效 / 畸形 JSON）。实现时补了第五类
`AiBadRequest`：**模型名写错返回 400**，这是新手最容易踩的，而归进"无法连接"
会把人引向完全错误的排查方向。详见 docs/DECISIONS.md。

## ⚠️ 隐私硬约束（C6），改这个文件前先读

这些异常会被上层 `logger.exception()` 打出来，而 traceback 里能看到局部变量。
所以本模块的异常**一律不得持有**：

- AI 返回的原始正文（哪怕畸形的）—— `AiSchemaInvalid` 只记**字段名**
- API Key，以及任何可能含 key 的片段
- 用户的知识正文

`user_message` 是四个类各自固定的文案，不含任何动态内容，
所以把它写进日志或显示给用户都是安全的。
"""

from __future__ import annotations

from typing import ClassVar

from backend.db.models import (
    ERROR_KIND_AUTH_REJECTED,
    ERROR_KIND_BAD_REQUEST,
    ERROR_KIND_SCHEMA_INVALID,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_UNAVAILABLE,
)


class AiError(Exception):
    """AI 调用失败的基类。

    `error_kind` 直接复用 `ai_messages.error_kind` 列的词表，
    这样 service 层捕获异常后不需要写一串 if/elif 去翻译成字符串——
    翻译表就长在异常类上，加一类失败时测试会提醒漏了对应关系
    （`tests/test_ai_provider.py` 断言异常类上的 kind 恰好覆盖列词表，不多不少）。
    """

    error_kind: ClassVar[str]
    user_message: ClassVar[str]

    def __init__(self, detail: str | None = None) -> None:
        # detail 只允许放**不敏感**的诊断信息（如 base_url、超时秒数）。
        # 默认文案用 user_message，保证调用方不传东西也不会丢信息。
        self.detail = detail
        super().__init__(detail or self.user_message)


class AiUnavailable(AiError):
    """连不上服务：DNS 失败、代理挡了、对方宕机、连接被重置。

    用户能做的：检查网络/代理，或换个 base_url。**重试可能有用也可能没用。**
    """

    error_kind = ERROR_KIND_UNAVAILABLE
    user_message = "无法连接 AI 服务，请检查网络或代理设置。"


class AiTimeout(AiError):
    """连上了但没在 `PKOS_AI_TIMEOUT_SECONDS` 内返回。

    和 `AiUnavailable` 分开是因为处置不同：超时**重试往往有效**，
    而且用户更可能需要的是把超时调长，而不是去查网络。
    """

    error_kind = ERROR_KIND_TIMEOUT
    user_message = "AI 服务响应超时，可以再试一次，或在设置页调大超时时间。"


class AiAuthRejected(AiError):
    """服务端返回 401/403：key 错了、过期了、或这个 key 没权限用这个模型。

    **重试没有用**，必须去设置页改 key，所以这一类不能被并进 AiUnavailable，
    否则用户会反复点重试。
    """

    error_kind = ERROR_KIND_AUTH_REJECTED
    user_message = "API Key 被服务拒绝，请到设置页检查密钥。"


class AiSchemaInvalid(AiError):
    """返回 200 但内容不合契约：不是 JSON、缺字段、类型不对、枚举值非法。

    这一类是 C3 的落点——**校验失败绝不写库**。
    `invalid_fields` 只装字段名，供日志定位用；畸形原文一行都不留。
    """

    error_kind = ERROR_KIND_SCHEMA_INVALID
    user_message = "AI 返回的内容无法解析，请重试；反复出现的话建议换一个模型。"

    def __init__(self, invalid_fields: tuple[str, ...], detail: str | None = None) -> None:
        self.invalid_fields = tuple(invalid_fields)
        super().__init__(detail or "字段校验失败：" + ", ".join(self.invalid_fields))


class AiBadRequest(AiError):
    """服务端返回 4xx（401/403 除外）：它听懂了连接，但拒绝这个请求。

    **这一类必须和 `AiUnavailable` 分开**，因为它在个人项目里的高频成因是
    「设置页的模型名写错」和「base_url 少写了 /v1」——这两种情况的解法都是
    去改设置，而不是去查网络。并进"无法连接"会把用户引到完全错的方向。

    响应体里的错误说明（`error.message`）常常正好点出问题，
    但那是服务端生成的自由文本，可能包含我们发过去的字段名甚至片段，
    所以**只取 HTTP 状态码**放进 detail，正文一个字都不带。
    """

    error_kind = ERROR_KIND_BAD_REQUEST
    user_message = "AI 服务拒绝了这个请求，请检查设置页里的模型名和 API 地址。"

    def __init__(self, status_code: int | None = None, detail: str | None = None) -> None:
        self.status_code = status_code
        super().__init__(detail or f"服务端返回状态码 {status_code}")
