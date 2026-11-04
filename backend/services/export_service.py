"""结构化导出（阶段 5，D-069）：Markdown 单文件 + JSON 全量。

两种格式回答两个不同的问题：
- **Markdown 给人读**：按领域分组、带目录，每条知识含当前理解正文、
  理解依据、尚存疑问和学习来源——可以直接粘进别的笔记软件。
- **JSON 给机器**：全部 10 张业务表逐表完整保真（含版本历史、
  复习记录、AI 对话、关系），将来搬数据或导回靠它。

导出只读本机数据库、只写响应体，不碰 AI、不碰网络（C5/C6 无涉：
正文会进导出文件，但那是用户自己点了下载的本机文件）。
FTS5 是派生的虚拟表，不在导出里——它随时能从正文重建（D-006）。
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import APP_VERSION
from backend.db.models import (
    STATUS_ACTIVE,
    AiConversation,
    AiMessage,
    AiReview,
    AiReviewDecision,
    IdempotencyKey,
    Knowledge,
    KnowledgeRelation,
    KnowledgeVersion,
    ReviewSchedule,
    ReviewSession,
    Source,
)

# JSON 导出的表清单：顺序即依赖顺序（被引用的在前）。
# 全部是业务表；FTS5 虚拟表不进（它是索引不是数据）。
_JSON_TABLES: tuple[type, ...] = (
    Knowledge,
    KnowledgeVersion,
    Source,
    AiConversation,
    AiMessage,
    AiReview,
    AiReviewDecision,
    IdempotencyKey,
    ReviewSchedule,
    ReviewSession,
    KnowledgeRelation,
)


# ================================================================ Markdown


def export_markdown(session: Session) -> str:
    """整库导出成一份 Markdown。archived 的知识也导出（带标记）：
    导出的目的是"带走全部理解"，悄悄丢掉归档项就是造假。"""
    knowledges = session.scalars(
        select(Knowledge).order_by(Knowledge.title)
    ).all()
    versions = {
        v.id: v
        for v in session.scalars(select(KnowledgeVersion)).all()
    }
    sources_by_knowledge: dict[str, list[Source]] = defaultdict(list)
    for source in session.scalars(select(Source)).all():
        sources_by_knowledge[source.knowledge_id].append(source)

    by_domain: dict[str, list[Knowledge]] = defaultdict(list)
    for k in knowledges:
        by_domain[k.domain or "未分类"].append(k)

    lines: list[str] = []
    _md_header(lines, knowledges, by_domain)
    for domain in sorted(by_domain):
        items = sorted(by_domain[domain], key=lambda k: k.title)
        lines.append(f"\n## {domain}\n")
        for k in items:
            _md_knowledge(lines, k, versions, sources_by_knowledge.get(k.id, []))
    return "\n".join(lines) + "\n"


def _md_header(
    lines: list[str],
    knowledges: list[Knowledge],
    by_domain: dict[str, list[Knowledge]],
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("# 个人知识库导出\n")
    lines.append(f"- 导出时间：{now}")
    lines.append(f"- 知识总数：{len(knowledges)} 条（{len(by_domain)} 个领域）")
    lines.append("- 本文件由 PersonalKnowledgeOS 自动生成，理解正文均为你的原文\n")
    lines.append("## 目录\n")
    for domain in sorted(by_domain):
        anchor = domain.replace(" ", "-")
        lines.append(f"- [{domain}](#{anchor})（{len(by_domain[domain])} 条）")


def _md_knowledge(
    lines: list[str],
    knowledge: Knowledge,
    versions: dict[str, KnowledgeVersion],
    sources: list[Source],
) -> None:
    flag = "（已归档）" if knowledge.status != STATUS_ACTIVE else ""
    lines.append(f"\n### {knowledge.title}{flag}\n")
    if knowledge.summary:
        lines.append(f"> {knowledge.summary}\n")

    version = versions.get(knowledge.current_version_id or "")
    version_count = sum(1 for v in versions.values() if v.knowledge_id == knowledge.id)
    meta = [f"版本数：{version_count}"]
    if knowledge.domain:
        meta.append(f"领域：{knowledge.domain}")
    lines.append("- " + "；".join(meta) + "\n")

    if version is not None:
        lines.append("**我的理解**\n")
        lines.append(version.user_understanding + "\n")
        if version.reasoning:
            lines.append("**理解依据**\n")
            lines.append(version.reasoning + "\n")
        if version.open_questions:
            lines.append("**尚存疑问**\n")
            lines.append(version.open_questions + "\n")
    if sources:
        lines.append("**学习来源**\n")
        for source in sources:
            ref = f"：{source.url_or_reference}" if source.url_or_reference else ""
            lines.append(f"- {source.title}{ref}")
        lines.append("")


# ================================================================ JSON


def export_json(session: Session) -> bytes:
    """全部业务表逐表导出。datetime 一律转成 ISO 字符串（带 UTC 时区，
    和库里的 UTCDateTime 约定一致），JSON 不再有"日期没有时区"这个坑。"""
    payload: dict[str, Any] = {
        "exported_at": datetime.now(UTC).isoformat(),
        "app_version": APP_VERSION,
        "format": "pkos-full-export/1",
    }
    for model in _JSON_TABLES:
        rows = session.scalars(select(model)).all()
        payload[model.__tablename__] = [
            {
                column.name: _jsonable(getattr(row, column.name))
                for column in model.__table__.columns
            }
            for row in rows
        ]
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = ["export_json", "export_markdown"]
