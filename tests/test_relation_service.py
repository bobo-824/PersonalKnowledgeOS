"""relation_service 的验收测试（阶段 4，工作流 E）。

盯的是工作流 E 的硬要求在数据层的落点：
- 正式图谱只显示 accepted；pending / rejected 都不出现（原则 4）
- rejected / ignored 永久保留，且召回不再提出同一对（防重复骚扰）
- 没有解释和依据的候选在 C3 层就被拒（RelationSuggestion），到不了这里
- confidence 只用于排序（C7），accept 永远是用户点了"确认"
- AI 判断逐对、失败不阻断；召回免费不调 AI（D-064 / D-066）
- 状态机终态不可反悔（domain/relation.py 管着，这里验证服务层不绕过）
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import KnowledgeRelation
from backend.schemas.ai import RelationSuggestion
from backend.schemas.knowledge import KnowledgeCreate
from backend.services.ai.errors import AiTimeout
from backend.services.ai.fake import FakeProvider
from backend.services.knowledge_service import archive_knowledge, create_knowledge
from backend.services.relation_service import (
    InvalidRelationState,
    InvalidRelationType,
    RelationNotFound,
    decide_relation,
    find_candidate_pairs,
    graph_data,
    growth_stats,
    judge_pairs,
    list_pending_relations,
    relations_for_knowledge,
    update_relation_type,
)


def make_knowledge(db_session: Session, title: str, understanding: str) -> str:
    return create_knowledge(
        db_session,
        KnowledgeCreate(
            title=title,
            user_understanding=understanding,
            domain="机器学习",
        ),
    ).id


def suggestion(**overrides: object) -> RelationSuggestion:
    payload = {
        "relation_type": "prerequisite",
        "explanation": "线性代数是理解梯度下降里矩阵运算的前提",
        "evidence": "梯度下降的理解里提到矩阵运算；线性代数的理解正是讲这个",
        "confidence": 0.8,
    }
    payload.update(overrides)
    return RelationSuggestion.model_validate(payload)


# ================================================================ 召回（免费）


def test_recall_finds_overlapping_pair_without_any_ai(db_session: Session) -> None:
    """召回是纯本地检索：不用排任何 FakeProvider 结果就该有候选。"""
    first = make_knowledge(db_session, "梯度下降的直觉", "沿最陡方向下山")
    second = make_knowledge(db_session, "梯度下降的数学", "对梯度下降做数学推导")
    pairs = find_candidate_pairs(db_session)
    pair_ids = {(p.source_id, p.target_id) for p in pairs}
    assert (first, second) in pair_ids or (second, first) in pair_ids


def test_recall_matches_via_understanding_head_windows(db_session: Session) -> None:
    """标题完全不同的两条，靠理解开头的滑窗也能召回。

    理解开头的窗口采样封顶（max_terms=4）：匹配词必须在开头的
    采样里活下来——这锁住的是"窗口从头部取"这个实现选择。
    """
    make_knowledge(
        db_session,
        "薛定谔的猫",
        "傅里叶变换是理解频谱的关键工具，后续复习时要把新旧笔记对照起来看，"
        "看看哪里想岔了，再把自己新的体会补在原有段落的后面，慢慢把整块知识焐热。",
    )
    make_knowledge(
        db_session,
        "频谱分析入门",
        "傅里叶变换把波形拆成一堆正弦波再叠加回去",
    )
    pairs = find_candidate_pairs(db_session)
    assert len(pairs) >= 1


def test_recall_excludes_existing_pairs_in_any_status(db_session: Session) -> None:
    """rejected 也算"已经审过"：防重复骚扰靠的就是这条。"""
    first = make_knowledge(db_session, "梯度下降的直觉", "沿最陡方向下山")
    second = make_knowledge(db_session, "梯度下降的数学", "数学推导")
    provider = FakeProvider()
    provider.queue(suggestion())
    report = judge_pairs(db_session, provider, [(first, second)])
    assert report.created == 1
    relation = db_session.scalars(select(KnowledgeRelation)).one()
    decide_relation(db_session, relation.id, "rejected")

    assert find_candidate_pairs(db_session) == []
    assert provider.remaining() == 0


def test_recall_excludes_archived_and_self(db_session: Session) -> None:
    make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    other = make_knowledge(db_session, "梯度下降的数学", "数学推导")
    archive_knowledge(db_session, other)
    # 归档的不出现在候选里；库里只剩一条 active 时也没有"对"可言
    assert find_candidate_pairs(db_session) == []


def test_recall_works_for_two_character_titles_via_like(db_session: Session) -> None:
    """FTS trigram 最小 3 字符：2 字标题走 LIKE 路径，也要能召回。"""
    first = make_knowledge(db_session, "熵", "不确定性的度量")
    second = make_knowledge(db_session, "熵与信息量", "熵在信息论里的含义")
    pairs = find_candidate_pairs(db_session)
    pair_ids = {(p.source_id, p.target_id) for p in pairs}
    assert (first, second) in pair_ids or (second, first) in pair_ids


def test_like_branch_also_excludes_archived(db_session: Session) -> None:
    """LIKE 路径和 FTS 路径一样只认 active：归档的不许混进候选。"""
    seed = make_knowledge(db_session, "熵", "不确定性的度量")
    archived = make_knowledge(db_session, "熵与信息量", "熵在信息论里的含义")
    live = make_knowledge(db_session, "熵增原理", "孤立系统熵不减")
    archive_knowledge(db_session, archived)
    pairs = find_candidate_pairs(db_session)
    assert len(pairs) == 1
    assert {pairs[0].source_id, pairs[0].target_id} == {seed, live}


# ================================================================ AI 判断（花钱）


def test_judge_creates_pending_rows_with_provenance(db_session: Session) -> None:
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion())
    report = judge_pairs(db_session, provider, [(first, second)])

    assert report.created == 1
    assert report.failed == []
    relation = db_session.scalars(select(KnowledgeRelation)).one()
    assert relation.status == "pending"
    assert relation.model == "fake-model"
    assert relation.prompt_version is not None
    assert relation.source_id == first and relation.target_id == second
    assert provider.remaining() == 0


def test_judge_respects_ai_direction(db_session: Session) -> None:
    """direction=second 表示乙是 source：存储方向必须翻转，语义不能倒。"""
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion(direction="second"))
    judge_pairs(db_session, provider, [(first, second)])
    relation = db_session.scalars(select(KnowledgeRelation)).one()
    assert relation.source_id == second and relation.target_id == first


def test_judge_skips_unrelated_and_never_stores_them(db_session: Session) -> None:
    """AI 说"不相关"是正常结论：不落行，也不算失败。
    不落行意味着下次召回同一对还会出现——召回器以后改进了，
    同一对值得再判一次，这和 rejected 的"永久安静"是有意不同。"""
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion(is_related=False, explanation="", evidence=""))
    report = judge_pairs(db_session, provider, [(first, second)])

    assert report.unrelated == 1
    assert report.created == 0
    assert report.failed == []
    assert db_session.scalars(select(KnowledgeRelation)).all() == []


def test_judge_failure_does_not_block_the_rest(db_session: Session) -> None:
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    third = make_knowledge(db_session, "反向传播", "链式法则逐层回传")
    provider = FakeProvider()
    provider.queue(AiTimeout("超时"))  # 第一对挂
    provider.queue(suggestion())  # 第二对成
    report = judge_pairs(db_session, provider, [(first, second), (first, third)])

    assert report.created == 1
    assert len(report.failed) == 1
    assert report.failed[0].error_kind == "timeout"
    relations = db_session.scalars(select(KnowledgeRelation)).all()
    assert len(relations) == 1
    assert relations[0].target_id == third


def test_judge_c5_sends_only_the_pair(db_session: Session) -> None:
    """C5：判断一对只发那一对的标题和当前理解，其他知识一个字都不发。"""
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    make_knowledge(db_session, "不该出现的知识", "这段话绝不能发给 AI")
    provider = FakeProvider()
    provider.queue(suggestion())
    judge_pairs(db_session, provider, [(first, second)])

    sent = "".join(m.content for m in provider.chat_calls[0])
    assert "线性代数基础" in sent and "梯度下降" in sent
    assert "不该出现的知识" not in sent


def test_judge_unique_constraint_backstop_skips_duplicates(db_session: Session) -> None:
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion())
    provider.queue(suggestion())  # 同一对再判一次：数据库唯一约束兜底
    report = judge_pairs(db_session, provider, [(first, second), (first, second)])
    assert report.created == 1
    assert report.skipped == 1


# ================================================================ 审核（用户）


def test_decide_accept_moves_to_graph_and_stamps_reviewed_at(db_session: Session) -> None:
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion())
    judge_pairs(db_session, provider, [(first, second)])
    relation = list_pending_relations(db_session)[0]

    accepted = decide_relation(db_session, relation.id, "accepted")
    assert accepted.status == "accepted"
    assert accepted.reviewed_at is not None

    graph = graph_data(db_session)
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["relation_type"] == "prerequisite"


def test_graph_shows_only_accepted(db_session: Session) -> None:
    """原则 4 / 工作流 E：pending 和 rejected 都不许出现在图谱里。"""
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    third = make_knowledge(db_session, "反向传播", "链式法则逐层回传")

    provider = FakeProvider()
    provider.queue(suggestion())  # → accepted
    provider.queue(suggestion())  # → 保持 pending
    judge_pairs(db_session, provider, [(first, second), (first, third)])
    pending = list_pending_relations(db_session)
    decide_relation(db_session, pending[0].id, "accepted")

    assert len(list_pending_relations(db_session)) == 1
    graph = graph_data(db_session)
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["source_id"] == first
    assert graph["edges"][0]["target_id"] == second


def test_terminal_states_cannot_be_re_decided(db_session: Session) -> None:
    """C7 的另一面：确认过的就是确认过的，程序不能替用户反悔。"""
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion())
    judge_pairs(db_session, provider, [(first, second)])
    relation = list_pending_relations(db_session)[0]

    decide_relation(db_session, relation.id, "rejected")
    with pytest.raises(InvalidRelationState):
        decide_relation(db_session, relation.id, "accepted")
    with pytest.raises(InvalidRelationState):
        decide_relation(db_session, relation.id, "ignored")


def test_update_relation_type_only_while_pending(db_session: Session) -> None:
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion())
    judge_pairs(db_session, provider, [(first, second)])
    relation = list_pending_relations(db_session)[0]

    updated = update_relation_type(db_session, relation.id, "deeper")
    assert updated.relation_type == "deeper"

    decide_relation(db_session, relation.id, "accepted")
    with pytest.raises(InvalidRelationState):
        update_relation_type(db_session, relation.id, "contrast")

    with pytest.raises(InvalidRelationType):
        update_relation_type(db_session, relation.id, "带 空格")


def test_decide_unknown_relation_raises_not_found(db_session: Session) -> None:
    with pytest.raises(RelationNotFound):
        decide_relation(db_session, "no-such-id", "accepted")


# ================================================================ 图谱与统计


def test_growth_stats_counts_the_right_things(db_session: Session) -> None:
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion())
    judge_pairs(db_session, provider, [(first, second)])
    relation = list_pending_relations(db_session)[0]
    decide_relation(db_session, relation.id, "accepted")

    stats = growth_stats(db_session)
    assert stats["total_knowledge"] == 2
    assert stats["added_last_7_days"] == 2
    assert stats["total_reviews"] == 0
    assert stats["total_relations"] == 1
    assert stats["domains"] == {"机器学习": 2}


def test_relations_for_knowledge_returns_both_sides(db_session: Session) -> None:
    first = make_knowledge(db_session, "线性代数基础", "矩阵与向量")
    second = make_knowledge(db_session, "梯度下降", "沿最陡方向下山")
    provider = FakeProvider()
    provider.queue(suggestion(direction="second"))
    judge_pairs(db_session, provider, [(first, second)])
    relation = list_pending_relations(db_session)[0]
    decide_relation(db_session, relation.id, "accepted")

    for_knowledge = relations_for_knowledge(db_session, first)
    assert for_knowledge["relations"][0]["other_id"] == second
    assert for_knowledge["relations"][0]["direction"] == "target"
