"""AI 结构化输出的校验边界（硬约束 C3）。

这两个模型是「AI 说的话能不能进这个系统」的唯一判据。
AI 返回的任何东西都要先过这里，过不了就不写库、不显示成结果。

## ⚠️ 与 `schemas/knowledge.py` 的三个刻意不同

**1. 用 `extra="ignore"`，而不是 `extra="forbid"`。**
面向人的接口要 forbid——前端多传一个拼错的 `titel`，必须当场报出来，
否则用户以为改成功了。但 AI 输出不能：模型多返回一个我们没定义的字段
（比如 `overall_score`）是很常见的行为，而且**重试也修不好**——
它对每个请求都这么答，forbid 会让这条工作流对那家服务商永久不可用。
更根本的是：`ai_reviews.result_json` 存的是**校验后模型的转储**而不是原始返回
（不能存原文，C6），所以多余字段无论如何都留不下来。
既然注定丢弃，就不该因为它拒绝整份结果。

**2. 文本允许为空。**
`KnowledgeCreate.title` 和 `user_understanding` 是 `min_length=1`——
空标题、空理解的记录不许存在，这是防止产品退化成收藏癖的关键设计。
但**草稿不是记录**。AI 从一段还没聊清楚的对话里提取不出理解时，
正确行为是返回空，前端提示"这段对话还没形成理解"，由用户补完再保存。
如果草稿层面就 min_length=1，AI 会被迫**编一段出来**才过校验——
那正是原则 1 最不能接受的事。
最终的落库校验仍然由 `KnowledgeCreate` 把关，一道都没松。

**3. 来源条目不套用「选 other 必填备注」那条规则。**
那条规则（D-029）在 `SourceCreate` 上，确认保存时照样生效。
草稿层面不查，是因为用户看到 AI 建议了一个 `other` 却没写备注时，
**该做的是让他补一句**，而不是把整份提取结果丢掉重来。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.schemas.knowledge import SourceKind

# ===== 长度上限 =====
# 和 prompts.py 里承诺给 AI 的 JSON 形状必须一致，
# tests/test_ai_schemas.py 里有断言"模型字段 == 提示词字段"的两条锁。

# 审查条目里的每段话。600 字是给人看的上限——AI 写得再长，
# 说明它没在"挑最要紧的几处"，而是在倾倒。
#
# 这里**不写 strip_whitespace**：清理空白由 `_AiOutputModel` 的
# `str_strip_whitespace=True` 统一负责，它对嵌套在 list 里的 str 同样生效。
# 两处都写会让人分不清哪一处才是真防线（变异测试实测：删掉条目级这句，
# 68 个测试全绿）。`min_length=1` 因此能拦住"只有一串空格"的空条目。
_REVIEW_TEXT = Annotated[str, StringConstraints(min_length=1, max_length=600)]
# 追问是一句话，不该写成段落。
_FOLLOW_UP = Annotated[str, StringConstraints(min_length=1, max_length=300)]
# 一个类别最多 20 条。超过就说明 AI 在凑数，前端也渲染不动。
_MAX_ITEMS = 20


class _AiOutputModel(BaseModel):
    """AI 输出的共同配置。理由见文件顶部第 1 条。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ============================================================ 工作流 B：提取草稿


class SourceDraft(_AiOutputModel):
    """AI 建议的学习来源。

    ⚠️ 条目内部的字段名要和 `schemas.knowledge.SourceCreate` **完全一致**，
    这样确认保存时能直接喂进去，不需要一层会写错的字段翻译。
    有专门的测试断言两边字段集合相同。

    但**容器字段名是故意不同的**：草稿里叫 `suggested_sources`，
    落库时叫 `sources`。改名不是随手——"建议"和"已确认"是两件事，
    用同一个名字会让人误读这份数据的状态。
    第⑥步做转接时按 `tests/test_ai_schemas.py` 里那份映射表来。
    """

    source_type: SourceKind = "other"
    title: str = Field(default="", max_length=300)
    url_or_reference: str | None = Field(default=None, max_length=1000)
    note: str | None = None


class KnowledgeDraft(_AiOutputModel):
    """从对话里提取出的知识草稿。只在内存和前端里活着，用户确认后才落库。"""

    title: str = Field(default="", max_length=200)
    domain: str | None = Field(default=None, max_length=100)
    summary: str | None = None

    # ⚠️ 这两个字段**永远不能合并**。
    # 合成就再也分不清这条知识里有多少是用户自己的东西，
    # 而"原始理解不可被 AI 覆盖"（原则 1 / C1）全靠这个区分才成立。
    #
    # `ai_supplements` 没有对应的数据库列，这是对的：它只在草稿和确认界面上存在。
    # 用户要是采纳了某条补充，那句话得**由他打字写进** user_understanding，
    # 不能由程序拼——程序一拼，这条记录就说不清是谁说的了。
    user_understanding: str = ""
    ai_supplements: list[Annotated[str, StringConstraints(max_length=600)]] = Field(
        default_factory=list, max_length=_MAX_ITEMS
    )

    # 与 knowledge_versions 同名字段，一一对应，不做翻译
    reasoning: str | None = None
    open_questions: str | None = None

    suggested_sources: list[SourceDraft] = Field(default_factory=list, max_length=_MAX_ITEMS)

    @property
    def has_understanding(self) -> bool:
        """有没有形成可以理解。前端据此决定是渲染表单还是提示"还没聊出内容"。"""
        return bool(self.user_understanding.strip())


# ============================================================ 工作流 C：知识审查


class CorrectPoint(_AiOutputModel):
    point: _REVIEW_TEXT
    why: _REVIEW_TEXT


class ReviewError(_AiOutputModel):
    # `claim` 必须能指回用户原话的具体一句。指不回来说明 AI 自己也没把握，
    # 那种内容应该待在 needs_verification 里，而不是冒充错误判定。
    claim: _REVIEW_TEXT
    correction: _REVIEW_TEXT
    evidence: _REVIEW_TEXT


class ReviewOmission(_AiOutputModel):
    missing: _REVIEW_TEXT
    why_it_matters: _REVIEW_TEXT


class NeedsVerificationItem(_AiOutputModel):
    item: _REVIEW_TEXT
    how_to_verify: _REVIEW_TEXT


class ReviewResult(_AiOutputModel):
    """审查结果的五分类。

    五个类别**一个都不能省**，尤其 `needs_verification`——
    它承认 AI 自己也不确定。只有"对/错"两类会逼 AI 在没把握时
    也给出确定判断，那正是原则 6 要防止的（AI_WORKFLOWS 第四节）。

    每个字段都给默认空数组：AI 漏写一个**空**数组不该让整份审查作废。
    全空也是合法结果（"我看不出什么问题"），不强制它必须挑出点毛病来。
    """

    correct_points: list[CorrectPoint] = Field(default_factory=list, max_length=_MAX_ITEMS)
    errors: list[ReviewError] = Field(default_factory=list, max_length=_MAX_ITEMS)
    omissions: list[ReviewOmission] = Field(default_factory=list, max_length=_MAX_ITEMS)
    follow_up_questions: list[_FOLLOW_UP] = Field(default_factory=list, max_length=_MAX_ITEMS)
    needs_verification: list[NeedsVerificationItem] = Field(
        default_factory=list, max_length=_MAX_ITEMS
    )

    @property
    def is_empty(self) -> bool:
        """五个类别都没内容。前端要能说清"AI 没提意见"不等于"AI 说你全对"。"""
        return not (
            self.correct_points
            or self.errors
            or self.omissions
            or self.follow_up_questions
            or self.needs_verification
        )
