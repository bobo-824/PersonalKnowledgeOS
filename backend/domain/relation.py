"""知识关系的词表、状态机与去重键——纯函数，不 import 任何框架。

D-065：关系类型固定六类（前置知识/深入/类比/对比/应用/组成），
AI 可以提这六类之外的自定义类型（前端图谱对自定义类型统一样式、
显示原文标签）。六类只是"图例"，不是"白名单"——校验只要求
自定义类型本身干净（非空、无空白、长度有限）。

状态机（工作流 E）：pending → accepted / rejected / ignored，
全部终态。rejected 和 ignored 的行永久保留（防 AI 重复提议同一对）。
"""

# ── 关系类型（D-065 六类固定词表）─────────────────────────────

RELATION_PREREQUISITE = "prerequisite"  # 前置知识：target 是 source 的前置
RELATION_DEEPER = "deeper"  # 深入：target 是 source 的更深入展开
RELATION_ANALOGY = "analogy"  # 类比：两者结构/道理相似
RELATION_CONTRAST = "contrast"  # 对比：容易混淆，值得对照着记
RELATION_APPLICATION = "application"  # 应用：target 是 source 的应用/用例
RELATION_COMPONENT = "component"  # 组成：target 是 source 的组成部分

CORE_RELATION_TYPES = (
    RELATION_PREREQUISITE,
    RELATION_DEEPER,
    RELATION_ANALOGY,
    RELATION_CONTRAST,
    RELATION_APPLICATION,
    RELATION_COMPONENT,
)

# 图例标签。自定义类型不在表里，前端显示原文。
RELATION_TYPE_LABELS = {
    RELATION_PREREQUISITE: "前置知识",
    RELATION_DEEPER: "深入",
    RELATION_ANALOGY: "类比",
    RELATION_CONTRAST: "对比",
    RELATION_APPLICATION: "应用",
    RELATION_COMPONENT: "组成",
}

# 自定义类型的硬上限（Python 层校验用；数据库列是 String(32)）
RELATION_TYPE_MAX_LEN = 32


def is_core_relation_type(relation_type: str) -> bool:
    return relation_type in CORE_RELATION_TYPES


def is_valid_relation_type(relation_type: str) -> bool:
    """六类之外的自定义类型也接受，但必须本身干净。"""
    if not isinstance(relation_type, str):
        return False
    if is_core_relation_type(relation_type):
        return True
    stripped = relation_type.strip()
    if not stripped or stripped != relation_type:
        return False
    if len(relation_type) > RELATION_TYPE_MAX_LEN:
        return False
    return not any(ch.isspace() for ch in relation_type)


# ── 审核状态机（工作流 E）─────────────────────────────────────

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_IGNORED = "ignored"

RELATION_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_IGNORED)

# 只有 pending 可以被处置；处置完是终态，不能反悔改状态。
# 用户想换类型时走「修改类型」（改的是 relation_type 字段，不是状态）。
_ALLOWED_TRANSITIONS = {
    STATUS_PENDING: (STATUS_ACCEPTED, STATUS_REJECTED, STATUS_IGNORED),
    STATUS_ACCEPTED: (),
    STATUS_REJECTED: (),
    STATUS_IGNORED: (),
}


def is_allowed_transition(old_status: str, new_status: str) -> bool:
    return new_status in _ALLOWED_TRANSITIONS.get(old_status, ())


def can_review(status: str) -> bool:
    """该行是否还处于可处置状态（只在 pending 时可以）。"""
    return status == STATUS_PENDING


# ── 去重键与置信度 ────────────────────────────────────────────

def canonical_pair(source_id: str, target_id: str) -> tuple[str, str]:
    """一对知识的方向无关去重键（排序后的二元组）。

    存库方向是有向的（语义方向），但"这对知识已经审核过"的判断
    必须不分方向：AI 把 (A,B) 说成 (B,A) 再提一遍，同样算骚扰。
    """
    return (source_id, target_id) if source_id <= target_id else (target_id, source_id)


def is_valid_confidence(confidence: float) -> bool:
    """confidence 只用于排序（C7），但数值本身必须落在 [0, 1]。"""
    return isinstance(confidence, (int, float)) and 0.0 <= confidence <= 1.0
