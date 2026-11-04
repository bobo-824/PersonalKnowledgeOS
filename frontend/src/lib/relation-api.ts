/**
 * 与后端 `/api/relations` 通信的出口。`request` 与 review-api.ts 同一份写法。
 *
 * 花费边界（D-042 / D-066）：只有 `judgePairs` 花 AI 请求费，
 * 调用方必须先 window.confirm 明示请求数再调；其余端点全部免费。
 */

import { z } from "zod"

import { httpErrorFrom } from "@/lib/api"
import {
  candidateListSchema,
  graphSchema,
  growthStatsSchema,
  judgeReportSchema,
  relatedForKnowledgeSchema,
  relationSchema,
  type CandidateList,
  type DecideAction,
  type Graph,
  type GrowthStats,
  type JudgeReport,
  type RelatedForKnowledge,
  type Relation,
} from "@/lib/relation-schemas"

const RELATION_BASE = "/api/relations"

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

// ===== 免费候选 =====

export function fetchCandidates(limit = 20): Promise<CandidateList> {
  return request(`${RELATION_BASE}/candidates?limit=${limit}`, candidateListSchema)
}

// ===== AI 判断（付费，每对一次请求）=====

export function judgePairs(
  pairs: { source_id: string; target_id: string }[],
): Promise<JudgeReport> {
  return request(`${RELATION_BASE}/judge`, judgeReportSchema, {
    method: "POST",
    body: { pairs },
  })
}

// ===== 待审核队列与处置 =====

export function fetchPendingRelations(): Promise<Relation[]> {
  return request(`${RELATION_BASE}/pending`, z.array(relationSchema))
}

export function decideRelation(relationId: string, action: DecideAction): Promise<Relation> {
  return request(
    `${RELATION_BASE}/${encodeURIComponent(relationId)}/decide`,
    relationSchema,
    { method: "POST", body: { action } },
  )
}

export function updateRelationType(relationId: string, relationType: string): Promise<Relation> {
  return request(
    `${RELATION_BASE}/${encodeURIComponent(relationId)}/type`,
    relationSchema,
    { method: "PUT", body: { relation_type: relationType } },
  )
}

// ===== 图谱与统计 =====

export function fetchGraph(): Promise<Graph> {
  return request(`${RELATION_BASE}/graph`, graphSchema)
}

export function fetchGrowthStats(): Promise<GrowthStats> {
  return request(`${RELATION_BASE}/stats`, growthStatsSchema)
}

// ===== 详情页相关知识 =====

export function fetchRelated(knowledgeId: string): Promise<RelatedForKnowledge> {
  return request(
    `${RELATION_BASE}/knowledge/${encodeURIComponent(knowledgeId)}/related`,
    relatedForKnowledgeSchema,
  )
}
