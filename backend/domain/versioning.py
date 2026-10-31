"""知识版本的领域规则。

本模块**不 import sqlalchemy，也不 import fastapi**。
这里承载的是整个产品最核心的不变量——「用户原文永不被覆盖」和
「只有理解真的变了才留新版本」（Blueprint 1.5 原则 2、DECISIONS D-030），
必须能用普通 pytest 直接断言，不需要起 TestClient、不需要建临时库。
一旦这一层沾上数据库，测试就得先跑迁移，反馈会慢一个数量级。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

# 只有这三个字段的变化才算「理解变了」。
#
# title / domain / summary / sources 属于整理性工作，就地 UPDATE，不建版本。
# 代价是标题的演变历史留不下来（title 在 knowledge 主表上，
# knowledge_versions 没有标题快照列）——这是用户知情并接受的取舍。
# 完整理由见 docs/DECISIONS.md D-030。
#
# 三个字段都算，是因为「我的疑问变了」本身就是理解在变化；
# 只盯 user_understanding 会漏掉真实的认知演进。
UNDERSTANDING_FIELDS: tuple[str, ...] = (
    "user_understanding",
    "reasoning",
    "open_questions",
)


class UnderstandingLike(Protocol):
    """任何带有这三个字段的对象：ORM 的 KnowledgeVersion、Pydantic 的请求模型都可以。

    用 Protocol 而不是定义一个 dataclass 让调用方转换，
    是为了避免 service 层在 ORM 对象和领域对象之间来回搬运同一份数据。
    """

    user_understanding: str | None
    reasoning: str | None
    open_questions: str | None


@dataclass(frozen=True, slots=True)
class Understanding:
    """一份理解内容的快照。结构上满足 UnderstandingLike，可直接参与比较。

    存在的理由是部分更新：`KnowledgeUpdate` 里缺席的字段表示"不改"，
    service 层需要先把"改完之后应该是什么"算出来，才能和当前版本比对。
    这个中间结果就是 Understanding。
    """

    user_understanding: str | None
    reasoning: str | None = None
    open_questions: str | None = None


def _normalize(value: str | None) -> str:
    """把 None、空串、纯空白统一成 ""，并去掉首尾空白。

    没有这一步会有两种假变化：
    - 用户把「尚存疑问」从 None 改成 ""（前端表单清空后提交的就是空串），
      语义上什么都没变，却会被判定成理解变了，凭空多一个版本。
    - 用户只在末尾多打了一个空格，同样会多一个版本。
    """
    return value.strip() if value else ""


def changed_fields(
    old: UnderstandingLike, new: UnderstandingLike
) -> tuple[str, ...]:
    """返回实际发生变化的理解类字段名，顺序与 UNDERSTANDING_FIELDS 一致。

    比的是**归一化后的实际值**，不是「有没有提交这个字段」。
    """
    return tuple(
        name
        for name in UNDERSTANDING_FIELDS
        if _normalize(getattr(old, name)) != _normalize(getattr(new, name))
    )


def understanding_changed(old: UnderstandingLike, new: UnderstandingLike) -> bool:
    """是否需要创建新版本。

    **service 层必须先调这个函数再决定建不建版本**，不能无脑建。
    否则「用户点了保存但什么都没改」也会让版本号 +1，制造出假历史。
    """
    return bool(changed_fields(old, new))


def next_version_number(existing_numbers: Iterable[int]) -> int:
    """下一个版本号：已有版本号的最大值 + 1，没有则从 1 开始。

    用 max 而不是 len：版本号必须单调递增且永不复用。
    即使某个版本行因为任何原因被移除，用 len 也算出一个已经用过的号，
    撞上 (knowledge_id, version_number) 的唯一约束。
    """
    highest = max(existing_numbers, default=0)
    return highest + 1
