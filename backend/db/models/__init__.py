"""全部 ORM 模型。Alembic 导入本模块即可拿到完整的 metadata。

取值常量也在这里统一导出，避免调用方直接 import 具体模型文件。
两套来源词表的区别见 source.py 顶部说明与 docs/DECISIONS.md D-029。
"""

from backend.db.models.ai_conversation import AiConversation
from backend.db.models.ai_message import (
    ERROR_KINDS,
    ERROR_KIND_AUTH_REJECTED,
    ERROR_KIND_SCHEMA_INVALID,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_UNAVAILABLE,
    MESSAGE_ROLES,
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_USER,
    AiMessage,
)
from backend.db.models.ai_review import AiReview
from backend.db.models.ai_review_decision import (
    CATEGORY_CORRECT_POINTS,
    CATEGORY_ERRORS,
    CATEGORY_FOLLOW_UP_QUESTIONS,
    CATEGORY_NEEDS_VERIFICATION,
    CATEGORY_OMISSIONS,
    DECISION_ACCEPTED,
    DECISION_DISAGREED,
    DECISION_IGNORED,
    REVIEW_CATEGORIES,
    REVIEW_DECISIONS,
    AiReviewDecision,
)
from backend.db.models.idempotency_key import IdempotencyKey
from backend.db.models.knowledge import (
    KNOWLEDGE_SOURCE_TYPES,
    SOURCE_TYPE_CONVERSATION,
    SOURCE_TYPE_MANUAL,
    STATUSES,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    Knowledge,
)
from backend.db.models.knowledge_version import (
    CREATED_BY_AI_CONFIRMED,
    CREATED_BY_USER,
    CREATED_BY_VALUES,
    KnowledgeVersion,
)
from backend.db.models.source import (
    SOURCE_KINDS,
    SOURCE_KIND_ARTICLE,
    SOURCE_KIND_BOOK,
    SOURCE_KIND_CONVERSATION,
    SOURCE_KIND_COURSE,
    SOURCE_KIND_OTHER,
    SOURCE_KIND_PAPER,
    SOURCE_KIND_PODCAST,
    SOURCE_KIND_VIDEO,
    Source,
)

__all__ = [
    "CATEGORY_CORRECT_POINTS",
    "CATEGORY_ERRORS",
    "CATEGORY_FOLLOW_UP_QUESTIONS",
    "CATEGORY_NEEDS_VERIFICATION",
    "CATEGORY_OMISSIONS",
    "CREATED_BY_AI_CONFIRMED",
    "CREATED_BY_USER",
    "CREATED_BY_VALUES",
    "DECISION_ACCEPTED",
    "DECISION_DISAGREED",
    "DECISION_IGNORED",
    "ERROR_KINDS",
    "ERROR_KIND_AUTH_REJECTED",
    "ERROR_KIND_SCHEMA_INVALID",
    "ERROR_KIND_TIMEOUT",
    "ERROR_KIND_UNAVAILABLE",
    "KNOWLEDGE_SOURCE_TYPES",
    "MESSAGE_ROLES",
    "MESSAGE_ROLE_ASSISTANT",
    "MESSAGE_ROLE_USER",
    "REVIEW_CATEGORIES",
    "REVIEW_DECISIONS",
    "SOURCE_KINDS",
    "SOURCE_KIND_ARTICLE",
    "SOURCE_KIND_BOOK",
    "SOURCE_KIND_CONVERSATION",
    "SOURCE_KIND_COURSE",
    "SOURCE_KIND_OTHER",
    "SOURCE_KIND_PAPER",
    "SOURCE_KIND_PODCAST",
    "SOURCE_KIND_VIDEO",
    "SOURCE_TYPE_CONVERSATION",
    "SOURCE_TYPE_MANUAL",
    "STATUSES",
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "AiConversation",
    "AiMessage",
    "AiReview",
    "AiReviewDecision",
    "IdempotencyKey",
    "Knowledge",
    "KnowledgeVersion",
    "Source",
]
