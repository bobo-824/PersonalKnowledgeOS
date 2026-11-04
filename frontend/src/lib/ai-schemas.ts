/**
 * AI 接口响应的 zod 校验模型（与 backend/schemas/ai_api.py 一一对应）。
 *
 * ## 词表是又一份拷贝
 *
 * `REVIEW_CATEGORIES` / `REVIEW_DECISIONS` 在后端已经有两份（模型常量 + Literal），
 * 这里是前端这份。漂移不会静默出错：后端返回陌生值时这里解析失败并给出可读报错。
 *
 * ## C1 的显示层约定
 *
 * `user_understanding`（用户原文）与 `ai_supplements`（AI 补充）是两个独立字段，
 * 界面上**永远分开显示**，也永远不能有"程序把 AI 的话拼进用户原文"的按钮——
 * 要采纳某条补充，必须由用户自己打字写进理解正文。
 */

import { z } from "zod"

import { knowledgeReadSchema, SOURCE_KINDS } from "@/lib/schemas"

const datetime = z.iso.datetime({ offset: true, local: true })

// ===== 会话与消息 =====

export const aiMessageSchema = z.object({
  id: z.string(),
  seq: z.number().int(),
  role: z.string(),
  content: z.string(),
  model: z.string().nullable(),
  prompt_tokens: z.number().int().nullable(),
  completion_tokens: z.number().int().nullable(),
  duration_ms: z.number().int().nullable(),
  /** 非 null = 失败占位消息：content 是后端写给用户看的话，不是 AI 原文 */
  error_kind: z.string().nullable(),
  created_at: datetime,
})
export type AiMessage = z.infer<typeof aiMessageSchema>

export const conversationSchema = z.object({
  id: z.string(),
  title: z.string(),
  created_at: datetime,
  updated_at: datetime,
})
export type Conversation = z.infer<typeof conversationSchema>

export const conversationDetailSchema = conversationSchema.extend({
  messages: z.array(aiMessageSchema),
})
export type ConversationDetail = z.infer<typeof conversationDetailSchema>

export const historyClearedSchema = z.object({
  deleted_conversations: z.number().int(),
})
export type HistoryCleared = z.infer<typeof historyClearedSchema>

// ===== 知识草稿（工作流 A 的产物）=====

export const sourceDraftSchema = z.object({
  source_type: z.enum(SOURCE_KINDS),
  title: z.string(),
  url_or_reference: z.string().nullable(),
  note: z.string().nullable(),
})
export type SourceDraft = z.infer<typeof sourceDraftSchema>

export const knowledgeDraftSchema = z.object({
  title: z.string(),
  domain: z.string().nullable(),
  summary: z.string().nullable(),
  /** 用户原文。AI 提取时也只是誊写，一个字都不能替用户改（C1）。 */
  user_understanding: z.string(),
  /** AI 补充。界面上与用户原文分开显示。 */
  ai_supplements: z.array(z.string()),
  reasoning: z.string().nullable(),
  open_questions: z.string().nullable(),
  suggested_sources: z.array(sourceDraftSchema),
})
export type KnowledgeDraft = z.infer<typeof knowledgeDraftSchema>

export const draftConfirmResultSchema = z.object({
  knowledge: knowledgeReadSchema,
  /** true = 这个幂等键之前已经创建过，返回的是当时那条，没有新增记录 */
  replayed: z.boolean(),
})
export type DraftConfirmResult = z.infer<typeof draftConfirmResultSchema>

// ===== 知识审查（工作流 C）=====

export const REVIEW_CATEGORIES = [
  "correct_points",
  "errors",
  "omissions",
  "follow_up_questions",
  "needs_verification",
] as const
export type ReviewCategory = (typeof REVIEW_CATEGORIES)[number]

export const REVIEW_CATEGORY_LABELS: Record<ReviewCategory, string> = {
  correct_points: "你说对的部分",
  errors: "需要纠正的理解",
  omissions: "可能遗漏的角度",
  follow_up_questions: "留给你的追问",
  needs_verification: "建议你亲自核实",
}

export const REVIEW_DECISIONS = ["accepted", "ignored", "disagreed"] as const
export type ReviewDecision = (typeof REVIEW_DECISIONS)[number]

export const REVIEW_DECISION_LABELS: Record<ReviewDecision, string> = {
  accepted: "采纳",
  ignored: "忽略",
  disagreed: "不同意",
}

export const reviewResultSchema = z.object({
  correct_points: z.array(z.object({ point: z.string(), why: z.string() })),
  errors: z.array(
    z.object({ claim: z.string(), correction: z.string(), evidence: z.string() }),
  ),
  omissions: z.array(
    z.object({ missing: z.string(), why_it_matters: z.string() }),
  ),
  follow_up_questions: z.array(z.string()),
  needs_verification: z.array(
    z.object({ item: z.string(), how_to_verify: z.string() }),
  ),
})
export type ReviewResult = z.infer<typeof reviewResultSchema>

export const reviewDecisionReadSchema = z.object({
  category: z.string(),
  item_index: z.number().int(),
  decision: z.enum(REVIEW_DECISIONS),
  updated_at: datetime,
})
export type ReviewDecisionRead = z.infer<typeof reviewDecisionReadSchema>

export const reviewSchema = z.object({
  id: z.string(),
  knowledge_id: z.string(),
  version_id: z.string(),
  /** 审的是第几版。采纳后会生成新版本，这份审查仍然针对它审的那版。 */
  version_number: z.number().int(),
  model: z.string(),
  prompt_version: z.string(),
  created_at: datetime,
  prompt_tokens: z.number().int().nullable(),
  completion_tokens: z.number().int().nullable(),
  duration_ms: z.number().int().nullable(),
  result: reviewResultSchema,
  decisions: z.array(reviewDecisionReadSchema),
  /** 五个类别全空。显示"AI 没有提出意见"，不能显示成空白或"你全对"。 */
  is_empty: z.boolean(),
})
export type Review = z.infer<typeof reviewSchema>

// ===== 设置页 =====

export const aiConfigViewSchema = z.object({
  provider: z.string(),
  base_url: z.string(),
  model: z.string(),
  timeout_seconds: z.number().int(),
  json_mode: z.boolean(),
  api_key_set: z.boolean(),
  /** 只有末 4 位，形如 …abcd。完整密钥永远不从后端回来（C6）。 */
  api_key_hint: z.string().nullable(),
  configured: z.boolean(),
  hint: z.string().nullable(),
  /** 被同名系统环境变量盖住的键：写 .env 不生效，必须明说。 */
  shadowed_keys: z.array(z.string()),
})
export type AiConfigView = z.infer<typeof aiConfigViewSchema>

export const aiConfigUpdateResultSchema = z.object({
  config: aiConfigViewSchema,
  /** 实际写进 .env 的环境变量名，用户可以对着文件核对 */
  changed_keys: z.array(z.string()),
  backup_file: z.string().nullable(),
  takes_effect_now: z.boolean(),
  message: z.string(),
})
export type AiConfigUpdateResult = z.infer<typeof aiConfigUpdateResultSchema>

export const aiConnectionTestSchema = z.object({
  ok: z.boolean(),
  model: z.string(),
  duration_ms: z.number().int(),
  error_kind: z.string().nullable(),
  message: z.string(),
})
export type AiConnectionTest = z.infer<typeof aiConnectionTestSchema>

// ===== SSE 流事件 =====

export const sseEventSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("chunk"), text: z.string() }),
  z.object({ type: z.literal("done") }),
  z.object({
    type: z.literal("error"),
    error_kind: z.string(),
    message: z.string(),
  }),
])

/** AI 请求失败的五类词表（与 ai_messages.error_kind 一致），前端用来给建议文案。 */
export const AI_ERROR_KIND_HINTS: Record<string, string> = {
  unavailable: "AI 服务暂时联系不上，稍后再试一次。",
  auth_rejected: "服务商拒绝了密钥。请到设置页检查 API Key。",
  bad_request: "请求被服务商拒绝，多半是模型名或 API 地址写法不对。",
  schema_invalid: "AI 返回的格式不符合要求，已按约定丢弃，可以重试一次。",
  timeout: "AI 服务响应超时。可以在设置页把超时时间调大。",
}
