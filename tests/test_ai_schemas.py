"""AI 输出 Schema 的校验行为，以及和提示词 / 数据库 / knowledge schema 的防漂移锁。

这里最有价值的一组测试是"三边对齐"：
提示词向 AI 承诺的字段名、Schema 实际校验的字段名、数据库列的词表，
三者必须一致。任何一边单独改了都是**静默故障**——
表现是"AI 老老实实照提示词返回，却永远过不了校验"，
而两边的代码看起来都完全正确。
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from backend.db.models import (
    REVIEW_CATEGORIES,
    SOURCE_KINDS,
    Knowledge,
    Source,
)
from backend.schemas.ai import (
    CorrectPoint,
    KnowledgeDraft,
    RatingSuggestion,
    NeedsVerificationItem,
    ReviewError,
    ReviewOmission,
    ReviewResult,
    SourceDraft,
)
from backend.schemas.knowledge import KnowledgeCreate, SourceCreate, SourceKind
from backend.db.models import RATINGS
from backend.services.ai.prompts import (
    EXTRACT_FIELDS,
    EXTRACT_SYSTEM_PROMPT,
    RATING_SUGGESTION_FIELDS,
    REVIEW_CATEGORIES_IN_PROMPT,
    REVIEW_ITEM_FIELDS_IN_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    SOURCE_ITEM_FIELDS_IN_PROMPT,
)


def _max_length(model: type, field: str) -> int | None:
    for item in model.model_fields[field].metadata:
        if hasattr(item, "max_length"):
            return item.max_length
    return None


def _review_item(category: str):
    return {
        "correct_points": CorrectPoint,
        "errors": ReviewError,
        "omissions": ReviewOmission,
        "needs_verification": NeedsVerificationItem,
    }[category]


# ================================================= 三边对齐：提示词 ↔ Schema


def test_draft_fields_exactly_match_the_prompt() -> None:
    """提示词承诺的字段名 ⇄ 草稿实际校验的字段名。"""
    assert set(KnowledgeDraft.model_fields) == set(EXTRACT_FIELDS)


def test_review_categories_match_prompt_and_database() -> None:
    """三方都要一致：提示词、ReviewResult 模型、ai_review_decisions.category 列词表。

    数据库那一侧尤其要紧——处置记录是按 (category, item_index) 定位的，
    类别名对不上就写不进那一列。
    """
    assert set(ReviewResult.model_fields) == set(REVIEW_CATEGORIES_IN_PROMPT)
    assert set(ReviewResult.model_fields) == set(REVIEW_CATEGORIES)


@pytest.mark.parametrize("category", tuple(REVIEW_ITEM_FIELDS_IN_PROMPT))
def test_review_item_subfields_match_the_prompt(category: str) -> None:
    model = _review_item(category)
    expected = REVIEW_ITEM_FIELDS_IN_PROMPT[category]
    assert set(model.model_fields) == set(expected)
    for name in expected:
        assert name in REVIEW_SYSTEM_PROMPT, f"提示词里没写出 {category}.{name}"


def test_source_draft_fields_match_the_knowledge_schema_exactly() -> None:
    """⚠️ 这是确认保存能不能直接喂进 KnowledgeCreate 的前提。

    字段名不一致的话，第⑥步就得写一层手工字段翻译；
    翻译漏一个字段是静默的——用户会看到建议的来源保存后少了一半信息。
    """
    assert set(SourceDraft.model_fields) == set(SourceCreate.model_fields)
    assert set(SourceDraft.model_fields) == set(SOURCE_ITEM_FIELDS_IN_PROMPT)


def test_prompt_spells_out_the_source_item_fields() -> None:
    for name in SOURCE_ITEM_FIELDS_IN_PROMPT:
        assert name in EXTRACT_SYSTEM_PROMPT


def test_source_kind_vocabulary_is_shared_not_rewritten() -> None:
    """草稿直接用 `schemas.knowledge.SourceKind`，不自己再抄一份 Literal。

    两份 Literal 会漂移；一份常量 + 一条断言才不会。
    """
    assert set(get_args(SourceKind)) == set(SOURCE_KINDS)
    # SourceDraft 的默认值也必须在词表里
    assert SourceDraft.model_fields["source_type"].default in SOURCE_KINDS


def test_ai_supplements_is_a_list_not_a_paragraph() -> None:
    """必须是数组：用户要逐条决定采纳哪条，写成一段就没法单独拒绝。"""
    assert "ai_supplements" in EXTRACT_FIELDS
    with pytest.raises(ValidationError):
        KnowledgeDraft.model_validate(
            {"user_understanding": "我的理解", "ai_supplements": "一整段补充说明"}
        )


def test_prompt_says_ai_supplements_is_an_array() -> None:
    assert "ai_supplements` 是**数组**" in EXTRACT_SYSTEM_PROMPT


# =============================================== 长度上限 ↔ 数据库列宽


def test_draft_text_limits_match_database_columns() -> None:
    """和 test_schemas.py 同一套规矩：上限超过列宽，
    超长内容会绕过校验、走到 SQLite 才炸，用户看到的是 500 而不是清晰的报错。"""
    assert _max_length(KnowledgeDraft, "title") == Knowledge.__table__.c.title.type.length
    assert _max_length(KnowledgeDraft, "domain") == Knowledge.__table__.c.domain.type.length
    assert _max_length(SourceDraft, "title") == Source.__table__.c.title.type.length
    assert _max_length(SourceDraft, "url_or_reference") == Source.__table__.c.url_or_reference.type.length


def test_draft_title_limit_equals_knowledge_create_limit() -> None:
    assert _max_length(KnowledgeDraft, "title") == _max_length(KnowledgeCreate, "title")


# ================================================ 空值：草稿松、落库紧


def test_draft_allows_blank_understanding_on_purpose() -> None:
    """草稿层面允许空理解，**故意**和 KnowledgeCreate 不一样。

    如果草稿也 min_length=1，AI 为了过校验就会被逼着编一段理解出来——
    那段编出来的话之后会被当作用户自己的理解保存。这是原则 1 最不能接受的。
    """
    draft = KnowledgeDraft.model_validate({"title": "过拟合", "user_understanding": ""})
    assert draft.user_understanding == ""
    assert draft.has_understanding is False

    # 同一份内容走落库校验必须被拒绝——一道都没松
    with pytest.raises(ValidationError):
        KnowledgeCreate.model_validate({"title": "过拟合", "user_understanding": ""})


def test_whitespace_only_understanding_counts_as_blank() -> None:
    draft = KnowledgeDraft.model_validate({"user_understanding": "   \n  "})
    assert draft.user_understanding == ""
    assert draft.has_understanding is False


def test_prompt_tells_the_ai_empty_is_an_okay_answer() -> None:
    """Schema 允许空，提示词也必须明说空是正常结果，
    否则模型会认为"必须给点什么"而开始编。"""
    assert "空字符串" in EXTRACT_SYSTEM_PROMPT
    assert "不算失败" in EXTRACT_SYSTEM_PROMPT


# ================================================== AI 输出的宽容度与严格度


def test_extra_fields_are_ignored_not_rejected() -> None:
    """对 AI 用 extra=ignore：模型多返回一个字段，重试也修不好，
    不该因此让整条工作流对这家服务商永久不可用。"""
    result = ReviewResult.model_validate(
        {
            "correct_points": [{"point": "甲", "why": "乙"}],
            "overall_score": 88,
            "confidence": "high",
        }
    )
    assert len(result.correct_points) == 1
    assert not hasattr(result, "overall_score")


def test_model_level_stripping_reaches_into_lists() -> None:
    """⚠️ schemas/ai.py 里那句注释的依据：清理空白只声明了一处（模型级配置），
    但它必须对**嵌在 list 里的 str** 也生效，否则 `min_length=1`
    会被 "   " 这种纯空格的空条目混过去。"""
    with pytest.raises(ValidationError):
        ReviewResult.model_validate({"follow_up_questions": ["   "]})
    with pytest.raises(ValidationError):
        ReviewResult.model_validate({"correct_points": [{"point": "   ", "why": "  \n "}]})

    # 250 个空格：按"先计数后清理"算的话长度合规（<300）、条数合规、非空合规，
    # 会一路通过；只有"先清理后校验"才会拒掉它。这条断言区分得出两种顺序。
    with pytest.raises(ValidationError):
        ReviewResult.model_validate({"follow_up_questions": [" " * 250]})


def test_human_facing_schema_still_forbids_extras() -> None:
    """对照：前端拼错字段名必须当场报错，不能被静默吞掉。"""
    with pytest.raises(ValidationError):
        KnowledgeCreate.model_validate(
            {"titel": "拼错了", "user_understanding": "我的理解"}
        )


@pytest.mark.parametrize("blank_field", ("point", "why"))
def test_review_items_reject_blank_text(blank_field: str) -> None:
    """只剩空白的条目等于没有内容，不能算一条审查意见。"""
    payload = {"point": "抓住了关键", "why": "说清了为什么"}
    payload[blank_field] = "   \n  "
    with pytest.raises(ValidationError):
        CorrectPoint.model_validate(payload)


def test_review_error_requires_all_three_parts() -> None:
    """evidence 不能省：没有依据的"你错了"就是武断，用户没法判断该不该采纳。"""
    with pytest.raises(ValidationError):
        ReviewError.model_validate({"claim": "梯度下降会收敛到全局最优", "correction": "未必"})


def test_needs_verification_requires_how_to_verify() -> None:
    """只说"这里不确定"而不告诉他怎么查，等于把活儿推回给用户。"""
    with pytest.raises(ValidationError):
        NeedsVerificationItem.model_validate({"item": "我不确定这个结论适用范围"})


def test_all_five_categories_may_be_omitted_or_empty() -> None:
    """漏写一个**空**数组不该让整份审查作废；全空也是合法结果。"""
    result = ReviewResult.model_validate({})
    assert result.is_empty is True
    assert all(
        getattr(result, category) == [] for category in REVIEW_CATEGORIES_IN_PROMPT
    )


def test_empty_review_is_not_the_same_as_all_correct() -> None:
    """前端要用这个区别说话：「AI 没提意见」不等于「AI 说你全对」。"""
    only_praise = ReviewResult.model_validate(
        {"correct_points": [{"point": "抓住了关键", "why": "说清了为什么"}]}
    )
    assert only_praise.is_empty is False
    assert only_praise.errors == []


def test_category_arrays_are_capped() -> None:
    """一个类别超过 20 条说明 AI 在倾倒而不是挑重点，前端也渲染不动。"""
    items = [{"point": f"第 {i} 条", "why": "理由"} for i in range(21)]
    with pytest.raises(ValidationError):
        ReviewResult.model_validate({"correct_points": items})


def test_single_field_length_is_enforced() -> None:
    with pytest.raises(ValidationError):
        ReviewError.model_validate(
            {"claim": "甲", "correction": "乙", "evidence": "长" * 601}
        )


def test_follow_up_questions_are_one_liners() -> None:
    """追问给 1~2 个就够，也不该写成段落。"""
    with pytest.raises(ValidationError):
        ReviewResult.model_validate({"follow_up_questions": ["问" * 301]})


def test_illegal_source_kind_is_rejected() -> None:
    """枚举值非法属于结构违规（C3），整份草稿作废重来——
    它说明 AI 没照契约输出，那别的字段也不能信。"""
    with pytest.raises(ValidationError):
        SourceDraft.model_validate({"source_type": "youtube", "title": "某个视频"})


def test_source_without_note_is_allowed_at_draft_stage() -> None:
    """D-029 那条「选 other 必填备注」在 SourceCreate 上，不在草稿上。

    草稿阶段拒绝的话，用户只能整份重来；
    而他的实际修复动作只是补一句话——那句话应该由他写，不是由 AI 猜。
    """
    draft = SourceDraft.model_validate({"source_type": "other", "title": "一次讨论"})
    assert draft.note is None
    with pytest.raises(ValidationError):
        SourceCreate.model_validate({"source_type": "other", "title": "一次讨论"})


# ================================================== 序列化：item_index 的前提


def test_result_round_trips_and_keeps_array_order() -> None:
    """⚠️ `ai_review_decisions` 用 (category, item_index) 定位条目，
    而 ai_reviews.result_json 存的是**模型的转储**而不是 AI 原文。
    所以转储再读回来必须顺序不变——变一位，用户之前标的"不同意"
    就会指到另一条上去。"""
    original = ReviewResult.model_validate(
        {
            "errors": [
                {"claim": "第一条", "correction": "改一", "evidence": "据一"},
                {"claim": "第二条", "correction": "改二", "evidence": "据二"},
                {"claim": "第三条", "correction": "改三", "evidence": "据三"},
            ],
            "omissions": [
                {"missing": "甲", "why_it_matters": "乙"},
                {"missing": "丙", "why_it_matters": "丁"},
            ],
        }
    )
    dumped = original.model_dump_json()
    reloaded = ReviewResult.model_validate_json(dumped)

    assert [e.claim for e in reloaded.errors] == ["第一条", "第二条", "第三条"]
    assert [o.missing for o in reloaded.omissions] == ["甲", "丙"]
    assert reloaded.model_dump() == original.model_dump()


# ================================================== 草稿 → 落库的映射


# 草稿确认后要喂给 KnowledgeCreate。三个字段不对应，是**有意**的：
DRAFT_TO_CREATE_RENAMED = {"suggested_sources": "sources"}
DRAFT_TO_CREATE_DROPPED = frozenset({"ai_supplements"})
DRAFT_TO_CREATE_DIRECT = frozenset(
    {"title", "domain", "summary", "user_understanding", "reasoning", "open_questions"}
)


def to_create_payload(draft: KnowledgeDraft) -> dict:
    """草稿 → KnowledgeCreate 的转接。第⑥步必须用同样的映射。"""
    payload = draft.model_dump(
        exclude=set(DRAFT_TO_CREATE_DROPPED) | set(DRAFT_TO_CREATE_RENAMED)
    )
    payload["sources"] = draft.model_dump()["suggested_sources"]
    payload["source_type"] = "conversation"
    return payload


def test_the_mapping_covers_every_draft_field() -> None:
    """每个草稿字段都必须有归属：直通、改名、或明确丢弃。

    这条断言防的是"草稿将来加一个字段，转接函数忘了处理"——
    那种漏法是静默的，用户会看到建议的信息保存后不见了。
    """
    accounted = DRAFT_TO_CREATE_DIRECT | set(DRAFT_TO_CREATE_RENAMED) | DRAFT_TO_CREATE_DROPPED
    assert accounted == set(EXTRACT_FIELDS), accounted ^ set(EXTRACT_FIELDS)


def test_ai_supplements_is_the_only_dropped_field_and_has_a_home() -> None:
    """`ai_supplements` 不落库不是遗漏，是设计：
    它没有对应的列，因为用户采纳某条补充之后，那句话要**由他打字写进**
    自己的理解里，而不是由程序拼接进去。"""
    assert DRAFT_TO_CREATE_DROPPED == {"ai_supplements"}
    assert "ai_supplements" not in KnowledgeCreate.model_fields


def test_draft_dump_can_feed_knowledge_create() -> None:
    """确认保存的通路。字段名或类型不兼容会在第⑥步变成运行时炸点，这里提前锁住。"""
    draft = KnowledgeDraft.model_validate(
        {
            "title": "梯度下降",
            "domain": "机器学习",
            "summary": "沿负梯度方向更新参数",
            "user_understanding": "每步往下降最快的方向走一小步",
            "ai_supplements": ["学习率太大会跳过最低点"],
            "reasoning": "因为梯度指向增长最快方向",
            "open_questions": "为什么非凸问题也管用？",
            "suggested_sources": [
                {
                    "source_type": "course",
                    "title": "统计学习方法",
                    "url_or_reference": None,
                    "note": "第三章",
                }
            ],
        }
    )

    created = KnowledgeCreate.model_validate(to_create_payload(draft))

    assert created.title == "梯度下降"
    assert created.user_understanding == "每步往下降最快的方向走一小步"
    assert created.source_type == "conversation"
    assert created.sources[0].source_type == "course"
    assert created.sources[0].note == "第三章"


def test_ai_supplements_do_not_enter_the_saved_record_by_themselves() -> None:
    """⚠️ 盯着第⑥步一个具体的实现陷阱：如果把 ai_supplements 拼到
    user_understanding 后面再保存，原则 1 当场失效，而功能上看起来一切正常。"""
    draft = KnowledgeDraft.model_validate(
        {
            "title": "甲",
            "user_understanding": "我自己说的话",
            "ai_supplements": ["AI 补的一句"],
        }
    )
    created = KnowledgeCreate.model_validate(to_create_payload(draft))
    assert created.user_understanding == "我自己说的话"
    assert "AI 补的一句" not in created.user_understanding


# ================================================= 对话式复习（阶段 3 ⑤）


def test_rating_suggestion_fields_exactly_match_the_prompt() -> None:
    assert set(RatingSuggestion.model_fields) == set(RATING_SUGGESTION_FIELDS)


def test_rating_suggestion_vocabulary_is_the_database_vocabulary() -> None:
    """suggested_rating 复用 review_api 的 ReviewRating Literal（单一来源），
    词表和 review_sessions.rating 的常量必须一致。"""
    assert set(
        RatingSuggestion.model_fields["suggested_rating"].annotation.__args__
    ) == set(RATINGS)
