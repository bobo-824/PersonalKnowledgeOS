/**
 * 与后端 `/api/review` 通信的出口。
 *
 * `request` 与 ai-api.ts 的 `requestAi` 是同一份写法（错误翻译、schema.parse）：
 * 那份没导出是因为它只服务 AI 模块，这里的请求都是"一次请求一个值"，
 * 唯一的流式对话直接复用 ai-api 的 `streamChatMessage`——复习追问走的就是
 * 学习室的消息端点，没有第二套流。
 */

import { z } from "zod"

import { httpErrorFrom } from "@/lib/api"
import {
  dueCountSchema,
  dueListSchema,
  ratingSuggestionSchema,
  reviewChatStartSchema,
  reviewOutcomeSchema,
  reviewSessionRowSchema,
  scheduleSchema,
  suspensionResultSchema,
  type DueCount,
  type DueList,
  type RatePayload,
  type RatingSuggestion,
  type ReviewChatStart,
  type ReviewOutcome,
  type ReviewSessionRow,
  type Schedule,
} from "@/lib/review-schemas"

const REVIEW_BASE = "/api/review"

function networkError(): Error {
  return new Error(
    "连不上后端服务。请确认已经运行 start.ps1（或 start.sh），并且浏览器地址是 http://127.0.0.1:8000。",
  )
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  options: { method?: "GET" | "POST" | "PUT" | "DELETE"; body?: unknown } = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      headers: options.body === undefined ? undefined : { "Content-Type": "application/json" },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    })
  } catch {
    throw networkError()
  }
  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // 响应体不是 JSON，交给 httpErrorFrom 的兜底文案
    }
    throw httpErrorFrom(response, body)
  }
  return schema.parse(await response.json())
}

// ===== 到期列表 =====

export function fetchDueList(limit = 50): Promise<DueList> {
  return request(`${REVIEW_BASE}/due?limit=${limit}`, dueListSchema)
}

export function fetchDueCount(): Promise<DueCount> {
  return request(`${REVIEW_BASE}/due/count`, dueCountSchema)
}

// ===== 单条知识的排期、评级、暂停 =====

export function fetchSchedule(knowledgeId: string): Promise<Schedule> {
  return request(
    `${REVIEW_BASE}/${encodeURIComponent(knowledgeId)}/schedule`,
    scheduleSchema,
  )
}

export function submitRating(knowledgeId: string, payload: RatePayload): Promise<ReviewOutcome> {
  return request(`${REVIEW_BASE}/${encodeURIComponent(knowledgeId)}/rate`, reviewOutcomeSchema, {
    method: "POST",
    body: payload,
  })
}

export function setSuspension(knowledgeId: string, suspended: boolean) {
  return request(
    `${REVIEW_BASE}/${encodeURIComponent(knowledgeId)}/suspension`,
    suspensionResultSchema,
    { method: "PUT", body: { suspended } },
  )
}

export function fetchReviewHistory(knowledgeId: string): Promise<ReviewSessionRow[]> {
  return request(
    `${REVIEW_BASE}/${encodeURIComponent(knowledgeId)}/history`,
    z.array(reviewSessionRowSchema),
  )
}

// ===== AI 对话式复习（工作流 D）=====

/** 开一场对话式复习。**付费操作**：调用方负责先用 window.confirm 说明（D-042）。 */
export function startReviewSession(knowledgeId: string): Promise<ReviewChatStart> {
  return request(`${REVIEW_BASE}/${encodeURIComponent(knowledgeId)}/session`, reviewChatStartSchema, {
    method: "POST",
  })
}

/** 让 AI 给评级建议。也是一次付费调用，同样是点击前说明。 */
export function fetchRatingSuggestion(
  knowledgeId: string,
  conversationId: string,
): Promise<RatingSuggestion> {
  return request(
    `${REVIEW_BASE}/${encodeURIComponent(knowledgeId)}/rating-suggestion`,
    ratingSuggestionSchema,
    { method: "POST", body: { conversation_id: conversationId } },
  )
}
