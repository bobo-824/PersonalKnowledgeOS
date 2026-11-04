"""知识关系的业务编排（阶段 4，工作流 E）。事务边界同其他 service：
一个业务动作 = 一个函数 = 一次 commit。

## 工作流 E 的三段，费用和存储完全不同

1. **召回（免费，不落库，不调 AI）**：`find_candidate_pairs` 纯本地检索
   （FTS5 全文 + 相似标题，D-064），每次查询实时计算——"新增/修改知识
   自动进候选"因此不需要任何钩子或队列表，新知识天然就在候选池里。
   候选对**不写 knowledge_relations**：这张表的 explanation / evidence
   是 NOT NULL，只有 AI 判断过的东西才配有一行。
2. **AI 判断（花钱，逐对）**：`judge_pairs` 每对发一次请求，输出过
   C3（RelationSuggestion）才落一行 status='pending'。单对失败不阻断
   其余对——批量判断的失败模式是"部分成功"，不是"全有或全无"。
3. **审核（用户，免费）**：确认 / 拒绝 / 忽略 / 修改类型。
   状态机在 `backend/domain/relation.py`，本文件不自己比较状态。

## 防重复骚扰的两道锁

- **去重在召回侧**：任何状态（含 rejected / ignored）的既有关系对，
  无论方向，都不再出现在候选里——rejected 行永久保留就是防骚扰的（工作流 E）。
- **唯一约束在数据库侧**：同一对知识同方向只允许一行（含 rejected）。
  召回检查和 INSERT 之间即使有并发窗口，约束也是最后一道兜底。

provider 是入参而不是内部构造，理由同 ai_service：测试塞 FakeProvider，
全部 AI 测试不联网、不花钱（D-038）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.base import utcnow
from backend.db.models import (
    STATUS_ACTIVE,
    Knowledge,
    KnowledgeRelation,
    ReviewSession,
)
from backend.domain.relation import (
    STATUS_ACCEPTED,
    STATUS_IGNORED,
    STATUS_PENDING,
    STATUS_REJECTED,
    can_review,
    canonical_pair,
    is_allowed_transition,
    is_valid_relation_type,
)
from backend.schemas.ai import RelationSuggestion
from backend.services.ai.prompts import PROMPT_VERSION, system_message
from backend.services.ai.provider import AiProvider, ChatMessage
from backend.services.knowledge_service import KnowledgeNotFound
from backend.services.review_service import require_live_knowledge
from backend.services.search_service import FTS_MIN_LENGTH, _fts_phrase, _like_pattern

logger = logging.getLogger(__name__)

# 候选召回的上限。多了审核队列看不完，反而变成堆积；
# 看完这一批，下次查询又会补上来（候选是实时算的，不存在"用完"）。
CANDIDATE_LIMIT = 20
# 单条知识一次最多召回几个候选搭档。没有它，一篇长理解能把
# 半个库都 MATCH 进来，队列全是同一篇的衍生。
PER_SEED_LIMIT = 5

# FTS 独立表（D-006：不会自动跟着数据变，这里只读）
_FTS_TABLE = "knowledge_fts"


class RelationNotFound(LookupError):
    """关系候选不存在。API 层映射成 404。"""


class InvalidRelationState(ValueError):
    """状态机不允许这次处置（比如对已经拒绝的行再点确认）。"""


class InvalidRelationType(ValueError):
    """关系类型不合法（空、带空白、超长）。"""


# ================================================================ 召回（免费）


@dataclass(frozen=True)
class CandidatePair:
    """一对待判断的候选。只有标题和领域，没有理解正文——
    队列只需要让人看出"这两条可能有关"，正文留给 AI 判断时按需读取（C5）。"""

    source_id: str
    target_id: str
    source_title: str
    target_title: str
    source_domain: str | None
    target_domain: str | None


def _match_windows(text: str, *, max_terms: int = 8) -> list[str]:
    """从一段中文文本取若干 3 字滑窗，做 FTS 的 OR 查询词。

    为什么不拿整段当短语查：trigram 的 MATCH 是**精确子串**匹配，
    「梯度下降的直觉」查不到「梯度下降的数学」——但它们显然相似。
    滑窗 OR 之后，任何共享 ≥3 连字的文本都能互相命中；
    共享 3 连字在中文里基本等于共享了一个词，这正是"相似"该有的粒度。
    窗口按步长采样封顶，避免长标题生成上百个 OR 词。
    """
    cleaned = "".join(text.split())
    size = 3
    if len(cleaned) < size:
        return []
    windows = [cleaned[i : i + size] for i in range(len(cleaned) - size + 1)]
    if len(windows) <= max_terms:
        return list(dict.fromkeys(windows))
    stride = -(-len(windows) // max_terms)
    sampled = windows[::stride][:max_terms]
    return list(dict.fromkeys(sampled))


def _matched_ids_for_title(session: Session, seed: Knowledge) -> set[str]:
    """按一条知识的标题（和当前理解的开头）找可能相关的知识 id。

    FTS5 为主；1~2 字标题 trigram 匹配不了，走 LIKE 扫标题。
    """
    terms = _match_windows(seed.title)
    version = seed.current_version
    if version is not None and version.user_understanding:
        # 理解开头的窗口补位：标题起得天差地别、内容其实相近的情况
        terms += _match_windows(version.user_understanding[:60], max_terms=4)
    terms = list(dict.fromkeys(terms))

    if not terms:
        return set()

    if len(seed.title.strip()) >= FTS_MIN_LENGTH:
        query = " OR ".join(_fts_phrase(term) for term in terms)
        matched = set(
            session.execute(
                text(
                    f"SELECT knowledge_id FROM {_FTS_TABLE} "
                    "WHERE title MATCH :q OR body MATCH :q"
                ),
                {"q": query},
            ).scalars()
        )
        # FTS 是独立表，可能滞后；active 过滤以权威表为准
        if matched:
            return set(
                session.scalars(
                    select(Knowledge.id)
                    .where(Knowledge.id.in_(matched))
                    .where(Knowledge.status == STATUS_ACTIVE)
                )
            )
        return set()

    # 1~2 字标题 FTS 匹配不了（trigram 最小 3 字符），走 LIKE 扫标题
    pattern = _like_pattern(seed.title.strip())
    return set(
        session.scalars(
            select(Knowledge.id)
            .where(Knowledge.title.like(pattern, escape="\\"))
            .where(Knowledge.status == STATUS_ACTIVE)
        )
    )


def _existing_pairs(session: Session) -> set[tuple[str, str]]:
    """既有关系的方向无关键集合。任何状态都算——rejected 保留的目的
    就是让这对别再被提出来。"""
    rows = session.execute(select(KnowledgeRelation.source_id, KnowledgeRelation.target_id)).all()
    return {canonical_pair(a, b) for a, b in rows}


def find_candidate_pairs(
    session: Session, *, limit: int = CANDIDATE_LIMIT
) -> list[CandidatePair]:
    """全库召回候选对。免费、实时、不落库、不调 AI。

    种子按最近更新排序：最近在学的知识，它的候选排前面。
    """
    seeds = session.scalars(
        select(Knowledge)
        .where(Knowledge.status == STATUS_ACTIVE)
        .order_by(Knowledge.updated_at.desc(), Knowledge.id)
    ).all()

    if len(seeds) < 2:
        return []

    existing = _existing_pairs(session)
    seen: set[tuple[str, str]] = set(existing)
    candidates: list[CandidatePair] = []

    for seed in seeds:
        if len(candidates) >= limit:
            break
        matched = _matched_ids_for_title(session, seed)
        matched.discard(seed.id)
        if not matched:
            continue
        partners = (
            session.scalars(
                select(Knowledge)
                .where(Knowledge.id.in_(matched))
                .order_by(Knowledge.updated_at.desc(), Knowledge.id)
                .limit(PER_SEED_LIMIT)
            ).all()
        )
        for partner in partners:
            pair = canonical_pair(seed.id, partner.id)
            if pair in seen:
                continue
            seen.add(pair)
            # 存储方向是有向的（语义由 AI 判断时定）；候选阶段只讲"这一对"
            candidates.append(
                CandidatePair(
                    source_id=pair[0],
                    target_id=pair[1],
                    source_title=seed.title if pair[0] == seed.id else partner.title,
                    target_title=partner.title if pair[0] == seed.id else seed.title,
                    source_domain=(
                        seed.domain if pair[0] == seed.id else partner.domain
                    ),
                    target_domain=(
                        partner.domain if pair[0] == seed.id else seed.domain
                    ),
                )
            )
            if len(candidates) >= limit:
                break

    return candidates


# ================================================================ AI 判断（花钱）


def _judge_request(first: Knowledge, second: Knowledge) -> list[ChatMessage]:
    """一对知识的判断请求。只发标题、领域和当前版本理解（C5）。"""
    def describe(k: Knowledge) -> str:
        version = k.current_version
        parts = [f"名称：{k.title}", f"领域：{k.domain or '（未填）'}"]
        if version is not None:
            parts.append(f"我的理解：{version.user_understanding}")
            if version.reasoning:
                parts.append(f"我的推理依据：{version.reasoning}")
        return "\n".join(parts)

    body = (
        "知识甲：\n" + describe(first) + "\n\n知识乙：\n" + describe(second)
    )
    return [system_message("relation_judge"), ChatMessage(role="user", content=body)]


@dataclass(frozen=True)
class JudgeFailure:
    """一次批量判断里失败的那几对。失败原因只到类别（error_kind），
    不带响应体（C6：日志与返回都不留原始输出）。"""

    source_id: str
    target_id: str
    error_kind: str
    message: str


@dataclass(frozen=True)
class JudgeReport:
    created: int
    skipped: int
    unrelated: int
    failed: list[JudgeFailure]


def judge_pairs(
    session: Session,
    provider: AiProvider,
    pairs: list[tuple[str, str]],
) -> JudgeReport:
    """让 AI 逐对判断候选，产出的候选落库 status='pending'。

    ⚠️ **每对一次请求**（前端点按钮前按对数明示花费，D-042）。
    单对失败（网络、校验、超时）记进报告、继续下一对——
    一批 10 对挂 1 对不该让另外 9 对白等。

    AI 说"不相关"（is_related=False）的记入 unrelated，不落行：
    没有关系的对不占用唯一约束的位置，下次召回还会出现——这是有意的，
    召回器以后改进了（比如换了关键词），同一对值得再判一次。

    方向由 AI 定（direction），relation_type 的语义相对 source → target 展开。
    """
    created = skipped = unrelated = 0
    failures: list[JudgeFailure] = []

    for source_id, target_id in pairs:
        first = session.get(Knowledge, source_id)
        second = session.get(Knowledge, target_id)
        if first is None or second is None:
            failures.append(
                JudgeFailure(source_id, target_id, "bad_request", "知识不存在，可能已删除")
            )
            continue

        try:
            result = provider.chat(_judge_request(first, second), RelationSuggestion)
        except Exception as exc:  # noqa: BLE001 —— 单对失败必须不阻断整批
            error_kind = getattr(exc, "error_kind", None) or "unavailable"
            message = getattr(exc, "user_message", None) or str(exc)
            failures.append(JudgeFailure(source_id, target_id, error_kind, message))
            logger.warning(
                "候选对 %s→%s 的 AI 判断失败：%s", source_id, target_id, error_kind
            )
            continue

        suggestion = result.value
        if not suggestion.is_related:
            unrelated += 1
            continue

        # direction 决定存储方向
        if suggestion.direction == "second":
            row_source, row_target = target_id, source_id
        else:
            row_source, row_target = source_id, target_id

        relation = KnowledgeRelation(
            source_id=row_source,
            target_id=row_target,
            relation_type=suggestion.relation_type,
            explanation=suggestion.explanation,
            evidence=suggestion.evidence,
            confidence=suggestion.confidence,
            status=STATUS_PENDING,
            model=provider.model_id,
            prompt_version=PROMPT_VERSION,
        )
        session.add(relation)
        try:
            session.commit()
            created += 1
        except IntegrityError:
            # 召回检查和这里之间理论上没有并发（单人本地），
            # 约束兜底只为不出悬空状态，回滚后继续下一对
            session.rollback()
            skipped += 1

    logger.info(
        "AI 关系判断完成：新建 %d，跳过 %d，不相关 %d，失败 %d",
        created, skipped, unrelated, len(failures),
    )
    return JudgeReport(created, skipped, unrelated, failures)


# ================================================================ 审核（用户）


def _require_relation(session: Session, relation_id: str) -> KnowledgeRelation:
    relation = session.get(KnowledgeRelation, relation_id)
    if relation is None:
        raise RelationNotFound(relation_id)
    return relation


def list_pending_relations(session: Session) -> list[KnowledgeRelation]:
    """待审核队列。置信度只用来排序（C7），高低都不影响能不能过。"""
    return list(
        session.scalars(
            select(KnowledgeRelation)
            .where(KnowledgeRelation.status == STATUS_PENDING)
            .order_by(KnowledgeRelation.confidence.desc(), KnowledgeRelation.created_at)
        )
    )


def decide_relation(
    session: Session, relation_id: str, action: str
) -> KnowledgeRelation:
    """用户处置一条候选：accepted / rejected / ignored。

    终态不可反悔（domain 状态机管着）：拒绝过的对哪怕再点确认，
    也得走召回重新提——而召回会因为它已存在而跳过，
    所以"拒绝"是持久的，这正是防骚扰的语义。
    """
    if action not in (STATUS_ACCEPTED, STATUS_REJECTED, STATUS_IGNORED):
        raise InvalidRelationState(f"未知处置：{action}")
    relation = _require_relation(session, relation_id)
    if not can_review(relation.status):
        raise InvalidRelationState(
            f"这条候选已经是 {relation.status}，不能再处置"
        )
    if not is_allowed_transition(relation.status, action):
        raise InvalidRelationState(f"不允许从 {relation.status} 变成 {action}")

    relation.status = action
    relation.reviewed_at = utcnow()
    session.commit()
    logger.info("关系候选 %s 已处置：%s", relation_id, action)
    return relation


def update_relation_type(
    session: Session, relation_id: str, relation_type: str
) -> KnowledgeRelation:
    """审核时改关系类型（工作流 E 的第四个处置动作）。

    只在 pending 时允许：类型是候选内容的一部分，定案了就别动；
    真想改已定案的，删库重来比绕状态机诚实。
    """
    cleaned = (relation_type or "").strip()
    if not cleaned or not is_valid_relation_type(cleaned):
        raise InvalidRelationType(f"关系类型不合法：{relation_type!r}")
    relation = _require_relation(session, relation_id)
    if not can_review(relation.status):
        raise InvalidRelationState("只有待审核的候选能改类型")
    relation.relation_type = cleaned
    session.commit()
    return relation


# ================================================================ 图谱与统计


def graph_data(session: Session) -> dict:
    """图谱页数据：全部 active 知识做节点，accepted 关系做边。

    **只显示 accepted**（工作流 E 硬要求 / 原则 4）。pending 的
    连一条虚线都不给——审核队列里看得到，图谱上不该有。
    """
    nodes = session.scalars(
        select(Knowledge)
        .where(Knowledge.status == STATUS_ACTIVE)
        .order_by(Knowledge.title)
    ).all()
    edges = session.scalars(
        select(KnowledgeRelation).where(
            KnowledgeRelation.status == STATUS_ACCEPTED
        )
    ).all()
    return {
        "nodes": [
            {"id": n.id, "title": n.title, "domain": n.domain} for n in nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source_id": e.source_id,
                "target_id": e.target_id,
                "relation_type": e.relation_type,
                "explanation": e.explanation,
            }
            for e in edges
        ],
    }


def relations_for_knowledge(
    session: Session, knowledge_id: str
) -> dict:
    """详情页「相关知识」：一条知识的 accepted 关系 + 它的免费候选。"""
    require_live_knowledge(session, knowledge_id)

    relations = session.scalars(
        select(KnowledgeRelation).where(
            (KnowledgeRelation.source_id == knowledge_id)
            | (KnowledgeRelation.target_id == knowledge_id),
            KnowledgeRelation.status == STATUS_ACCEPTED,
        )
    ).all()

    # 该知识的候选：召回一次，只留包含它的对
    candidates = [
        c for c in find_candidate_pairs(session, limit=CANDIDATE_LIMIT * 2)
        if knowledge_id in (c.source_id, c.target_id)
    ][:PER_SEED_LIMIT]

    def side(r: KnowledgeRelation) -> str:
        return "source" if r.source_id == knowledge_id else "target"

    def other_title(r: KnowledgeRelation) -> str:
        other = r.target if r.source_id == knowledge_id else r.source
        return other.title if other is not None else "（已删除）"

    return {
        "relations": [
            {
                "id": r.id,
                "direction": side(r),
                "relation_type": r.relation_type,
                "other_title": other_title(r),
                "other_id": (
                    r.target_id if r.source_id == knowledge_id else r.source_id
                ),
                "explanation": r.explanation,
            }
            for r in relations
        ],
        "candidates": candidates,
    }


def growth_stats(session: Session) -> dict:
    """图谱页的成长统计卡。全部一次查完，不逐项调接口。"""
    now = utcnow()
    week_ago = now - timedelta(days=7)

    total_knowledge = session.scalar(
        select(func.count(Knowledge.id)).where(Knowledge.status == STATUS_ACTIVE)
    ) or 0
    added_7d = session.scalar(
        select(func.count(Knowledge.id)).where(
            Knowledge.created_at >= week_ago,
            Knowledge.status == STATUS_ACTIVE,
        )
    ) or 0
    total_reviews = session.scalar(
        select(func.count(ReviewSession.id))
    ) or 0
    total_relations = session.scalar(
        select(func.count(KnowledgeRelation.id)).where(
            KnowledgeRelation.status == STATUS_ACCEPTED
        )
    ) or 0
    domains = dict(
        session.execute(
            select(Knowledge.domain, func.count(Knowledge.id))
            .where(Knowledge.status == STATUS_ACTIVE)
            .where(Knowledge.domain.is_not(None))
            .group_by(Knowledge.domain)
            .order_by(func.count(Knowledge.id).desc())
            .limit(10)
        ).all()
    )
    return {
        "total_knowledge": int(total_knowledge),
        "added_last_7_days": int(added_7d),
        "total_reviews": int(total_reviews),
        "total_relations": int(total_relations),
        "domains": {k: int(v) for k, v in domains.items()},
        "generated_at": now,
    }


__all__ = [
    "CANDIDATE_LIMIT",
    "CandidatePair",
    "InvalidRelationState",
    "InvalidRelationType",
    "JudgeFailure",
    "JudgeReport",
    "PER_SEED_LIMIT",
    "RelationNotFound",
    "decide_relation",
    "find_candidate_pairs",
    "graph_data",
    "growth_stats",
    "judge_pairs",
    "list_pending_relations",
    "relations_for_knowledge",
    "update_relation_type",
]
