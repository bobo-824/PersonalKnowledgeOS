"""关系接口的请求/响应模型（阶段 4 第⑤步的 HTTP 契约）。

纪律同 `schemas/review_api.py`：请求体一律 `extra="forbid"`，
响应模型从 service 数据直接构造。
`Literal` 词表与 `backend/domain/relation.py` 的常量各写一份，
是 D-003 的既有约定：换 /docs 里能看到完整枚举，测试锁两边不漂移。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 审核状态与处置动作。数据库里的四态；「修改类型」不是状态，单独的端点。
RelationStatus = Literal["pending", "accepted", "rejected", "ignored"]
DecideAction = Literal["accepted", "rejected", "ignored"]


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _DataReadModel(BaseModel):
    """从 service 的 dataclass 直接构造（CandidatePair / JudgeFailure）。
    _ApiModel 的纪律 + from_attributes。"""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, from_attributes=True
    )


class _ReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


# ================================================================ 免费候选


class CandidatePairRead(_DataReadModel):
    """召回出的一对待判断。没有理解正文（C5）：队列只帮人看出
    "这两条可能有关"，正文是 AI 判断时才读的。"""

    source_id: str
    target_id: str
    source_title: str
    target_title: str
    source_domain: str | None
    target_domain: str | None


class CandidateListRead(_ApiModel):
    candidates: list[CandidatePairRead]
    generated_at: datetime


# ================================================================ AI 判断


class JudgePairRequest(_ApiModel):
    source_id: str
    target_id: str


class JudgeRequest(_ApiModel):
    """让 AI 判断一批候选。**付费操作**：每对一次请求，
    前端点按钮前按 `len(pairs)` 明示（D-042），后端不重复确认。"""

    pairs: list[JudgePairRequest] = Field(min_length=1, max_length=50)


class JudgeFailureRead(_DataReadModel):
    source_id: str
    target_id: str
    error_kind: str
    message: str


class JudgeReportRead(_ApiModel):
    created: int
    skipped: int
    unrelated: int
    failed: list[JudgeFailureRead]


# ================================================================ 待审核队列


class RelationRead(_ReadModel):
    """一条关系候选。source/target 的标题由 API 层从懒加载关系补上，
    队列里得让人看出"哪两条知识"。"""

    id: str
    source_id: str
    target_id: str
    source_title: str
    target_title: str
    relation_type: str
    explanation: str
    evidence: str
    confidence: float
    status: RelationStatus
    model: str | None
    prompt_version: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DecideRequest(_ApiModel):
    action: DecideAction


class RelationTypeRequest(_ApiModel):
    """改关系类型。六类之一，或干净的自定义短语（D-065）。"""

    relation_type: str = Field(min_length=1, max_length=32)


# ================================================================ 图谱与统计


class GraphNodeRead(_ApiModel):
    id: str
    title: str
    domain: str | None


class GraphEdgeRead(_ApiModel):
    id: str
    source_id: str
    target_id: str
    relation_type: str
    explanation: str


class GraphRead(_ApiModel):
    """**只含 accepted 的边**（工作流 E 硬要求）。节点是全部 active 知识。"""

    nodes: list[GraphNodeRead]
    edges: list[GraphEdgeRead]


class GrowthStatsRead(_ApiModel):
    total_knowledge: int
    added_last_7_days: int
    total_reviews: int
    total_relations: int
    domains: dict[str, int]
    generated_at: datetime


# ================================================================ 详情页相关知识


class AcceptedRelationForKnowledgeRead(_ApiModel):
    id: str
    direction: Literal["source", "target"]
    relation_type: str
    other_id: str
    other_title: str
    explanation: str


class RelatedForKnowledgeRead(_ApiModel):
    relations: list[AcceptedRelationForKnowledgeRead]
    candidates: list[CandidatePairRead]
