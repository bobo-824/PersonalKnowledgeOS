# DATA_MODEL —— 数据模型与字段说明

当前数据库版本：**0001**（阶段 0 已落地，字段为阶段 1 的知识库功能准备）。
表结构由 `migrations/versions/0001_stage1_knowledge_core.py` 定义，
ORM 模型在 `backend/db/models/`，两者必须保持一致
（用 `uv run alembic check` 验证，不一致会报漂移）。

> 为什么这份说明放这里而不是 `alembic.ini`：Alembic 用**操作系统区域编码**
> （中文 Windows 是 GBK）读 `alembic.ini`，那个文件里写中文会直接
> `UnicodeDecodeError` 崩溃。详见 `DECISIONS.md` D-008。

---

## 总览

```
knowledge (知识单元主记录)
   │  1
   │
   ├──< knowledge_versions (理解的历史版本，追加式，永不覆盖)
   │       每个知识单元有 1..N 个版本，version_number 从 1 递增
   │       knowledge.current_version_id ──┐ 循环外键
   │                                      └──> 指向"当前生效"的那一版
   │
   └──< sources (学习来源与引用)
           一个知识单元可以来自多个来源

knowledge_fts (FTS5 虚拟表，全文检索索引，独立表)
   索引 knowledge.title + knowledge_versions.user_understanding
```

**核心设计**：知识单元本身只存"元数据 + 指向当前版本的指针"，
**所有理解正文都在 `knowledge_versions` 里**。
这是"用户原始理解永不被覆盖"（Blueprint 1.5 / 7.1）在数据结构上的保证：
修改理解 = 追加一行新版本，而不是 UPDATE 旧行。旧版本物理上不可能被改掉。

---

## 表：`knowledge`

知识单元主记录。存元数据和"当前是哪一版"的指针，**不存理解正文**。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | UUID 字符串。用 `str(uuid4())` 生成，存字符串而非 BLOB 是为了导出 JSON 时不用额外转换 |
| `title` | VARCHAR(200) | NOT NULL | 知识名称。Blueprint 5.1 的必填字段 |
| `domain` | VARCHAR(100) | 可空 | 所属领域，如"机器学习"、"操作系统"。自由文本，不做外键——个人知识库的领域分类会一直变，建字典表只会增加维护负担 |
| `summary` | TEXT | 可空 | 一句话摘要，列表页展示用 |
| `source_type` | VARCHAR(32) | NOT NULL，默认 `manual` | 这条知识怎么来的。取值：`manual`（手动记录）/ `conversation`（AI 对话中提取，需用户确认）。常量定义在 `backend/db/models/knowledge.py` |
| `status` | VARCHAR(16) | NOT NULL，默认 `active` | `active` / `archived`。**归档而非物理删除**（Blueprint 5.1）：archived 的不出现在默认列表，但数据完整保留 |
| `current_version_id` | VARCHAR(36) | 可空，FK → `knowledge_versions.id` | 指向当前生效的版本。可空是因为插入时版本还不存在（见下方"循环外键"） |
| `created_at` | DATETIME | NOT NULL | UTC，见下方"时间字段" |
| `updated_at` | DATETIME | NOT NULL | UTC，SQLAlchemy `onupdate=utcnow` 自动维护 |

**索引**：

| 索引名 | 列 | 用途 |
|---|---|---|
| `ix_knowledge_domain` | `domain` | 按领域浏览（Blueprint 阶段 4 验收项） |
| `ix_knowledge_status_updated_at` | `status, updated_at` | 默认列表查询是 `WHERE status='active' ORDER BY updated_at DESC`，复合索引正好覆盖 |

### 循环外键（`knowledge` ⇄ `knowledge_versions`）

`knowledge.current_version_id` 引用 `knowledge_versions.id`，
而 `knowledge_versions.knowledge_id` 又引用 `knowledge.id`。两张表互相依赖，
建表时**无论先建哪张，另一张都还不存在**。

解决方案（迁移 0001 里的实际顺序）：

1. 建 `knowledge`，**暂不含** `current_version_id` 的外键约束（列本身要建）
2. 建 `knowledge_versions`，含指向 `knowledge.id` 的外键
3. 回填 `knowledge.current_version_id` 的外键约束

第 3 步在 SQLite 上必须走 **batch 模式**（`op.batch_alter_table`），
因为 SQLite 不支持 `ALTER TABLE ADD CONSTRAINT`。batch 模式的做法是
建新表 → 搬数据 → 删旧表 → 改名。此时表是空的，代价为零。

ORM 侧对应两个配置：

- `ForeignKey(..., use_alter=True)` —— 告诉 Alembic 这条外键要延后创建
- `relationship(..., post_update=True)` —— 告诉 SQLAlchemy 插入时先写 `knowledge`
  拿到 UUID，再回填 `current_version_id`，不要在一条 INSERT 里就要求值

**写代码时的注意点**（测试里踩过）：`Knowledge.id` 的 UUID 是
`default=new_uuid`，**只在 flush 时才生成**。所以

```python
session.add(knowledge)
version = KnowledgeVersion(knowledge_id=knowledge.id, ...)  # ✗ knowledge.id 还是 None
```

会报 `NOT NULL constraint failed`。必须先 `session.flush()`：

```python
session.add(knowledge)
session.flush()                                              # ✓ 现在 knowledge.id 有值了
version = KnowledgeVersion(knowledge_id=knowledge.id, ...)
```

---

## 表：`knowledge_versions`

用户理解的历史版本。**追加式**：新版本永远是新行，旧行不动。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | UUID 字符串 |
| `knowledge_id` | VARCHAR(36) | NOT NULL，FK → `knowledge.id` | 属于哪个知识单元 |
| `version_number` | INTEGER | NOT NULL | 从 1 递增。**与 `knowledge_id` 组成唯一约束** |
| `user_understanding` | TEXT | NOT NULL | **用户原文。任何流程（包括 AI）都不得 UPDATE 这一列。** Blueprint 7.1 的硬验收项 |
| `reasoning` | TEXT | 可空 | 理解依据："我为什么这么认为" |
| `open_questions` | TEXT | 可空 | 尚存疑问。阶段 2 的 AI 审查会重点看这一列 |
| `ai_revision` | TEXT | 可空 | **AI 的修订建议，与用户原文物理隔离。** 存下来但不会自动生效；用户确认采纳后，会**新建一个版本**把采纳结果写进新版本的 `user_understanding`，本列的原始建议保留不动 |
| `change_reason` | VARCHAR(200) | 可空 | 这次为什么改 |
| `created_by` | VARCHAR(16) | NOT NULL，默认 `user` | `user`（用户自己写）/ `ai_confirmed`（用户确认采纳 AI 建议后生成的新版本）。**注意没有 `ai` 这个取值**——AI 不能直接创建版本，这是原则在数据层的体现 |
| `created_at` | DATETIME | NOT NULL | UTC |

**约束与索引**：

| 名称 | 内容 | 为什么重要 |
|---|---|---|
| `uq_knowledge_version_number` | UNIQUE(`knowledge_id`, `version_number`) | 防止并发或重试造成版本号重复。测试用 `IntegrityError` 验证过 |
| `fk_knowledge_versions_knowledge_id` | FK → `knowledge.id` | 防止孤儿版本。配合 `PRAGMA foreign_keys=ON` 才真正生效（SQLite 默认**不**强制外键） |
| `ix_knowledge_versions_knowledge_id` | `knowledge_id` | 查某个知识单元的版本列表 |

### 版本演进的完整例子

```
用户第一次记录"梯度下降"：
  v1  user_understanding = "沿着斜率最大的方向往下走"
      reasoning          = " intuition，没验证过"
      ai_revision        = NULL
      created_by         = "user"

AI 审查后指出遗漏，用户采纳：
  v1  （完全不变，原文永久保留）
  v2  user_understanding = "沿着负梯度方向按学习率迭代更新参数"
      reasoning          = "AI 指出需要明确学习率和参数更新"
      ai_revision        = "建议补充：学习率、迭代更新、负梯度方向"
      change_reason      = "采纳 AI 审查建议"
      created_by         = "ai_confirmed"

  knowledge.current_version_id → v2.id
```

**关键点**：v1 的 `user_understanding` 依然是那句不准确的原始理解，
一个字都没变。这正是 Blueprint 7.1 要的东西——能看到"我当初是怎么想的"，
以及"我是怎么想的改变的"。

测试 `test_original_understanding_survives_new_version` 就是断言这件事。

### 什么时候才创建新版本（阶段 1 的实现约束）

**规则**：`user_understanding`、`reasoning`、`open_questions`
三者**任一实际值发生变化**时，才创建新版本行。

改 `title` / `domain` / `summary` / `sources` 属于整理性工作，
**就地 UPDATE，不建版本**。

理由与已知取舍见 `DECISIONS.md` D-030。两条实现上的硬要求：

1. **必须先比对实际值，不能无脑建版本。** 否则"用户点了保存但什么都没改"
   也会让版本号 +1，制造出假历史。
2. **`title` 不在版本表里，所以标题的演变历史留不下来。**
   这是用户明确知情并接受的取舍。如果日后要改，
   需要给 `knowledge_versions` 加标题快照列并写迁移——
   现在数据库 0 行，是最便宜的时机。

---

## 表：`sources`

学习来源与引用信息。一个知识单元可以有多个来源。

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | UUID 字符串 |
| `knowledge_id` | VARCHAR(36) | NOT NULL，FK → `knowledge.id`，索引 | 属于哪个知识单元 |
| `source_type` | VARCHAR(32) | NOT NULL，默认 `other` | 来源类型，**固定词表**：`book` 书 / `article` 文章 / `video` 视频 / `course` 课程 / `podcast` 播客 / `paper` 论文 / `conversation` 对话 / `other` 其他。见下方说明 |
| `title` | VARCHAR(300) | NOT NULL | 来源标题 |
| `url_or_reference` | VARCHAR(1000) | 可空 | 网址或书籍页码等引用位置 |
| `note` | TEXT | 可空，**但 `source_type='other'` 时必填** | 备注。选"其他"时必须写清楚到底是什么来源，否则信息就丢了 |
| `created_at` | DATETIME | NOT NULL | UTC |

> **两套不同的 source_type 词表，不要混用**：
> - `knowledge.source_type` 描述**这条知识怎么进系统的**：
>   `manual`（手动记录）/ `conversation`（AI 对话中提取，需用户确认）
> - `sources.source_type` 描述**资料本身是什么形式**：
>   书 / 文章 / 视频 / 课程 / 播客 / 论文 / 对话 / 其他
>
> 取值约束放在 Pydantic schema 层（`backend/schemas/knowledge.py`），
> 不做数据库级 CHECK 约束，理由见 `DECISIONS.md` D-005 / D-029。
>
> **已知待办**：`sources.source_type` 的默认值 `"other"` 目前是硬编码字符串，
> 阶段 1 要像 `knowledge.source_type` 那样定义成模块级常量
> （`SOURCE_TYPE_BOOK` 等），并在 schema 层加"other 时 note 必填"的跨字段校验。

---

## 虚拟表：`knowledge_fts`（FTS5 全文检索）

```sql
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    knowledge_id UNINDEXED,
    title,
    body,
    tokenize='trigram'
);
```

| 列 | 说明 |
|---|---|
| `knowledge_id` | `UNINDEXED`：存着用于回表，但不参与分词检索 |
| `title` | 来自 `knowledge.title` |
| `body` | 来自**当前版本**的 `knowledge_versions.user_understanding` |

### 为什么是独立表而不是"外部内容表 + 触发器" ⚠️

**这一条偏离了最初向用户宣布的方案**，完整理由见 `DECISIONS.md` D-006。

简述：FTS5 的外部内容表（`content='<表名>'`）只能绑定**单一**表，
而这里要索引的内容横跨两张表——`knowledge.title` 和
`knowledge_versions.user_understanding`。所以只能用独立表。

**代价**：同步逻辑必须显式写在 `services/search_service.py` 里，
写入路径漏调同步函数就会索引不一致。阶段 1 必须有测试覆盖：
"改标题后能搜到新标题、搜不到旧标题"。

### 影子表

FTS5 会自动创建 5 张影子表：`knowledge_fts_data`、`knowledge_fts_idx`、
`knowledge_fts_content`、`knowledge_fts_docsize`、`knowledge_fts_config`。
它们**不在 ORM metadata 里**，所以 `migrations/env.py` 的 `_include_object`
过滤器按前缀 `knowledge_fts` 排除掉，否则 `alembic check` 会永远认为
这些表是多余的并要求 drop。

### 中文检索：trigram + LIKE 混合策略

`tokenize='trigram'` 是 SQLite 内置分词器里唯一对中文有效的，
但 **MATCH 查询要求至少 3 个字符**，2 字词直接返回空结果。
而「梯度」「复习」「熵」这类 1~2 字词在中文笔记里极其常见。

策略（阶段 1 在 `search_service` 实现）：

| 查询长度 | 走哪条路 | 实测性能 |
|---|---|---|
| ≥ 3 字符 | FTS5 `MATCH` | 索引命中，快 |
| 1~2 字符 | `LIKE '%xx%'` | 5000 条数据下 **1.0~1.4ms** |

两条路径结果做并集去重。实测结果一致。
完整理由见 `DECISIONS.md` D-007。

---

## 跨表约定

### 时间字段：一律 UTC，写库时拒绝 naive datetime

所有 `*_at` 列都用 `UTCDateTime`（`backend/db/base.py` 的 TypeDecorator）：

- **写入**：aware datetime → 归一化成 UTC → 去掉 tzinfo 落库（SQLite 不存时区）
- **读取**：补上 `timezone.utc`，返回 aware datetime
- **写入 naive datetime → 抛 `ValueError`**："拒绝写入不带时区的时间。请使用
  `backend.db.base.utcnow()` 生成 UTC 时间。"

全项目唯一的时间来源是 `utcnow()`，**不要用 `datetime.now()`**。

这道硬约束是为了防住 Blueprint 9.2 的"系统时间变化、跨日和时区转换
不会导致复习日期错乱"。在写库那一刻报错，比等到阶段 3 的复习日期
算错一天再回头查，成本低一个数量级。

**测试注意点**：SQLAlchemy 会把这个 ValueError 包成 `StatementError`。
断言要写：

```python
with pytest.raises(StatementError, match="不带时区") as excinfo:
    ...
assert isinstance(excinfo.value.__cause__, ValueError)
```

### 主键：UUID 字符串

用 `str(uuid4())` 存 VARCHAR(36)，不用自增整数。理由：

- 阶段 5 的结构化导出/导入不会因为 ID 冲突而撞车
- 前端 URL 里的 `/knowledge/:knowledgeId` 用 UUID 不会泄露"你一共有多少条知识"
- 本地应用不需要自增 ID 的紧凑性

### 枚举：字符串常量而非 SQLAlchemy Enum

所有"取值有限"的列都是 `String` + 模块级常量，校验放 Pydantic schema 层。
理由：SQLite 的 Enum 落成 CHECK 约束，而 SQLite **不能 ALTER TABLE 修改 CHECK**，
以后加取值只能整表重建。见 `DECISIONS.md` D-005。

### 删除策略：归档优先

Blueprint 5.1 明确"归档而非物理删除"。`knowledge.status = 'archived'`，
数据完整保留，默认列表不显示。

ORM 里的 `cascade="all, delete-orphan"` 只用于**对象图内部一致性**
（比如替换整个 versions 列表时清理被移除的行），
**不代表业务上允许物理删除知识单元**。阶段 1 的 service 层不应该暴露
硬删除接口。

---

## 后续阶段的表（尚未创建）

以下是 Blueprint 5.2~5.4 规划、但**当前数据库里还不存在**的表。
列在这里是为了说明字段设计的连贯性，不要误以为已经实现。

| 表 | 阶段 | 关键字段 | 已知风险 |
|---|---|---|---|
| `ai_conversations` / `ai_messages` | 2 | 对话与消息，AI 反馈独立存储 | 结构化输出必须过后端 Schema 校验 |
| `review_schedules` | 3 | FSRS 状态：`stability`、`difficulty`、`due`、`last_review`、`state` | **见下方警告** |
| `review_sessions` | 3 | 问题、回答、AI 评价、AI 建议评级、**用户最终评级**、耗时 | 用户评级是权威，AI 只是建议 |
| `knowledge_relations` | 4 | 关系类型、解释、依据、置信度、`status`(pending/accepted/rejected) | 图谱只查 accepted |

### ⚠️ 阶段 3 的 FSRS 字段不匹配（提前记录）

`fsrs` 6.3.2 的 `Card` 只有这些字段：

```
card_id, state, step, stability, difficulty, due, last_review
```

而 Blueprint 的 `review_schedules` 还列了
**`reps`、`lapses`、`elapsed_days`、`scheduled_days`**。
这四个字段 FSRS 库**不提供**，必须由 `review_sessions` 的历史记录推算：

- `reps` = 该知识单元的 `review_sessions` 行数
- `lapses` = 其中评级为 `again` 的行数
- `elapsed_days` = 本次 `reviewed_at` − 上次 `reviewed_at`
- `scheduled_days` = 本次 `due` − 上次 `reviewed_at`

另外测试**必须**用 `Scheduler(enable_fuzzing=False)`，
否则 FSRS 会给复习间隔加随机扰动，结果不确定、无法断言。

---

## 数据文件与备份

| 文件 | 说明 |
|---|---|
| `data/app.db` | 主数据库 |
| `data/app.db-wal` | WAL 预写日志，**未落盘的数据在这里** |
| `data/app.db-shm` | WAL 共享内存索引 |
| `data/backups/` | 备份输出目录（阶段 5 实现） |

数据库位置由 `PKOS_DATA_DIR` 决定，默认 `<项目根>/data`。
测试通过设置 `PKOS_DATA_DIR` 指向 `tmp_path` 实现完全隔离。

### ⚠️ 备份绝不能手动复制 `app.db`

开了 WAL 之后，最近的数据可能还在 `app.db-wal` 里没合并进主文件。
只复制 `app.db` 会拿到**不完整甚至不一致**的库。

正确做法是 `VACUUM INTO`（阶段 5 会包装成命令）：

```sql
VACUUM INTO 'data/backups/app-2026-10-31.db';
```

它会先合并 WAL，再输出一个自洽的独立文件。

**运行时的连接设置**（`backend/db/session.py`）：

| PRAGMA | 值 | 为什么 |
|---|---|---|
| `foreign_keys` | `ON` | SQLite **默认不强制外键**。不开这个，级联删除和 FK 约束形同虚设，孤儿数据静默产生 |
| `journal_mode` | `WAL` | 读写不互相阻塞 |
| `busy_timeout` | `5000` | 遇到锁时等 5 秒而不是立刻抛 `database is locked` |

---

## 迁移操作速查

```bash
uv run alembic upgrade head      # 升到最新
uv run alembic current           # 看当前版本
uv run alembic check             # 检查 ORM 模型与迁移是否漂移
uv run alembic history           # 看迁移链
uv run alembic downgrade base    # 全部回滚（危险，会删表）
```

`alembic.ini` 的 `sqlalchemy.url` 是**故意留空**的，
实际 URL 由 `migrations/env.py` 从 `backend.config` 读取，
保证端口、数据目录、凭据只有一个来源（见 `DECISIONS.md` D-010）。

**新增迁移的约定**：

- revision id 用四位数字递增（`0001`、`0002`…），不用 Alembic 默认的随机哈希——
  单线程开发不需要处理分支合并，顺序号更易读
- 涉及 FTS5 或改约束的迁移**手写**，不用 `--autogenerate`
  （autogenerate 完全不认识 FTS5 虚拟表）
- 改列、加约束一律走 `op.batch_alter_table`（SQLite 限制）
- 写完必须跑 `uv run pytest tests/test_migrations.py`，
  那个文件里有 downgrade→base 再 upgrade→head 的往返测试
