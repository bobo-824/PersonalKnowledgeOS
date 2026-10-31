"""全文检索，以及 FTS5 独立索引的同步。

## 为什么是两条检索路径

`tokenize='trigram'` 是 SQLite 内置分词器里唯一对中文有效的，但 MATCH 查询
**要求至少 3 个字符**——「梯度」「熵」这类 1~2 字词会直接返回空结果，
而这在中文学习笔记里极其常见。所以：

| 查询长度 | 路径 | 查哪里 |
|---|---|---|
| ≥ 3 字符 | FTS5 MATCH | `knowledge_fts` 虚拟表 |
| 1~2 字符 | LIKE 扫描 | **权威表**（`knowledge` + `knowledge_versions`） |

短查询刻意**不查 FTS 表**而是查权威表：这样即使索引因为漏调 `sync_index`
而不一致（DECISIONS D-006 明确记录了这个风险），1~2 字搜索仍然能给出正确结果。
把两条路径都建在 FTS 表上会让索引故障变成完全搜不到，反而更难发现原因。

实测：5000 条中文数据下 LIKE 约 1.0~1.4ms，与 FTS5 结果一致（D-007）。

## 注入防护

两条路径都把用户输入当**数据**而不是**查询语法**处理：

- FTS5：用双引号包成短语查询，内部双引号翻倍转义。否则用户输入
  `"` `*` `-` `NEAR` 等会让 MATCH 抛语法错误，甚至改变查询语义。
- LIKE：转义 `%` `_` `\\` 并声明 ESCAPE 字符。否则输入一个 `%` 就会匹配全库。
"""

from __future__ import annotations

import logging

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.orm import Session

from backend.db.models import Knowledge, KnowledgeVersion
from backend.schemas.knowledge import SearchResult, SearchResultItem

logger = logging.getLogger(__name__)

# trigram 分词器的最小查询长度。低于这个值 MATCH 返回空，必须走 LIKE。
FTS_MIN_LENGTH = 3

# 索引的正文取自当前版本的这一列（见 docs/DATA_MODEL.md）。
# 刻意不含 reasoning / open_questions：搜的是"我写过的理解"，
# 把疑问和依据也混进正文会让命中变得模糊。
FTS_TABLE = "knowledge_fts"


# ===== 用户输入的转义 =====


def _fts_phrase(query: str) -> str:
    """把用户输入变成 FTS5 的短语查询字面量。

    FTS5 里双引号包裹表示"按字面短语匹配"，内部的双引号靠翻倍转义。
    这样 `a"b` 或 `NEAR(a b)` 这类输入都只会被当成普通文本。
    """
    return '"' + query.replace('"', '""') + '"'


def _like_pattern(query: str) -> str:
    """把用户输入变成安全的 LIKE 模式，配合 escape='\\\\' 使用。

    转义顺序要紧：先转义反斜杠本身，再转义 % 和 _。
    反过来的话，转义 % 时加的反斜杠会在下一步被再转义一次。
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(body: str | None, query: str, width: int = 30) -> str | None:
    """在正文里截一段包含命中位置的文本，供前端预览。

    不用 FTS5 的 snippet() 函数，因为 LIKE 路径没有它，
    两条路径要用同一套逻辑才能给出一致的预览。
    """
    if not body:
        return None
    lowered_body = body.lower()
    lowered_query = query.lower()
    position = lowered_body.find(lowered_query)
    if position < 0:
        return body[:width].strip() or None
    start = max(0, position - width // 2)
    end = min(len(body), start + width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(body) else ""
    return f"{prefix}{body[start:end].strip()}{suffix}"


# ===== 索引同步 =====


def sync_index(session: Session, knowledge: Knowledge) -> None:
    """把一条知识的当前状态写进 FTS5 索引。幂等，可重复调用。

    ⚠️ **FTS5 是独立表，不会自动跟着数据变**（DECISIONS D-006）。
    任何改动了 title 或当前版本 user_understanding 的写入路径都必须调这个函数，
    否则索引与数据不一致：改了标题却还搜到旧标题。

    本函数不 commit，由调用方的事务统一提交。
    """
    knowledge_id = knowledge.id
    # 先删后插而不是 UPDATE：一条知识在索引里可能因为历史 bug 存在多行，
    # 先删干净能保证幂等。
    session.execute(
        text(f"DELETE FROM {FTS_TABLE} WHERE knowledge_id = :kid"),
        {"kid": knowledge_id},
    )

    title = knowledge.title or ""
    body = ""
    current = knowledge.current_version
    if current is not None:
        body = current.user_understanding or ""

    session.execute(
        text(f"INSERT INTO {FTS_TABLE} (knowledge_id, title, body) VALUES (:kid, :title, :body)"),
        {"kid": knowledge_id, "title": title, "body": body},
    )


def remove_from_index(session: Session, knowledge_id: str) -> None:
    """从索引里移除一条知识。"""
    session.execute(
        text(f"DELETE FROM {FTS_TABLE} WHERE knowledge_id = :kid"),
        {"kid": knowledge_id},
    )


def rebuild_index(session: Session) -> int:
    """清空并重建整个索引，返回索引的知识条数。

    用于索引不一致时的修复（比如某次写入漏调了 sync_index）。
    不 commit，由调用方决定。
    """
    session.execute(text(f"DELETE FROM {FTS_TABLE}"))

    rows = session.execute(
        select(Knowledge.id, Knowledge.title, KnowledgeVersion.user_understanding)
        .outerjoin(
            KnowledgeVersion, Knowledge.current_version_id == KnowledgeVersion.id
        )
    ).all()

    for knowledge_id, title, body in rows:
        session.execute(
            text(
                f"INSERT INTO {FTS_TABLE} (knowledge_id, title, body) "
                "VALUES (:kid, :title, :body)"
            ),
            {"kid": knowledge_id, "title": title or "", "body": body or ""},
        )

    logger.info("FTS5 索引已重建，共 %d 条知识", len(rows))
    return len(rows)


def index_stats(session: Session) -> dict[str, int]:
    """索引里的行数与知识条数，用于自检两者是否一致。"""
    indexed = session.execute(
        text(f"SELECT COUNT(*) FROM {FTS_TABLE}")
    ).scalar_one()
    total = session.execute(select(func.count(Knowledge.id))).scalar_one()
    return {"indexed": int(indexed), "knowledge": int(total)}


# ===== 检索 =====


def _match_ids_by_fts(session: Session, query: str) -> tuple[set[str], set[str]]:
    """FTS5 路径：分别取命中标题的和命中正文的 id。

    分两次查而不是查一次再判断，是为了能填 `matched_by`。
    """
    phrase = _fts_phrase(query)
    titles = {
        row[0]
        for row in session.execute(
            text(f"SELECT knowledge_id FROM {FTS_TABLE} WHERE title MATCH :q"),
            {"q": phrase},
        ).all()
    }
    bodies = {
        row[0]
        for row in session.execute(
            text(f"SELECT knowledge_id FROM {FTS_TABLE} WHERE body MATCH :q"),
            {"q": phrase},
        ).all()
    }
    return titles, bodies


def _match_ids_by_like(session: Session, query: str) -> tuple[set[str], set[str]]:
    """LIKE 路径：查权威表，不依赖 FTS 索引是否同步。"""
    pattern = _like_pattern(query)

    titles = set(
        session.execute(
            select(Knowledge.id).where(
                Knowledge.title.like(pattern, escape="\\")
            )
        ).scalars()
    )
    bodies = set(
        session.execute(
            select(Knowledge.id)
            .join(
                KnowledgeVersion,
                Knowledge.current_version_id == KnowledgeVersion.id,
            )
            .where(KnowledgeVersion.user_understanding.like(pattern, escape="\\"))
        ).scalars()
    )
    return titles, bodies


def search(
    session: Session,
    query: str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> SearchResult:
    """按查询串搜索知识，返回标题命中与正文命中的并集。

    `status` 为 None 时不过滤；传 'active' 则排除已归档
    （归档项默认不该出现在搜索结果里，见 Blueprint 5.1）。

    去重和分页在 Python 侧完成：命中的 id 集合本来就要在内存里做并集，
    再切一次片比构造带 UNION 的 SQL 更直白。个人知识库的量级下这不是瓶颈。
    """
    stripped = query.strip()
    if not stripped:
        # 空查询不返回全库。API 层应当用 Query(min_length=1) 挡住，
        # 这里只是兜底，避免直接调 service 时把整库拉出来。
        return SearchResult(items=[], total=0, query=query, strategy="like")

    use_fts = len(stripped) >= FTS_MIN_LENGTH
    strategy = "fts" if use_fts else "like"

    if use_fts:
        title_ids, body_ids = _match_ids_by_fts(session, stripped)
    else:
        title_ids, body_ids = _match_ids_by_like(session, stripped)

    matched_ids = title_ids | body_ids
    if not matched_ids:
        return SearchResult(items=[], total=0, query=query, strategy=strategy)

    statement = select(Knowledge, KnowledgeVersion.user_understanding).outerjoin(
        KnowledgeVersion, Knowledge.current_version_id == KnowledgeVersion.id
    ).where(Knowledge.id.in_(matched_ids))
    if status is not None:
        statement = statement.where(Knowledge.status == status)
    # 排序稳定，否则同样的查询两次结果顺序可能不同，分页会重复或漏项
    statement = statement.order_by(Knowledge.updated_at.desc(), Knowledge.id)

    rows = session.execute(statement).all()
    total = len(rows)
    page = rows[offset : offset + limit]

    items = []
    for knowledge, understanding in page:
        kid = knowledge.id
        in_title = kid in title_ids
        in_body = kid in body_ids
        matched_by = "both" if (in_title and in_body) else ("title" if in_title else "body")
        items.append(
            SearchResultItem(
                knowledge_id=kid,
                title=knowledge.title,
                domain=knowledge.domain,
                status=knowledge.status,
                matched_by=matched_by,
                snippet=_snippet(understanding, stripped),
            )
        )

    return SearchResult(items=items, total=total, query=query, strategy=strategy)


def suggest_domains(session: Session, *, limit: int = 50) -> list[tuple[str, int]]:
    """已用过的领域名及其知识条数，按条数降序（DECISIONS D-031）。

    前端用原生 `<datalist>` 做补全，把常用的排前面。

    这只是**减少**领域名混乱，不能消除——用户照样可以无视提示硬打一个新写法。
    彻底解决需要建领域字典表，已否决（理由见 D-031）。
    """
    rows = session.execute(
        select(Knowledge.domain, func.count(Knowledge.id))
        .where(Knowledge.domain.is_not(None))
        .where(cast(Knowledge.domain, String) != "")
        .group_by(Knowledge.domain)
        .order_by(func.count(Knowledge.id).desc(), Knowledge.domain)
        .limit(limit)
    ).all()
    return [(domain, int(count)) for domain, count in rows]


def find_similar_titles(session: Session, title: str, *, limit: int = 5) -> list[str]:
    """按标题找可能重复的知识，用于新建时提示"你是不是已经记过了"。

    刻意用 LIKE 而不是 FTS：这个提示对 2 字标题也要有效，
    而且它不是主搜索路径，简单可靠比快更重要。
    """
    cleaned = title.strip()
    if len(cleaned) < 2:
        return []
    pattern = _like_pattern(cleaned)
    rows = session.execute(
        select(Knowledge.title)
        .where(Knowledge.title.like(pattern, escape="\\"))
        .where(Knowledge.status != "archived")
        .limit(limit)
    ).scalars()
    return list(rows)


__all__ = [
    "FTS_MIN_LENGTH",
    "FTS_TABLE",
    "find_similar_titles",
    "index_stats",
    "rebuild_index",
    "remove_from_index",
    "search",
    "suggest_domains",
    "sync_index",
]
