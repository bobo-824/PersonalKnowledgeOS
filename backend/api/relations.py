"""知识关系的 REST 接口（阶段 4 第⑤步，工作流 E）。

分层约定同 api/review.py：本层只做解析请求、调 service、映射状态码。
**静态路径 /candidates /pending /graph /stats /knowledge 必须声明在
/{relation_id}/... 之前**——不然会被当成关系 id。

花费边界（D-042 / D-066）：
- GET /candidates 和 GET /knowledge/{id}/related 纯本地检索，免费；
- POST /judge 每对一次 AI 请求，前端点按钮前按对数明示，后端不重复确认。
AI 失败不抛出——批量判断的失败记在报告里逐对返回，
前端把"失败的对"单独展示，成功的照常进待审核队列。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.deps import ProviderDep
from backend.db.base import utcnow
from backend.db.session import get_session
from backend.schemas.relation_api import (
    AcceptedRelationForKnowledgeRead,
    CandidateListRead,
    CandidatePairRead,
    DecideRequest,
    GraphEdgeRead,
    GraphNodeRead,
    GraphRead,
    GrowthStatsRead,
    JudgeFailureRead,
    JudgePairRequest,
    JudgeReportRead,
    JudgeRequest,
    RelatedForKnowledgeRead,
    RelationRead,
    RelationTypeRequest,
)
from backend.services import relation_service

router = APIRouter(prefix="/api/relations", tags=["relations"])

SessionDep = Annotated[Session, Depends(get_session)]

LimitQuery = Annotated[int, Query(ge=1, le=200, description="最多返回多少条")]


def _relation_read(relation) -> RelationRead:
    """ORM 行 → 响应模型。标题从懒加载的 source/target 关系取
    （模型配了 lazy="joined"，一定已加载）。"""
    return RelationRead(
        id=relation.id,
        source_id=relation.source_id,
        target_id=relation.target_id,
        source_title=relation.source.title if relation.source is not None else "（已删除）",
        target_title=relation.target.title if relation.target is not None else "（已删除）",
        relation_type=relation.relation_type,
        explanation=relation.explanation,
        evidence=relation.evidence,
        confidence=relation.confidence,
        status=relation.status,
        model=relation.model,
        prompt_version=relation.prompt_version,
        reviewed_at=relation.reviewed_at,
        created_at=relation.created_at,
        updated_at=relation.updated_at,
    )


# ===== 免费候选（必须在 /{relation_id} 之前） =====


@router.get("/candidates", response_model=CandidateListRead)
def list_candidates(session: SessionDep, limit: LimitQuery = 20) -> CandidateListRead:
    """召回候选对。免费、实时计算（D-064 / D-066）：
    新增或修改知识后不用做任何事，这里天然就能看到新候选。"""
    pairs = relation_service.find_candidate_pairs(session, limit=limit)
    return CandidateListRead(
        candidates=[CandidatePairRead.model_validate(p) for p in pairs],
        generated_at=utcnow(),
    )


@router.get("/pending", response_model=list[RelationRead])
def list_pending(session: SessionDep) -> list[RelationRead]:
    """待审核队列。AI 判断过的候选在这里等用户处置（C7：用户终审）。"""
    return [_relation_read(r) for r in relation_service.list_pending_relations(session)]


@router.get("/graph", response_model=GraphRead)
def get_graph(session: SessionDep) -> GraphRead:
    """图谱数据。**只显示 accepted 的边**（工作流 E 硬要求）。"""
    data = relation_service.graph_data(session)
    return GraphRead(
        nodes=[GraphNodeRead.model_validate(n) for n in data["nodes"]],
        edges=[GraphEdgeRead.model_validate(e) for e in data["edges"]],
    )


@router.get("/stats", response_model=GrowthStatsRead)
def get_stats(session: SessionDep) -> GrowthStatsRead:
    """成长统计卡：知识总数、近 7 天新增、累计复习、连线数、领域分布。"""
    return GrowthStatsRead.model_validate(relation_service.growth_stats(session))


@router.get("/knowledge/{knowledge_id}/related", response_model=RelatedForKnowledgeRead)
def related_for_knowledge(knowledge_id: str, session: SessionDep) -> RelatedForKnowledgeRead:
    """详情页「相关知识」：这条知识的 accepted 关系 + 它的免费候选。"""
    data = relation_service.relations_for_knowledge(session, knowledge_id)
    return RelatedForKnowledgeRead(
        relations=[
            AcceptedRelationForKnowledgeRead.model_validate(r)
            for r in data["relations"]
        ],
        candidates=[CandidatePairRead.model_validate(c) for c in data["candidates"]],
    )


# ===== AI 判断（付费） =====


@router.post("/judge", response_model=JudgeReportRead)
def judge_pairs(body: JudgeRequest, session: SessionDep, provider: ProviderDep) -> JudgeReportRead:
    """让 AI 逐对判断候选。**每对一次请求**（D-042：前端先明示再调）。

    单对失败不阻断整批：失败的对带着 error_kind 回到报告里，
    其余照常落成 pending 候选。返回 200 哪怕有失败——
    "部分成功"是正常结果，不是错误。
    """
    report = relation_service.judge_pairs(
        session,
        provider,
        [(p.source_id, p.target_id) for p in body.pairs],
    )
    return JudgeReportRead(
        created=report.created,
        skipped=report.skipped,
        unrelated=report.unrelated,
        failed=[JudgeFailureRead.model_validate(f) for f in report.failed],
    )


# ===== 审核处置 =====


@router.post("/{relation_id}/decide", response_model=RelationRead)
def decide_relation(relation_id: str, body: DecideRequest, session: SessionDep) -> RelationRead:
    """处置一条候选：确认进图谱 / 拒绝 / 暂时忽略。

    终态不可反悔：拒绝过的对不会再被 AI 提出来（防重复骚扰），
    这是持久语义而不是临时的。
    """
    relation = relation_service.decide_relation(session, relation_id, body.action)
    return _relation_read(relation)


@router.put("/{relation_id}/type", response_model=RelationRead)
def update_relation_type(
    relation_id: str, body: RelationTypeRequest, session: SessionDep
) -> RelationRead:
    """审核时改关系类型（工作流 E 第四个处置动作）。只在 pending 时允许。"""
    relation = relation_service.update_relation_type(
        session, relation_id, body.relation_type
    )
    return _relation_read(relation)
