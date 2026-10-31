"""Pydantic schema 层的测试。

重点不是"校验能跑"，而是三类**会随代码演进而悄悄失效**的约束：
1. schema 的取值词表与模型常量是否漂移（两边各写了一份）
2. schema 的长度上限与数据库列宽是否漂移
3. 部分更新能否区分"没传这个字段"和"想把这个字段清空"
"""

from __future__ import annotations

from typing import get_args

import pytest
from annotated_types import MaxLen
from pydantic import ValidationError

from backend.db.models import (
    CREATED_BY_VALUES,
    KNOWLEDGE_SOURCE_TYPES,
    SOURCE_KINDS,
    STATUSES,
    Knowledge,
    KnowledgeVersion,
    Source,
)
from backend.schemas.knowledge import (
    CreatedBy,
    KnowledgeCreate,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpdate,
    SourceCreate,
    SourceKind,
)


def _max_length(model: type, field: str) -> int | None:
    """取出 Pydantic v2 字段上的 max_length。"""
    for item in model.model_fields[field].metadata:
        if isinstance(item, MaxLen):
            return item.max_length
    return None


class TestVocabularyDrift:
    """schema 用 Literal 写死取值（为了 OpenAPI 能显示枚举），
    模型用字符串常量（为了加取值时不用改数据库）。
    两边各一份，必须有人盯着它们一致。
    """

    def test_source_kind_literal_matches_model_constants(self):
        assert set(get_args(SourceKind)) == set(SOURCE_KINDS)

    def test_knowledge_source_type_literal_matches_model_constants(self):
        assert set(get_args(KnowledgeSourceType)) == set(KNOWLEDGE_SOURCE_TYPES)

    def test_status_literal_matches_model_constants(self):
        assert set(get_args(KnowledgeStatus)) == set(STATUSES)

    def test_created_by_literal_matches_model_constants(self):
        assert set(get_args(CreatedBy)) == set(CREATED_BY_VALUES)

    def test_ai_is_not_a_valid_created_by_value(self):
        """Blueprint 原则 2：AI 不能直接创建版本。

        这条同时守住两边——常量里没有 "ai"，schema 的 Literal 里也没有。
        """
        assert "ai" not in CREATED_BY_VALUES
        assert "ai" not in get_args(CreatedBy)

    def test_the_two_source_vocabularies_are_actually_different(self):
        """两套 source_type 词表含义不同，必须真的不一样。

        如果哪天有人把它们统一了，说明误解了设计（见 source.py 顶部说明），
        这条测试会先叫出来。
        """
        assert set(SOURCE_KINDS) != set(KNOWLEDGE_SOURCE_TYPES)
        assert len(SOURCE_KINDS) == 8
        assert len(KNOWLEDGE_SOURCE_TYPES) == 2


class TestLengthMatchesDatabase:
    """schema 的长度上限必须等于数据库列宽。

    不一致的后果：超长数据绕过校验，一路走到 SQLite 才报错，
    用户看到 500 而不是清晰的 422。
    """

    @pytest.mark.parametrize(
        ("field", "column_length"),
        [
            ("title", Knowledge.__table__.c.title.type.length),
            ("domain", Knowledge.__table__.c.domain.type.length),
        ],
    )
    def test_knowledge_create_lengths(self, field: str, column_length: int):
        assert _max_length(KnowledgeCreate, field) == column_length

    def test_knowledge_update_lengths_match_create(self):
        for field in ("title", "domain"):
            assert _max_length(KnowledgeUpdate, field) == _max_length(
                KnowledgeCreate, field
            )

    def test_source_title_length_matches_column(self):
        assert (
            _max_length(SourceCreate, "title")
            == Source.__table__.c.title.type.length
        )

    def test_source_url_length_matches_column(self):
        assert (
            _max_length(SourceCreate, "url_or_reference")
            == Source.__table__.c.url_or_reference.type.length
        )

    def test_change_reason_length_matches_column(self):
        assert (
            _max_length(KnowledgeCreate, "change_reason")
            == KnowledgeVersion.__table__.c.change_reason.type.length
        )

    def test_title_over_limit_is_rejected_before_reaching_the_database(self):
        limit = Knowledge.__table__.c.title.type.length
        with pytest.raises(ValidationError) as excinfo:
            KnowledgeCreate(title="x" * (limit + 1), user_understanding="我理解了这个知识")
        assert "title" in str(excinfo.value)


class TestSourceOtherRequiresNote:
    """DECISIONS D-029：选「其他」必须写清楚是什么来源。"""

    def test_other_without_note_is_rejected(self):
        with pytest.raises(ValidationError, match="备注必填"):
            SourceCreate(source_type="other", title="某个来源")

    def test_other_with_blank_note_is_rejected(self):
        """空白串不算填了。"""
        with pytest.raises(ValidationError, match="备注必填"):
            SourceCreate(source_type="other", title="某个来源", note="   ")

    def test_other_with_real_note_is_accepted(self):
        source = SourceCreate(
            source_type="other", title="某个来源", note="某 up 主的直播"
        )
        assert source.note == "某 up 主的直播"

    @pytest.mark.parametrize("kind", [k for k in SOURCE_KINDS if k != "other"])
    def test_non_other_kinds_do_not_require_note(self, kind: str):
        """只有 other 才强制备注，其他类型不填也能过——否则记录本身成了负担。"""
        source = SourceCreate(source_type=kind, title="某个来源")
        assert source.note is None

    def test_unknown_source_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            SourceCreate(source_type="blog", title="某个来源")


class TestUnderstandingIsSubstantive:
    """「自己的理解」是这个产品的核心字段，不能应付式填写。"""

    @pytest.mark.parametrize("junk", [".", "，", "???", "。。。", "-", "、"])
    def test_punctuation_only_understanding_is_rejected(self, junk: str):
        """min_length=1 拦不住这些，必须有专门的检查。"""
        with pytest.raises(ValidationError, match="实际内容"):
            KnowledgeCreate(title="梯度下降", user_understanding=junk)

    def test_whitespace_only_understanding_is_rejected(self):
        with pytest.raises(ValidationError):
            KnowledgeCreate(title="梯度下降", user_understanding="     ")

    def test_empty_understanding_is_rejected(self):
        with pytest.raises(ValidationError):
            KnowledgeCreate(title="梯度下降", user_understanding="")

    def test_missing_understanding_is_rejected(self):
        """必填：不允许只写标题就保存。"""
        with pytest.raises(ValidationError):
            KnowledgeCreate(title="梯度下降")  # type: ignore[call-arg]

    def test_short_but_real_understanding_is_accepted(self):
        """不能矫枉过正——一句话的真实理解也应该能存。"""
        knowledge = KnowledgeCreate(title="熵", user_understanding="混乱程度")
        assert knowledge.user_understanding == "混乱程度"

    def test_alphanumeric_counts_as_real_content(self):
        assert KnowledgeCreate(title="E=mc²", user_understanding="质能等价").title


class TestBlankNormalization:
    def test_whitespace_only_optional_fields_become_none(self):
        knowledge = KnowledgeCreate(
            title="梯度下降",
            user_understanding="沿负梯度方向迭代",
            domain="   ",
            reasoning="",
            open_questions="\n\t ",
        )
        assert knowledge.domain is None
        assert knowledge.reasoning is None
        assert knowledge.open_questions is None

    def test_surrounding_whitespace_is_stripped(self):
        knowledge = KnowledgeCreate(
            title="  梯度下降  ",
            user_understanding="  沿负梯度方向迭代  ",
            domain="  机器学习  ",
        )
        assert knowledge.title == "梯度下降"
        assert knowledge.user_understanding == "沿负梯度方向迭代"
        assert knowledge.domain == "机器学习"

    def test_internal_newlines_are_preserved(self):
        """用户的理解常常是多段的，只清理两端，不能压掉内部换行。"""
        text = "第一段\n\n第二段"
        knowledge = KnowledgeCreate(
            title="梯度下降", user_understanding=f"  {text}  "
        )
        assert knowledge.user_understanding == text


class TestPartialUpdateSemantics:
    """KnowledgeUpdate 必须能区分"没传"和"传了 null"。

    这是 service 层正确性的前提：用 model_dump() 后检查 None 会把两者混为一谈。
    """

    def test_absent_field_is_not_in_fields_set(self):
        update = KnowledgeUpdate.model_validate({"title": "新标题"})
        assert update.model_fields_set == {"title"}
        assert "reasoning" not in update.model_fields_set

    def test_explicit_null_is_in_fields_set(self):
        update = KnowledgeUpdate.model_validate({"reasoning": None})
        assert update.model_fields_set == {"reasoning"}

    def test_absent_and_explicit_null_dump_identically_but_differ_in_fields_set(self):
        """这条测试就是在钉住上面那个陷阱：dump 一样，语义完全不同。"""
        absent = KnowledgeUpdate.model_validate({"title": "新标题"})
        explicit = KnowledgeUpdate.model_validate({"title": "新标题", "reasoning": None})

        assert absent.model_dump()["reasoning"] == explicit.model_dump()["reasoning"]
        assert absent.model_fields_set != explicit.model_fields_set

    def test_empty_update_changes_nothing(self):
        update = KnowledgeUpdate.model_validate({})
        assert update.model_fields_set == set()

    def test_clearing_reasoning_is_expressible(self):
        update = KnowledgeUpdate.model_validate({"reasoning": None})
        assert "reasoning" in update.model_fields_set
        assert update.reasoning is None

    def test_sources_absent_vs_empty_list(self):
        """不传 sources = 保持原样；传空数组 = 清空全部来源。"""
        absent = KnowledgeUpdate.model_validate({"title": "新标题"})
        cleared = KnowledgeUpdate.model_validate({"sources": []})
        assert absent.sources is None
        assert cleared.sources == []
        assert "sources" not in absent.model_fields_set


class TestExtraFieldsForbidden:
    def test_misspelled_field_is_rejected_not_silently_ignored(self):
        """前端把 title 拼成 titel 时，必须报错。

        如果静默忽略，用户会以为改成功了，实际什么都没改——
        这种 bug 极难排查。
        """
        with pytest.raises(ValidationError, match="titel"):
            KnowledgeCreate.model_validate(
                {"titel": "梯度下降", "user_understanding": "沿负梯度方向迭代"}
            )

    def test_update_rejects_status_field(self):
        """归档不是编辑，走独立接口（见 KnowledgeUpdate 的文档字符串）。"""
        with pytest.raises(ValidationError, match="status"):
            KnowledgeUpdate.model_validate({"status": "archived"})

    def test_update_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            KnowledgeUpdate.model_validate({"nonexistent": 1})


class TestDefaults:
    def test_create_defaults_to_manual_source_type(self):
        knowledge = KnowledgeCreate(title="梯度下降", user_understanding="沿负梯度方向迭代")
        assert knowledge.source_type == "manual"

    def test_create_defaults_to_no_sources(self):
        knowledge = KnowledgeCreate(title="梯度下降", user_understanding="沿负梯度方向迭代")
        assert knowledge.sources == []

    def test_source_defaults_to_other_kind(self):
        """默认 other 是刻意的：来源类型不确定时宁可标成"其他"+必填说明，
        也不要猜一个错误的类型。"""
        with pytest.raises(ValidationError, match="备注必填"):
            SourceCreate(title="某个来源")
