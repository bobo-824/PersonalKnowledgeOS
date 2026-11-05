"""系统提示词的可验证性。

提示词是这个产品质量的实际杠杆，但它的效果没法在这里测——
那些要等真实 key 接上之后由用户来判断。
这里测的是**结构与承诺**：字段名齐不齐、原则有没有写进去、
和数据库词表对没对上。这些一旦漂移就是静默故障，所以必须有测试。
"""

from __future__ import annotations

import re

import pytest

from backend.db.models import RATINGS, REVIEW_CATEGORIES, SOURCE_KINDS
from backend.domain.relation import CORE_RELATION_TYPES
from backend.domain.versioning import UNDERSTANDING_FIELDS
from backend.services.ai.prompts import (
    EXTRACT_FIELDS,
    EXTRACT_SYSTEM_PROMPT,
    PROMPT_VERSION,
    PROMPTS,
    RATING_SUGGESTION_FIELDS,
    RATING_VALUES_IN_PROMPT,
    RELATION_DIRECTIONS_IN_PROMPT,
    RELATION_JUDGE_SYSTEM_PROMPT,
    RELATION_SUGGESTION_FIELDS,
    RELATION_TYPES_IN_PROMPT,
    REVIEW_CATEGORIES_IN_PROMPT,
    REVIEW_CHAT_SYSTEM_PROMPT,
    REVIEW_RATE_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    STUDY_CHAT_SYSTEM_PROMPT,
    system_message,
)


def test_six_prompts_are_present_and_distinct() -> None:
    assert set(PROMPTS) == {
        "study_chat",
        "extract",
        "review",
        "review_chat",
        "review_rate",
        "relation_judge",
    }
    texts = list(PROMPTS.values())
    assert all(text.strip() for text in texts)
    assert len(set(texts)) == len(PROMPTS), "有两个工作流用了同一份提示词"


def test_system_message_wraps_the_right_text() -> None:
    message = system_message("review")
    assert message.role == "system"
    assert message.content == REVIEW_SYSTEM_PROMPT


def test_unknown_prompt_name_says_so() -> None:
    """写错提示词名要在构造时就炸，而不是发出去一段没有系统提示的请求。"""
    with pytest.raises(ValueError, match="未知的提示词名"):
        system_message("revieww")


def test_prompt_version_is_a_real_label() -> None:
    """落进 ai_reviews.prompt_version（NOT NULL，String(16)），格式要短而可排错。"""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+", PROMPT_VERSION)
    assert len(PROMPT_VERSION) <= 16


def test_json_examples_have_balanced_braces() -> None:
    """提示词里那段 JSON 形状说明要是括号都不配对，
    说明示例本身写坏了——AI 照着一个坏例子输出，校验永远过不了。"""
    for name in ("extract", "review"):
        text = PROMPTS[name]
        assert text.count("{") == text.count("}"), f"{name} 的 JSON 例子括号不平衡"
        assert text.count("[") == text.count("]"), f"{name} 的 JSON 例子方括号不平衡"


# ------------------------------------------------ 字段名：提示词 ↔ 数据模型


@pytest.mark.parametrize("field", EXTRACT_FIELDS)
def test_extract_prompt_names_every_expected_field(field: str) -> None:
    assert field in EXTRACT_SYSTEM_PROMPT


def test_extract_covers_the_fields_versioning_reads() -> None:
    """⚠️ 这是一条**跨层**不变量，不是自证。

    `domain/versioning.py` 的 UNDERSTANDING_FIELDS 决定"要不要建新版本"（D-030）。
    从对话提取的知识只有 v1，将来用户改自己的理解时要能正常触发新版本；
    如果草稿压根没有 `reasoning` 或 `open_questions`，
    那两个字段就永远改不动，"我的疑问变了也算认知演进"这半条原则当场失效。

    上面的 parametrize 断言用的是 EXTRACT_FIELDS 自己，改它就等于同时改了测试，
    所以这条以 versioning 那边的常量为准——漂移会被这里抓到。
    """
    for field in UNDERSTANDING_FIELDS:
        assert field in EXTRACT_FIELDS, f"草稿字段漏了 {field}"
        assert field in EXTRACT_SYSTEM_PROMPT


def test_extract_prompt_separates_user_words_from_ai_additions() -> None:
    """user_understanding 之外必须还有一个装 AI 补充的字段，两者不能合并。"""
    assert "ai_supplements" in EXTRACT_FIELDS
    assert set(EXTRACT_FIELDS) - set(UNDERSTANDING_FIELDS) - {
        "title",
        "domain",
        "summary",
        "suggested_sources",
        "ai_supplements",
    } == set(), "草稿里冒出了预期之外的字段"


def test_extract_prompt_lists_all_eight_source_kinds() -> None:
    """来源类型是固定八种（D-029）。提示词漏一种，AI 就会造出一个非法值，
    然后每条带那种来源的提取都校验失败。"""
    for kind in SOURCE_KINDS:
        assert kind in EXTRACT_SYSTEM_PROMPT, f"提示词里没列出来源类型 {kind}"


@pytest.mark.parametrize("category", REVIEW_CATEGORIES_IN_PROMPT)
def test_review_prompt_names_every_category(category: str) -> None:
    assert category in REVIEW_SYSTEM_PROMPT


def test_review_prompt_categories_match_the_database_vocabulary() -> None:
    """提示词里的五个类别，必须和 ai_review_decisions.category 列的词表一致。

    不一致的后果很具体：处置记录写不进那一列，
    或者用户在前端看到的类别和后端存的对不上。
    """
    assert set(REVIEW_CATEGORIES_IN_PROMPT) == set(REVIEW_CATEGORIES)
    assert len(REVIEW_CATEGORIES_IN_PROMPT) == len(REVIEW_CATEGORIES)


# -------------------------------------------------- 原则有没有真的写进提示词


def test_chat_prompt_forbids_handing_over_a_copy_paste_answer() -> None:
    """原则 1 的落点：不能让 AI 写好一段让用户存成"自己的理解"。"""
    assert "复制粘贴" in STUDY_CHAT_SYSTEM_PROMPT
    assert "先问他自己现在怎么理解" in STUDY_CHAT_SYSTEM_PROMPT


def test_chat_prompt_requires_separating_user_words_from_ai_additions() -> None:
    """为工作流 B 铺垫：不先做区分，提取时就分不出来源。"""
    assert "我补一句" in STUDY_CHAT_SYSTEM_PROMPT
    assert "分开保存" in STUDY_CHAT_SYSTEM_PROMPT


def test_chat_prompt_keeps_it_to_one_question_at_a_time() -> None:
    """连着抛三个问题会让人直接放弃回答，原则 1 就形同虚设。"""
    assert "一次只问一个小问题" in STUDY_CHAT_SYSTEM_PROMPT


def test_chat_prompt_scores_mastery_in_every_reply() -> None:
    """用户要的是"看得见自己到哪儿了"：每轮回答末尾必须有十分制打分。"""
    assert "掌握度：" in STUDY_CHAT_SYSTEM_PROMPT
    assert "/10" in STUDY_CHAT_SYSTEM_PROMPT
    assert "每次回答的**最后一行**" in STUDY_CHAT_SYSTEM_PROMPT


def test_chat_prompt_stops_asking_once_mastery_passes_eight() -> None:
    """⚠️ 反"无休止问答"的那一条：超过 8 分必须停止提问、把选择权交回用户。

    没有这条，前面的"先问他自己怎么理解"会让对话永远绕不完——
    用户已经讲通了还在被追问，这是本次修改的直接动因。
    """
    assert "超过 8 分" in STUDY_CHAT_SYSTEM_PROMPT
    assert "不要再抛新问题" in STUDY_CHAT_SYSTEM_PROMPT
    assert "把选择权交回给他" in STUDY_CHAT_SYSTEM_PROMPT


def test_chat_prompt_scoring_is_not_flattery() -> None:
    """打分必须能区分，否则等于没有：不许为了让他高兴而打高分。"""
    assert "不要为了让他高兴而打高分" in STUDY_CHAT_SYSTEM_PROMPT
    assert "分数要真的能区分" in STUDY_CHAT_SYSTEM_PROMPT


def test_extract_prompt_keeps_the_two_provenance_fields_apart() -> None:
    """C1 的数据前提：混在一起就再也分不清哪部分是谁的。"""
    assert "必须严格分开" in EXTRACT_SYSTEM_PROMPT
    assert "user_understanding" in EXTRACT_SYSTEM_PROMPT
    assert "ai_supplements" in EXTRACT_SYSTEM_PROMPT


def test_extract_prompt_allows_empty_instead_of_confabulation() -> None:
    """宁可空着也不许脑补——脑补出来的理解会被当作用户的东西永久保存。"""
    assert "不要脑补" in EXTRACT_SYSTEM_PROMPT
    assert "空字符串" in EXTRACT_SYSTEM_PROMPT
    assert "不许编造" in EXTRACT_SYSTEM_PROMPT


def test_extract_prompt_says_the_ai_does_not_save() -> None:
    """C2：AI 不能直接创建知识。提示词里不能给它"写入"的暗示。"""
    assert "你不负责保存" in EXTRACT_SYSTEM_PROMPT


def test_review_prompt_keeps_the_admit_uncertainty_slot() -> None:
    """needs_verification 存在的全部理由：不给 AI 一个诚实的出口，
    它就会在没有把握时也给出确定判断。"""
    assert "专门为你留的" in REVIEW_SYSTEM_PROMPT
    assert "how_to_verify" in REVIEW_SYSTEM_PROMPT


def test_review_prompt_requires_pointing_at_the_original_words() -> None:
    """指不回原话的错误说明 AI 自己也没把握，不该算错误。"""
    assert "claim" in REVIEW_SYSTEM_PROMPT
    assert "指不回原话" in REVIEW_SYSTEM_PROMPT


def test_review_prompt_is_advice_not_authority() -> None:
    """C7：建议不具权威性，不采纳是普通操作，不该有任何压力暗示。"""
    assert "只是**建议**" in REVIEW_SYSTEM_PROMPT
    assert "不同意" in REVIEW_SYSTEM_PROMPT
    assert "不会因为他没采纳" in REVIEW_SYSTEM_PROMPT


def test_review_prompt_does_not_evaluate_the_person() -> None:
    assert "只评价**理解本身**" in REVIEW_SYSTEM_PROMPT


def test_review_prompt_states_that_only_one_item_is_visible() -> None:
    """C5 最小必要传输：不发全库。提示词要明确 AI 只看得到这一条，
    否则它会去猜"你还学过……"，那就是在凭想象评价用户。"""
    assert "你看到的只有下面这一条" in REVIEW_SYSTEM_PROMPT


def test_no_prompt_invites_the_ai_to_rewrite_understanding() -> None:
    """反向检查：任何一段提示词都不该出现"替用户改好理解"的说法。"""
    for name, text in PROMPTS.items():
        assert "修正用户的理解" not in text, name
        assert "帮他改写" not in text, name


# -------------------------------------------------- 复习工作流（阶段 3 ⑤）


def test_review_rate_prompt_names_every_suggestion_field() -> None:
    for field in RATING_SUGGESTION_FIELDS:
        assert field in REVIEW_RATE_SYSTEM_PROMPT, f"评级建议提示词漏了字段 {field}"


def test_review_rate_rating_values_match_the_database_vocabulary() -> None:
    """提示词里列的四级评级必须和 review_sessions.rating 的词表一致：
    AI 建议了词表外的值，整份建议会在 C3 校验处报废。"""
    assert set(RATING_VALUES_IN_PROMPT) == set(RATINGS)


def test_review_rate_prompt_says_it_is_only_a_suggestion() -> None:
    """C7：建议不是权威，最终评级由用户确认。这句话必须写进提示词，
    否则模型会输出"你应该评 good"之类的命令式措辞去绑架用户。"""
    assert "只是**建议**" in REVIEW_RATE_SYSTEM_PROMPT
    assert "用户" in REVIEW_RATE_SYSTEM_PROMPT


def test_review_chat_prompt_forbids_revealing_the_answer() -> None:
    """验收硬项：AI 不提前泄露答案。泄露一次，这次复习就白做了。"""
    assert "绝不提前泄露答案" in REVIEW_CHAT_SYSTEM_PROMPT
    assert "一次只问一个问题" in REVIEW_CHAT_SYSTEM_PROMPT


def test_review_chat_prompt_defers_grading_to_the_dedicated_call() -> None:
    """对话过程不给评级：评级建议走 review_rate 的结构化调用，
    两件事混在一起，建议就没法过 C3 校验。"""
    assert "不要在这段对话里给评级" in REVIEW_CHAT_SYSTEM_PROMPT


# ------------------------------------------------ 关系判断（阶段 4 工作流 E）


@pytest.mark.parametrize("field", RELATION_SUGGESTION_FIELDS)
def test_relation_prompt_names_every_expected_field(field: str) -> None:
    assert field in RELATION_JUDGE_SYSTEM_PROMPT


def test_relation_prompt_lists_the_core_types() -> None:
    """六类固定词表必须逐个出现在提示词里，和 domain 词表一致（D-065）。
    提示词漏一种，AI 就会造出一个不认识的类型名，或者把六类之外的
    东西当成六类来用。"""
    assert set(RELATION_TYPES_IN_PROMPT) == set(CORE_RELATION_TYPES)
    for relation_type in RELATION_TYPES_IN_PROMPT:
        assert relation_type in RELATION_JUDGE_SYSTEM_PROMPT


def test_relation_prompt_directions_are_complete() -> None:
    assert set(RELATION_DIRECTIONS_IN_PROMPT) == {"first", "second"}


def test_relation_prompt_requires_explanation_and_evidence() -> None:
    """工作流 E 硬要求：没有解释和依据的候选不接受。提示词必须先说这件事，
    Pydantic 校验才有资格当第二道防线。"""
    assert "都必须给" in RELATION_JUDGE_SYSTEM_PROMPT
    assert "盲签" in RELATION_JUDGE_SYSTEM_PROMPT


def test_relation_prompt_says_confidence_never_auto_accepts() -> None:
    """C7：置信度再高也要用户点一下。这句必须写进提示词。"""
    assert "不会让这条关系自动通过" in RELATION_JUDGE_SYSTEM_PROMPT
