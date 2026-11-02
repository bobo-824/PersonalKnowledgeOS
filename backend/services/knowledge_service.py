"""知识单元的应用服务：CRUD、归档、版本演进。

**事务边界在这一层**：一个业务动作 = 一个函数 = 一次 commit。
API 层只负责解析请求和映射状态码，不开事务（见 docs/ARCHITECTURE.md）。

## 两条贯穿全文件的硬规则

**规则一：用户原文永不被覆盖（Blueprint 1.5 原则 2）**

`knowledge_versions` 是追加式表。修改理解 = 新增一行，旧行永远不动。
AI 的建议只能写进 `ai_revision` 列，`user_understanding` 不接受来自 AI 路径的 UPDATE。
`created_by` 只有 `user` 和 `ai_confirmed` 两种取值，**没有 `ai`**。

**规则二：只有理解真的变了才建新版本（DECISIONS D-030）**

改标题、领域、摘要、来源属于整理性工作，就地 UPDATE，不产生版本。
否则"用户点了保存但什么都没改"也会让版本号 +1，制造出假历史。
判定逻辑委托给 `backend/domain/versioning.py`，本文件不自己比对字段。

## ⚠️ FTS5 索引必须显式同步

`knowledge_fts` 是独立表，不会自动跟着数据变（DECISIONS D-006）。
**任何改动了 title 或当前版本 user_understanding 的写入路径都必须调
`search_service.sync_index()`**，否则会出现"改了标题却还搜到旧标题"。
本文件里所有 commit 之前都会同步。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.db.models import (
    CREATED_BY_USER,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    Knowledge,
    KnowledgeVersion,
    Source,
)
from backend.domain.versioning import (
    Understanding,
    UnderstandingLike,
    changed_fields,
    next_version_number,
    understanding_changed,
)
from backend.schemas.knowledge import (
    KnowledgeCreate,
    KnowledgeListItem,
    KnowledgeUpdate,
)
from backend.services.search_service import sync_index

logger = logging.getLogger(__name__)

# 自动生成 change_reason 时用的中文字段名。
# 存进数据库而不是只用于展示，是因为版本历史要能自我解释：
# 半年后翻到某一条版本，应该能直接看出"这次改的是理解还是疑问"。
FIELD_LABELS = {
    "user_understanding": "自己的理解",
    "reasoning": "理解依据",
    "open_questions": "尚存疑问",
}

# 就地更新、不触发新版本的字段（D-030）
IN_PLACE_FIELDS = ("title", "domain", "summary")


class KnowledgeNotFound(Exception):
    """知识单元不存在。API 层映射成 404。"""

    def __init__(self, knowledge_id: str) -> None:
        self.knowledge_id = knowledge_id
        super().__init__(f"找不到知识单元：{knowledge_id}")


def _require(session: Session, knowledge_id: str) -> Knowledge:
    """取一条知识，带上详情需要的关联。不存在则抛 KnowledgeNotFound。

    ⚠️ `populate_existing` 是必需的，不是优化。

    会话配了 `expire_on_commit=False`（commit 后对象不过期，省一次重查），
    副作用是：如果对象已经在身份映射里、集合属性也已加载，
    那么**再次 selectinload 不会刷新它**，读到的是旧快照。
    实测现象是"更新后版本数还是 1"，而数据库里其实已经有 2 行了。

    生产环境每个请求一个新 session，身份映射是空的，不会触发这个问题；
    但同一 session 内先写后读（以及所有用共享 db_session 夹具的测试）会中招。
    `populate_existing` 强制用查询结果覆盖内存里的旧状态。
    """
    knowledge = session.execute(
        select(Knowledge)
        .options(
            selectinload(Knowledge.current_version),
            selectinload(Knowledge.versions),
            selectinload(Knowledge.sources),
        )
        .execution_options(populate_existing=True)
        .where(Knowledge.id == knowledge_id)
    ).scalar_one_or_none()
    if knowledge is None:
        raise KnowledgeNotFound(knowledge_id)
    return knowledge


def _describe_change(fields: tuple[str, ...]) -> str:
    """把变化的字段名转成一句人话，用于自动填 change_reason。"""
    names = "、".join(FIELD_LABELS.get(f, f) for f in fields)
    return f"修改了：{names}"


# ===== 创建 =====


def create_knowledge(session: Session, data: KnowledgeCreate) -> Knowledge:
    """新建知识单元，同时创建第 1 个版本。

    ⚠️ 必须先 flush 才能拿到 UUID：`Knowledge.id` 的 `default=new_uuid`
    只在 flush 时触发。在 flush 之前读 `knowledge.id` 得到的是 None，
    拿它去建版本会报 `NOT NULL constraint failed`（这个坑在阶段 0 踩过）。
    """
    knowledge = Knowledge(
        title=data.title,
        domain=data.domain,
        summary=data.summary,
        source_type=data.source_type,
        status=STATUS_ACTIVE,
    )
    session.add(knowledge)
    session.flush()

    version = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=1,
        user_understanding=data.user_understanding,
        reasoning=data.reasoning,
        open_questions=data.open_questions,
        change_reason=data.change_reason or "初次记录",
        created_by=CREATED_BY_USER,
    )
    session.add(version)
    session.flush()

    # 回填循环外键。此时两张表的行都已存在，约束可以满足。
    knowledge.current_version_id = version.id
    session.flush()

    for source in data.sources:
        session.add(
            Source(
                knowledge_id=knowledge.id,
                source_type=source.source_type,
                title=source.title,
                url_or_reference=source.url_or_reference,
                note=source.note,
            )
        )

    # current_version 关系此时可以正常惰性加载，sync_index 要读它的正文
    sync_index(session, knowledge)
    session.commit()
    # 日志只记 id，不记标题和正文（Blueprint 9.1：日志不得记录不必要的私人知识正文）
    logger.info("已创建知识单元 %s", knowledge.id)
    # 重新加载后再返回：commit 后内存里的对象状态可能不完整（见 _require 的说明）
    return _require(session, knowledge.id)


# ===== 读取 =====


def get_knowledge(session: Session, knowledge_id: str) -> Knowledge:
    """取详情，含全部版本历史与来源。"""
    return _require(session, knowledge_id)


def list_knowledge(
    session: Session,
    *,
    status: str | None = STATUS_ACTIVE,
    domain: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[KnowledgeListItem], int]:
    """分页列出知识单元，返回（当页条目，符合条件的总条数）。

    `status=None` 表示不过滤（连已归档的一起列）。默认只列 active，
    因为归档项按 Blueprint 5.1 不该出现在默认列表里。

    **查询次数固定为 4，与页大小无关**（实测值，`tests/test_knowledge_service.py` 锁死）：
    总数统计、主查询、`current_version` 预加载、版本号分组统计。
    不这么做的话，逐行访问 `k.versions` 会产生 N+1 查询，
    列表页翻到 50 条就是 100 多次查询。结果为空时后两次自动跳过，只剩 2 次。
    """
    conditions = []
    if status is not None:
        conditions.append(Knowledge.status == status)
    if domain:
        conditions.append(Knowledge.domain == domain)

    base = select(Knowledge)
    if conditions:
        base = base.where(*conditions)

    total = session.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()

    rows = session.execute(
        base.options(selectinload(Knowledge.current_version))
        # 同 _require：避免身份映射里的旧快照盖掉刚查出来的新数据
        .execution_options(populate_existing=True)
        # 排序必须稳定：updated_at 相同的两条如果顺序不定，分页会重复或漏项，
        # 所以补一个 id 作为次级排序键。
        .order_by(Knowledge.updated_at.desc(), Knowledge.id)
        .limit(limit)
        .offset(offset)
    ).scalars().all()

    counts: dict[str, int] = {}
    if rows:
        ids = [k.id for k in rows]
        counts = dict(
            session.execute(
                select(
                    KnowledgeVersion.knowledge_id, func.count(KnowledgeVersion.id)
                )
                .where(KnowledgeVersion.knowledge_id.in_(ids))
                .group_by(KnowledgeVersion.knowledge_id)
            ).all()
        )

    items = [
        KnowledgeListItem(
            id=k.id,
            title=k.title,
            domain=k.domain,
            summary=k.summary,
            source_type=k.source_type,
            status=k.status,
            created_at=k.created_at,
            updated_at=k.updated_at,
            version_count=int(counts.get(k.id, 0)),
            current_understanding=(
                k.current_version.user_understanding if k.current_version else None
            ),
        )
        for k in rows
    ]
    return items, int(total)


# ===== 更新 =====


def append_version(
    session: Session,
    knowledge: Knowledge,
    current: UnderstandingLike,
    effective: Understanding,
    *,
    change_reason: str | None,
    created_by: str,
    ai_revision: str | None = None,
) -> KnowledgeVersion | None:
    """理解真的变了才追加新版本；没变返回 None。

    ⚠️ **这段逻辑只允许有一份。** 手动编辑（`update_knowledge`）和
    采纳 AI 审查建议（`ai_service.accept_ai_review`）都要追加版本，
    而这里是全项目最容易写错的两行：

    1. **必须同时赋值外键和关系属性**，不能只改 `current_version_id`。
       `_require` 已经把 `current_version` 预加载进内存（指向旧版本），
       而会话配了 `expire_on_commit=False`，只改外键不会让已加载的关系失效。
       后果是紧接着的 `sync_index` 读到**旧版本**，把过时正文写进索引——
       表现为"改了理解，但搜索还是命中旧内容"。实测踩过。
    2. **FTS5 索引必须显式同步**（D-006）。`knowledge_fts` 是独立表，
       不会自动跟着数据变。

    两处都有专门的测试和变异测试盯着（见 D-037）。
    复制第二份就等于给这两个坑多一个被漏掉的机会，所以抽成一个函数。

    调用方负责 `sync_index` 和 `commit`：索引同步的次数等于事务数，
    而不是"改了几处"，免得同一事务里同步两遍。
    """
    if not understanding_changed(current, effective):
        return None

    diff = changed_fields(current, effective)
    version = KnowledgeVersion(
        knowledge_id=knowledge.id,
        version_number=next_version_number(
            v.version_number for v in knowledge.versions
        ),
        user_understanding=effective.user_understanding or "",
        reasoning=effective.reasoning,
        open_questions=effective.open_questions,
        change_reason=change_reason or _describe_change(diff),
        created_by=created_by,
        ai_revision=ai_revision,
    )
    session.add(version)
    session.flush()

    knowledge.current_version_id = version.id
    knowledge.current_version = version
    session.flush()

    logger.info(
        "知识单元 %s 理解发生变化，创建版本 v%d（%s，来源=%s）",
        knowledge.id,
        version.version_number,
        version.change_reason,
        created_by,
    )
    return version


def update_knowledge(
    session: Session, knowledge_id: str, data: KnowledgeUpdate
) -> Knowledge:
    """更新知识单元。理解类字段真的变了才创建新版本。

    靠 `data.model_fields_set` 判断哪些字段是用户显式提交的。
    **不能用 `model_dump()` 后检查 None**：那样无法区分
    「用户没传这个字段」和「用户想把它清空」，两者 dump 出来都是 None。
    """
    knowledge = _require(session, knowledge_id)
    provided = data.model_fields_set

    if not provided:
        # 什么都没提交，直接返回，不碰 updated_at，也不建版本
        return knowledge

    # ① 非理解字段：就地更新（D-030）
    for field in IN_PLACE_FIELDS:
        if field in provided:
            setattr(knowledge, field, getattr(data, field))

    # ② 来源：传了就是整体替换（空数组 = 清空全部来源），不传则保持原样
    if "sources" in provided:
        # delete-orphan 级联会在 flush 时真正删掉被移除的行
        knowledge.sources.clear()
        session.flush()
        for source in data.sources or []:
            knowledge.sources.append(
                Source(
                    knowledge_id=knowledge.id,
                    source_type=source.source_type,
                    title=source.title,
                    url_or_reference=source.url_or_reference,
                    note=source.note,
                )
            )

    # ③ 算出「应用这次更新之后，理解应该是什么」
    current = knowledge.current_version
    effective = Understanding(
        user_understanding=(
            data.user_understanding
            if "user_understanding" in provided
            else current.user_understanding
        ),
        reasoning=(
            data.reasoning if "reasoning" in provided else current.reasoning
        ),
        open_questions=(
            data.open_questions
            if "open_questions" in provided
            else current.open_questions
        ),
    )

    # ④ 只有真的变了才追加新版本（规则和两个已知坑都收在 append_version 里）
    append_version(
        session,
        knowledge,
        current,
        effective,
        change_reason=data.change_reason,
        created_by=CREATED_BY_USER,
    )

    # ⑤ 标题或正文都可能变了，索引必须同步（D-006）
    sync_index(session, knowledge)
    session.commit()
    # 重新加载：新创建的版本行不在原对象的 versions 集合里，
    # 直接返回会让调用方看到"版本数没变"的假象。
    return _require(session, knowledge.id)


# ===== 归档与恢复 =====
#
# 归档不是"编辑"，所以走独立接口而不是塞进 KnowledgeUpdate：
# 改状态不该触发版本判断，混在一起会让 update 逻辑多一条特例分支。
# 另外归档是"把数据从默认视野里移走"，误操作的代价比误改标题大得多，
# 独立接口能让前端把它做成需要确认的动作。


def archive_knowledge(session: Session, knowledge_id: str) -> Knowledge:
    """归档。**不物理删除**（Blueprint 5.1）：数据完整保留，只是默认列表不显示。"""
    knowledge = _require(session, knowledge_id)
    knowledge.status = STATUS_ARCHIVED
    # 索引保留：搜索接口按 status 过滤，恢复归档时不需要重建索引
    session.commit()
    logger.info("已归档知识单元 %s", knowledge_id)
    return _require(session, knowledge_id)


def restore_knowledge(session: Session, knowledge_id: str) -> Knowledge:
    """从归档恢复。"""
    knowledge = _require(session, knowledge_id)
    knowledge.status = STATUS_ACTIVE
    session.commit()
    logger.info("已恢复知识单元 %s", knowledge_id)
    return _require(session, knowledge_id)


__all__ = [
    "FIELD_LABELS",
    "IN_PLACE_FIELDS",
    "KnowledgeNotFound",
    "append_version",
    "archive_knowledge",
    "create_knowledge",
    "get_knowledge",
    "list_knowledge",
    "restore_knowledge",
    "update_knowledge",
]
