/**
 * 后端响应的 zod 校验模型与取值词表。
 *
 * ## 为什么前端还要再校验一遍
 *
 * 后端已经有 Pydantic 了，但前端拿到的是**跨进程传来的 JSON**，中间隔着
 * 网络、Vite 代理和 React Query 缓存。字段改名或类型变了而没有这层校验，
 * 表现会是页面上某处莫名变成 `undefined`、或者 `.map is not a function`，
 * 排查起来很费劲。有了它，问题会直接说清楚是哪个字段对不上。
 *
 * ## ⚠️ 这里的词表是第三份拷贝
 *
 * 同一套取值在三个地方各写了一份：Python 模型常量、Python Pydantic schema、
 * 这里。TypeScript 的 `as const` 数组没法从 Python 生成，重复是代价。
 *
 * 漂移由后端测试兜住：`tests/test_schemas.py` 断言 Python 那两份一致，
 * `tests/test_knowledge_api.py` 断言 OpenAPI 里的枚举等于模型常量。
 * 这一份如果落后了，表现为解析失败并给出可读报错——**不会静默出错**。
 */

import { z } from "zod"

/**
 * 后端 UTCDateTime 序列化成 `2026-10-31T11:08:03.623533Z`。
 * 放开 offset 和 local 是为了将来序列化格式微调时不至于整个页面白屏。
 */
const datetime = z.iso.datetime({ offset: true, local: true })

// ===== 来源类型（sources.source_type，8 种）=====

export const SOURCE_KINDS = [
  "book",
  "article",
  "video",
  "course",
  "podcast",
  "paper",
  "conversation",
  "other",
] as const
export type SourceKind = (typeof SOURCE_KINDS)[number]

export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  book: "书籍",
  article: "文章",
  video: "视频",
  course: "课程",
  podcast: "播客",
  paper: "论文",
  conversation: "对话",
  other: "其他",
}

// ===== 知识来源方式（knowledge.source_type，2 种）=====
//
// ⚠️ 和上面那套是**两个不同的概念**，两套里都有 "conversation" 但含义不同：
//   SOURCE_KINDS 里的 conversation  = 这份资料本身是一场对话
//   KNOWLEDGE_SOURCE_TYPES 里的 conversation = 这条知识是在 AI 学习室里产生的

export const KNOWLEDGE_SOURCE_TYPES = ["manual", "conversation"] as const
export type KnowledgeSourceType = (typeof KNOWLEDGE_SOURCE_TYPES)[number]

export const KNOWLEDGE_SOURCE_TYPE_LABELS: Record<KnowledgeSourceType, string> = {
  manual: "自己记录",
  conversation: "AI 学习室中产生",
}

// ===== 状态与版本作者 =====

export const KNOWLEDGE_STATUSES = ["active", "archived"] as const
export type KnowledgeStatus = (typeof KNOWLEDGE_STATUSES)[number]

export const STATUS_LABELS: Record<KnowledgeStatus, string> = {
  active: "在用",
  archived: "已归档",
}

/**
 * 版本作者。词表里**没有 `ai`**：AI 不能直接创建版本，
 * 所有入库路径都必须经过一次用户确认（Blueprint 1.5 原则 2）。
 * 前端必须把这两种来源在界面上明确区分开，不能混着显示。
 */
export const CREATED_BY_VALUES = ["user", "ai_confirmed"] as const
export type CreatedBy = (typeof CREATED_BY_VALUES)[number]

export const CREATED_BY_LABELS: Record<CreatedBy, string> = {
  user: "我自己写的",
  ai_confirmed: "我确认采纳了 AI 的建议",
}

export const MATCHED_BY_LABELS = {
  title: "命中标题",
  body: "命中正文",
  both: "标题和正文都命中",
} as const

// ===== 响应模型 =====

export const sourceReadSchema = z.object({
  id: z.string(),
  source_type: z.enum(SOURCE_KINDS),
  title: z.string(),
  url_or_reference: z.string().nullable(),
  note: z.string().nullable(),
  created_at: datetime,
})
export type SourceRead = z.infer<typeof sourceReadSchema>

export const knowledgeVersionSchema = z.object({
  id: z.string(),
  version_number: z.number().int(),
  user_understanding: z.string(),
  reasoning: z.string().nullable(),
  open_questions: z.string().nullable(),
  /** AI 的修订建议与用户原文分列存储，界面上必须分开显示（Blueprint 原则 2） */
  ai_revision: z.string().nullable(),
  change_reason: z.string().nullable(),
  created_by: z.enum(CREATED_BY_VALUES),
  created_at: datetime,
})
export type KnowledgeVersion = z.infer<typeof knowledgeVersionSchema>

export const knowledgeReadSchema = z.object({
  id: z.string(),
  title: z.string(),
  domain: z.string().nullable(),
  summary: z.string().nullable(),
  source_type: z.enum(KNOWLEDGE_SOURCE_TYPES),
  status: z.enum(KNOWLEDGE_STATUSES),
  created_at: datetime,
  updated_at: datetime,
  current_version_id: z.string().nullable(),
  sources: z.array(sourceReadSchema),
  versions: z.array(knowledgeVersionSchema),
})
export type KnowledgeRead = z.infer<typeof knowledgeReadSchema>

export const knowledgeListItemSchema = z.object({
  id: z.string(),
  title: z.string(),
  domain: z.string().nullable(),
  summary: z.string().nullable(),
  source_type: z.enum(KNOWLEDGE_SOURCE_TYPES),
  status: z.enum(KNOWLEDGE_STATUSES),
  created_at: datetime,
  updated_at: datetime,
  version_count: z.number().int(),
  current_understanding: z.string().nullable(),
})
export type KnowledgeListItem = z.infer<typeof knowledgeListItemSchema>

export const knowledgePageSchema = z.object({
  items: z.array(knowledgeListItemSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
})
export type KnowledgePage = z.infer<typeof knowledgePageSchema>

export const searchResultItemSchema = z.object({
  knowledge_id: z.string(),
  title: z.string(),
  domain: z.string().nullable(),
  status: z.enum(KNOWLEDGE_STATUSES),
  matched_by: z.enum(["title", "body", "both"]),
  snippet: z.string().nullable(),
})
export type SearchResultItem = z.infer<typeof searchResultItemSchema>

export const searchResultSchema = z.object({
  items: z.array(searchResultItemSchema),
  total: z.number().int(),
  query: z.string(),
  /** 后端实际走了哪条检索路径，界面上要说明结果来源 */
  strategy: z.enum(["fts", "like"]),
})
export type SearchResult = z.infer<typeof searchResultSchema>

export const domainSuggestionSchema = z.object({
  domain: z.string(),
  count: z.number().int(),
})
export type DomainSuggestion = z.infer<typeof domainSuggestionSchema>

// ===== 派生工具 =====

/**
 * 当前生效的版本。
 *
 * ⚠️ 必须用 `current_version_id` 定位，**不能取版本号最大的那条**。
 * 后者是个隐含假设，一旦将来支持"恢复到旧版本"就会失效。
 */
export function currentVersionOf(
  knowledge: KnowledgeRead,
): KnowledgeVersion | undefined {
  return knowledge.versions.find((v) => v.id === knowledge.current_version_id)
}

/** 版本号最大的那条，仅用于算"下一个版本号是几"。 */
export function latestVersionNumberOf(knowledge: KnowledgeRead): number {
  return knowledge.versions.reduce((max, v) => Math.max(max, v.version_number), 0)
}

export const emptySearchResult: SearchResult = {
  items: [],
  total: 0,
  query: "",
  strategy: "like",
}
