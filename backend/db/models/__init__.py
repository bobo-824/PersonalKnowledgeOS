"""全部 ORM 模型。Alembic 导入本模块即可拿到完整的 metadata。"""

from backend.db.models.knowledge import (
    SOURCE_TYPE_CONVERSATION,
    SOURCE_TYPE_MANUAL,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    Knowledge,
)
from backend.db.models.knowledge_version import (
    CREATED_BY_AI_CONFIRMED,
    CREATED_BY_USER,
    KnowledgeVersion,
)
from backend.db.models.source import Source

__all__ = [
    "CREATED_BY_AI_CONFIRMED",
    "CREATED_BY_USER",
    "SOURCE_TYPE_CONVERSATION",
    "SOURCE_TYPE_MANUAL",
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "Knowledge",
    "KnowledgeVersion",
    "Source",
]
