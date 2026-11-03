"""AI 接口的请求/响应模型（第⑦步的 HTTP 契约）。

## 为什么和 `schemas/ai.py` 分成两个文件

`schemas/ai.py` 是**硬约束 C3 的边界**：AI 说的话能不能进系统，唯一判据在那里。
它必须保持"只有 AI 输出模型"这一件事，混进 HTTP DTO 之后，
"C3 的校验点到底在哪一行"就要靠读了才知道。

本文件的模型描述的是**前端和后端之间**的约定，安全性要求完全不同：
请求体一律 `extra="forbid"`（前端拼错字段名必须当场 422，
不能被静默丢掉后让用户以为保存成功了）。

⚠️ 唯一的例外：`DraftConfirmRequest.draft` 复用了 C3 的 `KnowledgeDraft`，
所以那一层是 `extra="ignore"`。复用而不是另建一份"草稿编辑模型"，
是因为字段已经有三份互相锁死的定义（提示词 / C3 模型 / 数据库列），
再加第四份就是给将来漏改埋雷。用户编辑草稿时前端提交的是
它从 `/extract` 拿到的那批字段，改名会在下一层 `KnowledgeCreate`
（`extra="forbid"` + `min_length`）炸出来，不会静默丢内容。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.ai import KnowledgeDraft, ReviewResult
from backend.schemas.knowledge import KnowledgeRead

# ===== 取值词表 =====
# 与 backend/db/models/ai_review_decision.py 的 REVIEW_CATEGORIES、REVIEW_DECISIONS
# 对应。重复一份是 D-003 的既有约定：Literal 不接受运行时 tuple，
# 换来的是 /docs 里能看到完整枚举，测试再锁两边不漂移。
ReviewCategory = Literal[
    "correct_points",
    "errors",
    "omissions",
    "follow_up_questions",
    "needs_verification",
]
ReviewDecision = Literal["accepted", "ignored", "disagreed"]

# 单条文本的长度上限。对话输入没有对应的数据库列宽可对齐（content 是 Text），
# 这里的数字是**请求体**的上限：防止前端把整篇论文粘进来，
# 那既贵又会让模型抓不住重点（C5 最小必要）。
_USER_TEXT_MAX = 8000
# 会话列表一次最多返回这么多条。默认 50，和知识列表一致。
_LIST_LIMIT_MAX = 200


class _ApiModel(BaseModel):
    """请求模型：多传字段一律拒绝。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _ReadModel(BaseModel):
    """响应模型：从 ORM 对象直接构造。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# ================================================================ 会话与消息


class ConversationCreate(_ApiModel):
    """新建会话。第一句话同时用于生成标题（不让 AI 起名，见 D-042）。"""

    text: str = Field(min_length=1, max_length=_USER_TEXT_MAX)


class MessageCreate(_ApiModel):
    """追加一轮对话。响应是 SSE 流，所以这里没有对应的响应模型。"""

    text: str = Field(min_length=1, max_length=_USER_TEXT_MAX)


class MessageRead(_ReadModel):
    id: str
    seq: int
    role: str
    content: str
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int | None
    # 非 None 表示这条是**失败占位**：content 是我们自己写给用户看的那句话，
    # 不是 AI 的原文。前端要用不同样式显示，并且不把它作为上下文发给 AI。
    error_kind: str | None
    created_at: datetime


class ConversationRead(_ReadModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationRead):
    """⚠️ `messages` 由接口层用显式查询填充，不依赖 ORM 关系属性。

    `expire_on_commit=False` 下，同一 Session 里早先加载过的集合不会自动刷新，
    读关系属性可能拿到陈旧集合（阶段 2 实测踩过，见 ai_service 的 `_context_messages`）。
    """

    messages: list[MessageRead] = Field(default_factory=list)


class HistoryCleared(_ApiModel):
    """清空对话历史的结果。删除的行数必须回显，用户要能确认不是空操作。"""

    deleted_conversations: int


# ================================================================ 草稿与确认


class DraftConfirmRequest(_ApiModel):
    """用户看过、改过之后确认保存的那份草稿。

    `idempotency_key` **必填**：前端按钮 disabled 只挡得住手滑，挡不住
    超时重发和刷新后再点一次。重复提交造成重复记录是阶段 2 的明确验收禁止项，
    所以去重必须落在服务端（见 ai_service.confirm_draft 与 D-052）。
    """

    draft: KnowledgeDraft
    idempotency_key: str = Field(
        min_length=8,
        max_length=64,
        description="前端为每次确认生成的 UUID（列宽 String(64)，别塞别的东西进来）",
    )


class DraftConfirmResult(_ApiModel):
    knowledge: KnowledgeRead
    # True = 这个幂等键之前已经创建过，返回的是**当时那条**，没有新增记录
    replayed: bool


# ================================================================ 审查与处置


class ReviewCreateRequest(_ApiModel):
    knowledge_id: str = Field(min_length=1, max_length=36)


class ReviewDecisionRequest(_ApiModel):
    category: ReviewCategory
    # 定位靠 (category, item_index)，所以 result_json 写入后永不 UPDATE：
    # 数组顺序一变，已有的处置就会指到别的条目上。
    item_index: int = Field(ge=0, description="该类别数组里的下标，从 0 开始")
    decision: ReviewDecision


class ReviewDecisionRead(_ApiModel):
    category: str
    item_index: int
    decision: str
    updated_at: datetime


class ReviewRead(_ApiModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    knowledge_id: str
    version_id: str
    # 审的是第几版。前端要显示"这份审查针对 v2"，
    # 用户采纳后会生成 v3，届时这份审查就该看得出是审 v2 的。
    version_number: int
    model: str
    prompt_version: str
    created_at: datetime
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int | None
    result: ReviewResult
    decisions: list[ReviewDecisionRead] = Field(default_factory=list)
    # 五个类别全空。前端据此显示"AI 没有提出意见"，
    # 而不是显示一个空白的审查区（也不能显示成"AI 说你全对"）。
    is_empty: bool


class AcceptReviewRequest(_ApiModel):
    """采纳建议后，由**用户重写**的理解。

    ⚠️ `new_understanding` 必填且必须是真的由用户提交的那句话——
    这是 C1 的结构性保证（详见 ai_service.accept_ai_review 的说明）。
    采纳了哪些条目**不在这里传**：服务端读数据库里 decision='accepted' 的处置。
    """

    new_understanding: str = Field(min_length=1, max_length=_USER_TEXT_MAX)
    reasoning: str | None = None
    open_questions: str | None = None
    change_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _require_real_understanding(self) -> "AcceptReviewRequest":
        """和 KnowledgeCreate 同一条规则：只填标点不算写了理解。"""
        if not any(ch.isalnum() for ch in self.new_understanding):
            raise ValueError(
                "「自己的理解」需要写出实际内容。请自己把采纳的部分组织成一句话，"
                "不要直接照抄 AI 的原文。"
            )
        return self


# ================================================================ 设置页


class AiConfigView(_ApiModel):
    """当前 AI 配置。**永不含完整密钥**（Blueprint 9.1 / C6）。"""

    provider: str
    base_url: str
    model: str
    timeout_seconds: int
    json_mode: bool
    api_key_set: bool
    # 只有末 4 位，形如 `…abcd`。见 settings_service._mask_key 的取舍说明。
    api_key_hint: str | None
    # base_url + model 齐了才 True。和 api_key_set 分开显示：
    # 本地 Ollama 是"configured 但 key 未填"的合法状态。
    configured: bool
    hint: str | None
    # 被真实环境变量盖住的键名。这些项**写 .env 不生效**，必须告诉用户。
    shadowed_keys: list[str] = Field(default_factory=list)


class AiConfigUpdateRequest(_ApiModel):
    """改 AI 配置。**只有请求体里出现的字段会被写入 .env**，其余保持原样。

    和 `KnowledgeUpdate` 一样靠 `model_fields_set` 区分"没传"和"传了空值"。
    所有字段都禁止空串（min_length 兜住），所以
    **不存在"提交空串把密钥删掉"这种误操作**：想删密钥请直接编辑 .env 那一行。
    """

    provider: str | None = Field(default=None, min_length=1, max_length=40)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    api_key: str | None = Field(default=None, min_length=8, max_length=300)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    timeout_seconds: int | None = Field(default=None, ge=5, le=600)
    json_mode: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _absolute_url(cls, value: str | None) -> str | None:
        """只挡"一眼就不是地址"的输入，不去猜端点对不对。

        真正的验证是设置页那个"测试连接"按钮——它发一次真请求，
        比在这里写一堆正则可靠，也不会把合法的自签名内网地址误杀。
        """
        if value is None:
            return None
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError(
                "API 地址要以 http:// 或 https:// 开头，例如 "
                "https://api.deepseek.com/v1（本地 Ollama 形如 http://127.0.0.1:11434/v1）"
            )
        return value

    @model_validator(mode="after")
    def _require_at_least_one_change(self) -> "AiConfigUpdateRequest":
        """空请求体必须拒绝：否则会返回一个 200，用户以为保存成功了。"""
        if not self.model_fields_set:
            raise ValueError("没有任何要修改的项")
        return self


class AiConfigUpdateResult(_ApiModel):
    config: AiConfigView
    # 实际写进 .env 的**环境变量名**，用户能对着文件核对
    changed_keys: list[str]
    # 写之前的 .env 备份到了哪里（含密钥，所以落在 data/ 下，被 Git 忽略）
    backup_file: str | None
    # False = 这些项被同名环境变量盖住了，界面要明说"改了暂时不生效"
    takes_effect_now: bool
    message: str


class AiConnectionTest(_ApiModel):
    """"测试连接"的结果。失败时 `error_kind` 用的是 `ai_messages.error_kind` 那套词表，
    所以前端可以把同一个"该重试 / 该改 key / 该改模型名"的判断复用在这一处。"""

    ok: bool
    model: str
    duration_ms: int
    error_kind: str | None
    message: str
