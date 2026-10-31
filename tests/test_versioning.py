"""领域层版本规则的测试。

这些是纯函数，不需要数据库夹具，跑起来是毫秒级的。
把 D-030 的约束放在这里断言，比等到 service 层集成测试才发现要便宜得多。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.domain.versioning import (
    UNDERSTANDING_FIELDS,
    changed_fields,
    next_version_number,
    understanding_changed,
)


@dataclass
class Fake:
    """替身对象：模拟 ORM 的 KnowledgeVersion 和 Pydantic 请求模型的共同形状。"""

    user_understanding: str | None = None
    reasoning: str | None = None
    open_questions: str | None = None


class TestUnderstandingChanged:
    def test_nothing_changed_means_no_new_version(self):
        old = Fake("梯度下降是沿负梯度方向迭代更新参数", "看了推导", "学习率怎么选")
        new = Fake("梯度下降是沿负梯度方向迭代更新参数", "看了推导", "学习率怎么选")
        assert understanding_changed(old, new) is False
        assert changed_fields(old, new) == ()

    @pytest.mark.parametrize("field", UNDERSTANDING_FIELDS)
    def test_each_understanding_field_alone_triggers_a_version(self, field: str):
        """三个理解字段任一变了，都必须建新版本——不能只盯 user_understanding。"""
        old = Fake("原文", "依据", "疑问")
        new = Fake("原文", "依据", "疑问")
        setattr(new, field, f"{getattr(old, field)}（改过了）")
        assert understanding_changed(old, new) is True
        assert changed_fields(old, new) == (field,)

    def test_changed_fields_reports_all_that_differ_in_declared_order(self):
        old = Fake("原文", "依据", "疑问")
        new = Fake("新原文", "依据", "新疑问")
        # reasoning 没变，所以不在结果里；顺序跟 UNDERSTANDING_FIELDS 一致
        assert changed_fields(old, new) == ("user_understanding", "open_questions")

    def test_whitespace_only_edit_is_not_a_change(self):
        """只在末尾多打个空格，不应该产生版本。"""
        old = Fake("原文", "依据", "疑问")
        new = Fake("原文  ", "依据\n", "疑问\t ")
        assert understanding_changed(old, new) is False

    @pytest.mark.parametrize("empty", [None, "", "   ", "\n\t"])
    def test_none_and_blank_are_equivalent(self, empty: str | None):
        """None / 空串 / 纯空白语义相同。

        前端表单清空一个选填字段后提交的可能是 ""，而数据库里存的是 None，
        不归一化就会把「什么都没改」误判成「理解变了」，凭空多一个版本。
        """
        old = Fake("原文", None, None)
        new = Fake("原文", empty, empty)
        assert understanding_changed(old, new) is False

    def test_clearing_a_field_is_a_real_change(self):
        """但把有内容的字段清空，是真的变了，必须留版本。"""
        old = Fake("原文", "我看了推导过程", None)
        new = Fake("原文", None, None)
        assert understanding_changed(old, new) is True
        assert changed_fields(old, new) == ("reasoning",)

    def test_works_with_objects_that_have_extra_attributes(self):
        """Protocol 只要求三个字段存在，ORM 对象和 Pydantic 模型都多带别的属性。"""

        class WithExtras:
            id = "some-uuid"
            version_number = 3
            created_by = "user"
            user_understanding = "原文"
            reasoning = None
            open_questions = None
            title = "这个字段不属于理解，不该参与比较"
            domain = "机器学习"

        assert understanding_changed(WithExtras(), Fake("原文", None, None)) is False
        # 改 title 不算理解变化（D-030：标题就地更新，不建版本）
        other = WithExtras()
        other.title = "改了标题"
        assert understanding_changed(WithExtras(), other) is False


class TestNextVersionNumber:
    def test_first_version_is_one(self):
        assert next_version_number([]) == 1

    def test_continues_from_the_highest(self):
        assert next_version_number([1, 2, 3]) == 4

    def test_uses_max_not_count_so_numbers_are_never_reused(self):
        """用 max 而不是 len：即使中间某个版本行不在了，也不能复用已有版本号。

        复用会撞上 (knowledge_id, version_number) 的唯一约束，
        或者更糟——悄悄覆盖历史。
        """
        assert next_version_number([1, 2, 5]) == 6
        assert next_version_number([3]) == 4

    def test_accepts_any_iterable(self):
        assert next_version_number(v for v in range(1, 4)) == 4

    def test_unordered_input(self):
        assert next_version_number([2, 7, 1, 4]) == 8
