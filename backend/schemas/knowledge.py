"""知识单元的 Pydantic 模型：外部数据的校验边界。

两条贯穿全文件的原则：

1. **长度上限必须与数据库列宽一致**。不一致的话，超长数据会绕过校验、
   一路走到 SQLite 才炸，用户看到的是 500 而不是清晰的 422。
   `tests/test_schemas.py` 会断言两边对得上。

2. **取值词表用 `Literal` 写死在这里，而不是从模型常量动态生成**。
   Python 的 `Literal` 不接受运行时的 tuple，只能写字面量。
   代价是与 `backend/db/models/` 里的常量重复了一份，
   所以有专门的测试断言两边取值集合完全相同，防止悄悄漂移。
   换来的是 OpenAPI 文档里能看到完整枚举，`/docs` 页面可以直接试。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ===== 取值词表 =====
# 与 backend/db/models/knowledge.py 的 KNOWLEDGE_SOURCE_TYPES、STATUSES 对应
KnowledgeSourceType = Literal["manual", "conversation"]
KnowledgeStatus = Literal["active", "archived"]
CreatedBy = Literal["user", "ai_confirmed"]

# 与 backend/db/models/source.py 的 SOURCE_KINDS 对应
SourceKind = Literal[
    "book",
    "article",
    "video",
    "course",
    "podcast",
    "paper",
    "conversation",
    "other",
]


def _clean(value: str | None) -> str | None:
    """去掉首尾空白；只剩空白则归一化成 None。

    保留内部换行（用户的理解常常是多段的），只清理两端。
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class _StrictModel(BaseModel):
    """所有模型的共同配置。

    `extra="forbid"` 是关键：前端多传一个拼错的字段名（比如 `titel`）时，
    直接报 422 指出来，而不是被静默忽略、让用户以为改成功了。
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ===== 学习来源 =====


class SourceCreate(_StrictModel):
    source_type: SourceKind = "other"
    title: str = Field(min_length=1, max_length=300)
    url_or_reference: str | None = Field(default=None, max_length=1000)
    note: str | None = None

    @model_validator(mode="after")
    def _other_requires_note(self) -> SourceCreate:
        """选「其他」时必须写清楚到底是什么来源（DECISIONS D-029）。

        不然信息就丢了：半年后看到一条 source_type='other' 且没有任何说明的记录，
        完全想不起来是从哪学的，这条来源等于白记。
        """
        if self.source_type == "other" and not (self.note or "").strip():
            raise ValueError(
                "来源类型选择「其他」时，备注必填：请写清楚具体是什么来源"
                "（例如「某 up 主的直播」「和同事的讨论」）"
            )
        return self


class SourceRead(_StrictModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    source_type: str
    title: str
    url_or_reference: str | None
    note: str | None
    created_at: datetime


# ===== 知识版本 =====


class KnowledgeVersionRead(_StrictModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    version_number: int
    user_understanding: str
    reasoning: str | None
    open_questions: str | None
    # AI 的修订建议与用户原文分列存储，前端必须能区分展示（Blueprint 原则 2）
    ai_revision: str | None
    change_reason: str | None
    created_by: str
    created_at: datetime


# ===== 创建 =====


class KnowledgeCreate(_StrictModel):
    # 「名称」和「自己的理解」是必填的。
    #
    # 强制写理解是这个产品的关键设计：如果允许只写标题就保存，
    # 会退化成收藏癖——存了 500 条，一条都没学会。
    # 强制用自己的话说一遍，记录本身就完成了第一次学习（见 docs/PRD.md 3.1）。
    title: str = Field(min_length=1, max_length=200)
    user_understanding: str = Field(min_length=1)
    domain: str | None = Field(default=None, max_length=100)
    summary: str | None = None
    source_type: KnowledgeSourceType = "manual"
    reasoning: str | None = None
    open_questions: str | None = None
    change_reason: str | None = Field(default=None, max_length=200)
    sources: list[SourceCreate] = Field(default_factory=list)

    @field_validator("domain", "summary", "reasoning", "open_questions", "change_reason")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return _clean(value)

    @model_validator(mode="after")
    def _require_real_understanding(self) -> KnowledgeCreate:
        """「自己的理解」不能只有标点或空白。

        min_length=1 拦不住 "." 或 "，" 这种应付式填写。
        这里要求至少有一个字母、数字或汉字。
        """
        if not any(ch.isalnum() for ch in self.user_understanding):
            raise ValueError(
                "「自己的理解」需要写出实际内容，不能只填标点符号。"
                "用自己的话说清楚这个知识是什么，是这条记录最有价值的部分。"
            )
        return self


# ===== 更新 =====


class KnowledgeUpdate(_StrictModel):
    """部分更新。**只有请求体里显式出现的字段才会被应用。**

    这一点靠 Pydantic 的 `model_fields_set` 实现，service 层必须用它来判断，
    不能用 `model_dump()` 后逐个检查 None——那样就无法区分
    「用户没传这个字段」和「用户想把这个字段清空」。

    举例：`{"reasoning": null}` 是"把理解依据清空"（要应用），
    `{}` 是"什么都不改"（不能碰 reasoning）。两者 dump 出来都是 None。

    注意这里**没有 status 字段**：归档不是"编辑"，走独立的
    archive / restore 接口。混在一起会让更新逻辑要额外判断
    "改状态算不算理解变化、要不要建新版本"，而答案是不该建。
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=100)
    summary: str | None = None
    user_understanding: str | None = Field(default=None, min_length=1)
    reasoning: str | None = None
    open_questions: str | None = None
    change_reason: str | None = Field(default=None, max_length=200)
    # 传了 sources 就是整体替换（含空数组=清空全部来源），不传则保持原样
    sources: list[SourceCreate] | None = None

    @field_validator("domain", "summary", "reasoning", "open_questions", "change_reason")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        return _clean(value)

    @model_validator(mode="after")
    def _require_real_understanding(self) -> KnowledgeUpdate:
        if self.user_understanding is not None and not any(
            ch.isalnum() for ch in self.user_understanding
        ):
            raise ValueError(
                "「自己的理解」需要写出实际内容，不能只填标点符号。"
            )
        return self


# ===== 读取 =====


class KnowledgeListItem(_StrictModel):
    """列表项：不含版本历史，避免列表查询把全部版本都拖出来。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    title: str
    domain: str | None
    summary: str | None
    source_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    # 下面两个由 service 层填充，不是 ORM 列
    version_count: int = 0
    current_understanding: str | None = None


class KnowledgeRead(_StrictModel):
    """详情页：完整数据，含当前理解、全部版本历史和来源。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str
    title: str
    domain: str | None
    summary: str | None
    source_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    # 前端靠它在 versions 里定位"当前生效的是哪一版"并高亮。
    # 不靠"版本号最大的那条"来推断——那是隐含假设，一旦将来支持回滚就会失效。
    current_version_id: str | None = None
    sources: list[SourceRead] = Field(default_factory=list)
    versions: list[KnowledgeVersionRead] = Field(default_factory=list)


class KnowledgePage(_StrictModel):
    """分页包装。`total` 用于前端显示"共 N 条"和算总页数。"""

    items: list[KnowledgeListItem]
    total: int
    limit: int
    offset: int


class SearchResultItem(_StrictModel):
    """搜索结果项。`matched_by` 说明命中的是标题还是正文，前端可以高亮。"""

    knowledge_id: str
    title: str
    domain: str | None
    status: str
    matched_by: Literal["title", "body", "both"]
    snippet: str | None = None


class SearchResult(_StrictModel):
    items: list[SearchResultItem]
    total: int
    query: str
    # 实际走了哪条检索路径。FTS5 trigram 要求至少 3 个字符，
    # 1~2 字查询走 LIKE 兜底并查权威表，所以索引即使不同步也不影响短查询。
    # 把这个告诉前端，便于在界面上说明结果来源（DECISIONS D-007）。
    strategy: Literal["fts", "like"]


class DomainSuggestion(_StrictModel):
    """领域自动补全的一项。`count` 让前端可以把常用的排在前面。"""

    domain: str
    count: int
