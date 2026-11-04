/**
 * 与后端 `/api/ai`、`/api/settings` 通信的出口（工作流 A/B/C + 设置页）。
 *
 * 复用 api.ts 的错误翻译；SSE 流式回复是这里唯一不用 React Query 的请求——
 * 流是一个持续产出事件的过程，不是"一次请求一个值"，用 `onChunk` 回调
 * 比塞进 query 缓存更贴合。
 *
 * ## 为什么用 fetch + ReadableStream 而不是 EventSource
 *
 * EventSource 只支持 GET，而发消息必须 POST 带 JSON 体；
 * 它也不能带 `Content-Type: application/json`。所以手工解析
 * `data: {json}\n\n` 帧格式（后端 backend/api/ai.py 的 `_event` 只产这一种行）。
 *
 * ## 幂等键为什么由调用方持有
 *
 * `confirmDraft` 的幂等键**必须由调用方生成并持有**（草稿从提取到确认期间不变）：
 * 如果每次调用现生成一个新键，超时重发和刷新后再点就各自是新键，
 * 服务端的去重就形同虚设。按钮 disabled 挡手滑，这把键挡重放。
 */

import { z } from "zod"

import { httpErrorFrom } from "@/lib/api"
import {
  aiConfigUpdateResultSchema,
  aiConfigViewSchema,
  aiConnectionTestSchema,
  conversationDetailSchema,
  conversationSchema,
  draftConfirmResultSchema,
  historyClearedSchema,
  knowledgeDraftSchema,
  reviewSchema,
  sseEventSchema,
  type AiConfigUpdateResult,
  type AiConfigView,
  type AiConnectionTest,
  type Conversation,
  type ConversationDetail,
  type DraftConfirmResult,
  type HistoryCleared,
  type KnowledgeDraft,
  type Review,
  type ReviewCategory,
  type ReviewDecision,
} from "@/lib/ai-schemas"
import { knowledgeReadSchema, type KnowledgeRead, type SourceKind } from "@/lib/schemas"

const AI_BASE = "/api/ai"
const SETTINGS_BASE = "/api/settings"

/** 与 api.ts 的 request() 同样的网络失败文案：读者可能完全不懂技术。 */
function networkError(): Error {
  return new Error(
    "连不上后端服务。请确认已经运行 start.ps1（或 start.sh），并且浏览器地址是 http://127.0.0.1:8000。",
  )
}

async function requestAi<T>(
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

// ===== 会话 =====

export function fetchConversations(limit = 50): Promise<Conversation[]> {
  return requestAi(`${AI_BASE}/conversations?limit=${limit}`, z.array(conversationSchema))
}

export function fetchConversation(id: string): Promise<ConversationDetail> {
  return requestAi(
    `${AI_BASE}/conversations/${encodeURIComponent(id)}`,
    conversationDetailSchema,
  )
}

export function createConversation(text: string): Promise<Conversation> {
  return requestAi(`${AI_BASE}/conversations`, conversationSchema, {
    method: "POST",
    body: { text },
  })
}

/** 清空对话历史。只删对话，知识、版本、审查记录一行不动（后端有专门测试）。 */
export function clearConversationHistory(): Promise<HistoryCleared> {
  return requestAi(`${AI_BASE}/conversations?confirm=yes`, historyClearedSchema, {
    method: "DELETE",
  })
}

// ===== 流式回复 =====

export type StreamHandlers = {
  /** 每收到一小段 AI 文字就回调一次；界面把它追加进正在生成的消息里 */
  onChunk: (text: string) => void
}

/**
 * 发一条消息并消费 SSE 流。
 *
 * - 正常结束（收到 done）：resolve，消息已在服务端落库，调用方刷新会话即可。
 * - AI 失败（收到 error 事件）：抛 `AiStreamError`。**用户那条消息和失败占位
 *   都已经在服务端落库**，所以同样只需刷新会话。
 * - HTTP 层失败（404/409/422/502…）：抛 `ApiError`，消息没有发出。
 */
export async function streamChatMessage(
  conversationId: string,
  text: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(
      `${AI_BASE}/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ text }),
        signal,
      },
    )
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error
    throw networkError()
  }

  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // SSE 端点失败时总该回 JSON，这里兜个底
    }
    throw httpErrorFrom(response, body)
  }
  if (!response.body) {
    throw new Error("后端没有返回流式响应体，请刷新页面重试。")
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  const handleFrame = (frame: string): void => {
    // 后端一行只产 `data: {json}`，空行是帧分隔。为稳妥起见按行扫描。
    for (const line of frame.split("\n")) {
      if (!line.startsWith("data:")) continue
      let event: unknown
      try {
        event = JSON.parse(line.slice(5).trim())
      } catch {
        throw new Error("收到无法解析的流式数据，请刷新页面重试。")
      }
      const parsed = sseEventSchema.parse(event)
      if (parsed.type === "chunk") {
        handlers.onChunk(parsed.text)
      } else if (parsed.type === "error") {
        throw new AiStreamError(parsed.error_kind, parsed.message)
      }
      // done：正常收尾，外层循环自然结束
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let separator = buffer.indexOf("\n\n")
      while (separator !== -1) {
        handleFrame(buffer.slice(0, separator))
        buffer = buffer.slice(separator + 2)
        separator = buffer.indexOf("\n\n")
      }
    }
    // 流以连接关闭结束（done 之后的收尾），剩余不足一帧的内容不再处理
  } finally {
    reader.releaseLock()
  }
}

/** AI 失败的流内错误。error_kind 用 ai_messages 那套词表，界面据此给建议。 */
export class AiStreamError extends Error {
  readonly errorKind: string
  constructor(errorKind: string, message: string) {
    super(message)
    this.name = "AiStreamError"
    this.errorKind = errorKind
  }
}

// ===== 提取草稿与确认（工作流 B）=====

export function extractDraft(conversationId: string): Promise<KnowledgeDraft> {
  return requestAi(
    `${AI_BASE}/conversations/${encodeURIComponent(conversationId)}/extract`,
    knowledgeDraftSchema,
    { method: "POST", body: {} },
  )
}

export type DraftConfirmPayload = {
  title: string
  domain: string | null
  summary: string | null
  user_understanding: string
  ai_supplements: string[]
  reasoning: string | null
  open_questions: string | null
  suggested_sources: SourceDraftPayload[]
}

export type SourceDraftPayload = {
  source_type: SourceKind
  title: string
  url_or_reference: string | null
  note: string | null
}

/**
 * 确认草稿入库。`idempotencyKey` 由调用方在草稿加载时生成、确认前保持不变——
 * 同一个键重复提交只会创建一条（服务端按键去重，见 D-052 的取舍说明）。
 */
export function confirmDraft(
  draft: DraftConfirmPayload,
  idempotencyKey: string,
): Promise<DraftConfirmResult> {
  return requestAi(`${AI_BASE}/drafts/confirm`, draftConfirmResultSchema, {
    method: "POST",
    body: { draft, idempotency_key: idempotencyKey },
  })
}

// ===== 知识审查（工作流 C）=====

export function listReviews(knowledgeId: string): Promise<Review[]> {
  return requestAi(
    `${AI_BASE}/reviews?knowledge_id=${encodeURIComponent(knowledgeId)}`,
    z.array(reviewSchema),
  )
}

export function createReview(knowledgeId: string): Promise<Review> {
  return requestAi(`${AI_BASE}/reviews`, reviewSchema, {
    method: "POST",
    body: { knowledge_id: knowledgeId },
  })
}

export function decideReviewItem(
  reviewId: string,
  category: ReviewCategory,
  itemIndex: number,
  decision: ReviewDecision,
): Promise<Review> {
  return requestAi(
    `${AI_BASE}/reviews/${encodeURIComponent(reviewId)}/decisions`,
    reviewSchema,
    { method: "PUT", body: { category, item_index: itemIndex, decision } },
  )
}

export type AcceptReviewPayload = {
  new_understanding: string
  reasoning?: string | null
  open_questions?: string | null
  change_reason?: string | null
}

/** 采纳审查。响应是更新后的知识（含新版本），调用方借此刷新详情页。 */
export function acceptReview(
  reviewId: string,
  payload: AcceptReviewPayload,
): Promise<KnowledgeRead> {
  return requestAi(
    `${AI_BASE}/reviews/${encodeURIComponent(reviewId)}/accept`,
    knowledgeReadSchema,
    { method: "POST", body: payload },
  )
}

// ===== 设置页 =====

export function fetchAiConfig(): Promise<AiConfigView> {
  return requestAi(`${SETTINGS_BASE}/ai`, aiConfigViewSchema)
}

export type AiConfigChanges = {
  provider?: string
  base_url?: string
  api_key?: string
  model?: string
  timeout_seconds?: number
  json_mode?: boolean
}

export function updateAiConfig(changes: AiConfigChanges): Promise<AiConfigUpdateResult> {
  return requestAi(`${SETTINGS_BASE}/ai`, aiConfigUpdateResultSchema, {
    method: "PUT",
    body: changes,
  })
}

export function testAiConnection(): Promise<AiConnectionTest> {
  return requestAi(`${SETTINGS_BASE}/ai/test`, aiConnectionTestSchema, {
    method: "POST",
    body: {},
  })
}
