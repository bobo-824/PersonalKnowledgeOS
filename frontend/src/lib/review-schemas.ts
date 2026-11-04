/**
 * 复习接口的 zod 校验模型（与 backend/schemas/review_api.py 一一对应）。
 *
 * ## 词表第三份拷贝的老约定
 *
 * `RATINGS` / `REVIEW_MODES` / `ScheduleState` / `ScheduleStatus` 在后端
 * 各有两份（模型常量 + Literal），这里是前端这份。漂移不会静默出错：
 * 后端返回陌生值时这里解析失败并给出可读报错。
 *
 * ## C7 在显示层的落点
 *
 * AI 的 `suggested_rating` 只是建议：界面上必须让用户**亲自点四个按钮之一**，
 * 提交的 `rating` 永远来自用户的点击，AI 建议只进存档和排期旁的展示。
 */

import { z } from "zod"

const datetime = z.iso.datetime({ offset: true, local: true })

// ===== 词表 =====

export const RATINGS = ["again", "hard", "good", "easy"] as const
export type ReviewRating = (typeof RATINGS)[number]

/** D-061：大白话四级。按钮主文案 + 一行解释，让第一次用的人不用查文档。 */
export const RATING_LABELS: Record<ReviewRating, string> = {
  again: "忘了",
  hard: "困难",
  good: "良好",
  easy: "简单",
}

export const RATING_HINTS: Record<ReviewRating, string> = {
  again: "想不起来，需要从头再学",
  hard: "想起来得费劲",
  good: "想起来了，有点难度",
  easy: "脱口而出",
}

export const REVIEW_MODES = ["self_test", "ai_chat"] as const
export type ReviewMode = (typeof REVIEW_MODES)[number]

export const MODE_LABELS: Record<ReviewMode, string> = {
  self_test: "纯自测",
  ai_chat: "AI 对话",
}

export const SCHEDULE_STATES = ["learning", "review", "relearning"] as const
export type ScheduleState = (typeof SCHEDULE_STATES)[number]

export const STATE_LABELS: Record<ScheduleState, string> = {
  learning: "学习中",
  review: "复习中",
  relearning: "重新学",
}

export const SCHEDULE_STATUSES = ["active", "suspended"] as const
export type ScheduleStatus = (typeof SCHEDULE_STATUSES)[number]

// ===== 到期列表 =====

export const dueItemSchema = z.object({
  knowledge_id: z.string(),
  title: z.string(),
  domain: z.string().nullable(),
  due: datetime,
  state: z.enum(SCHEDULE_STATES),
  reps: z.number().int(),
})
export type DueItem = z.infer<typeof dueItemSchema>

export const dueListSchema = z.object({
  items: z.array(dueItemSchema),
  count: z.number().int(),
  generated_at: datetime,
})
export type DueList = z.infer<typeof dueListSchema>

export const dueCountSchema = z.object({
  count: z.number().int(),
  generated_at: datetime,
})
export type DueCount = z.infer<typeof dueCountSchema>

// ===== 排期与评级 =====

export const scheduleSchema = z.object({
  knowledge_id: z.string(),
  state: z.enum(SCHEDULE_STATES),
  step: z.number().int(),
  stability: z.number().nullable(),
  difficulty: z.number().nullable(),
  due: datetime,
  last_review: datetime.nullable(),
  status: z.enum(SCHEDULE_STATUSES),
})
export type Schedule = z.infer<typeof scheduleSchema>

export type RatePayload = {
  rating: ReviewRating
  mode: ReviewMode
  /** AI 建议只是存档，排期计算不读它（后端有测试锁定） */
  suggested_rating?: ReviewRating
  ai_comment?: string
  conversation_id?: string
  /** 前端从出题展示计时到提交；量不到就不传 */
  duration_ms?: number
}

export const derivedStatsSchema = z.object({
  reps: z.number().int(),
  lapses: z.number().int(),
  elapsed_days: z.number().int(),
  scheduled_days: z.number().int(),
})

export const reviewOutcomeSchema = z.object({
  knowledge_id: z.string(),
  rating: z.enum(RATINGS),
  mode: z.enum(REVIEW_MODES),
  state: z.enum(SCHEDULE_STATES),
  due: datetime,
  stats: derivedStatsSchema,
})
export type ReviewOutcome = z.infer<typeof reviewOutcomeSchema>

export const suspensionResultSchema = z.object({
  knowledge_id: z.string(),
  status: z.enum(SCHEDULE_STATUSES),
})

// ===== 历史 =====

export const reviewSessionRowSchema = z.object({
  id: z.string(),
  reviewed_at: datetime,
  rating: z.enum(RATINGS),
  mode: z.enum(REVIEW_MODES),
  /** 非 null 表示那次是 AI 对话式复习 */
  suggested_rating: z.enum(RATINGS).nullable(),
  ai_comment: z.string().nullable(),
  /** mode=ai_chat 而 conversation_id 为 null：问答原文已被用户清空 */
  conversation_id: z.string().nullable(),
  duration_ms: z.number().int().nullable(),
})
export type ReviewSessionRow = z.infer<typeof reviewSessionRowSchema>

// ===== AI 对话式复习（工作流 D）=====

export const reviewChatStartSchema = z.object({
  conversation_id: z.string(),
  title: z.string(),
  first_question: z.string(),
})
export type ReviewChatStart = z.infer<typeof reviewChatStartSchema>

export const ratingSuggestionSchema = z.object({
  /** C3 + C7：过词表校验，但只作为建议展示，用户不点就不生效 */
  suggested_rating: z.enum(RATINGS),
  evaluation: z.string(),
})
export type RatingSuggestion = z.infer<typeof ratingSuggestionSchema>
