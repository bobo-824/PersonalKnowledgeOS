/**
 * 关系接口的 zod 校验模型（与 backend/schemas/relation_api.py 一一对应）。
 *
 * ## 词表第三份拷贝的老约定
 *
 * 关系六类的英文值在后端有三份（domain/relation.py 常量、提示词、
 * schema Literal），这里是前端这份。漂移不会静默出错：
 * 后端返回陌生值时这里解析失败并给出可读报错。
 * 自定义类型（D-065）不是六类之一，所以 relation_type 用 z.string()
 * 而不是枚举——展示时六类查标签表，其余原样显示。
 *
 * ## C7 在显示层的落点
 *
 * 候选的 `confidence` 只影响列表排序的展示，界面上永远要用户
 * 亲自点「确认」，图谱里出现的关系只可能来自那次点击。
 */

import { z } from "zod"

const datetime = z.iso.datetime({ offset: true, local: true })

// ===== 词表 =====

export const RELATION_TYPES = [
  "prerequisite",
  "deeper",
  "analogy",
  "contrast",
  "application",
  "component",
] as const
export type CoreRelationType = (typeof RELATION_TYPES)[number]

/** D-065：固定六类的图例标签。自定义类型不在表里，显示原文。 */
export const RELATION_TYPE_LABELS: Record<CoreRelationType, string> = {
  prerequisite: "前置知识",
  deeper: "深入",
  analogy: "类比",
  contrast: "对比",
  application: "应用",
  component: "组成",
}

export function relationTypeLabel(relationType: string): string {
  return (RELATION_TYPE_LABELS as Record<string, string>)[relationType] ?? relationType
}

export const RELATION_STATUSES = ["pending", "accepted", "rejected", "ignored"] as const
export type RelationStatus = (typeof RELATION_STATUSES)[number]

export const STATUS_LABELS: Record<RelationStatus, string> = {
  pending: "待审核",
  accepted: "已确认",
  rejected: "已拒绝",
  ignored: "已忽略",
}

// ===== 免费候选 =====

export const candidatePairSchema = z.object({
  source_id: z.string(),
  target_id: z.string(),
  source_title: z.string(),
  target_title: z.string(),
  source_domain: z.string().nullable(),
  target_domain: z.string().nullable(),
})
export type CandidatePair = z.infer<typeof candidatePairSchema>

export const candidateListSchema = z.object({
  candidates: z.array(candidatePairSchema),
  generated_at: datetime,
})
export type CandidateList = z.infer<typeof candidateListSchema>

// ===== AI 判断 =====

export const judgeFailureSchema = z.object({
  source_id: z.string(),
  target_id: z.string(),
  error_kind: z.string(),
  message: z.string(),
})
export type JudgeFailure = z.infer<typeof judgeFailureSchema>

export const judgeReportSchema = z.object({
  created: z.number().int(),
  skipped: z.number().int(),
  unrelated: z.number().int(),
  failed: z.array(judgeFailureSchema),
})
export type JudgeReport = z.infer<typeof judgeReportSchema>

// ===== 待审核队列 =====

export const relationSchema = z.object({
  id: z.string(),
  source_id: z.string(),
  target_id: z.string(),
  source_title: z.string(),
  target_title: z.string(),
  relation_type: z.string(),
  explanation: z.string(),
  evidence: z.string(),
  confidence: z.number(),
  status: z.enum(RELATION_STATUSES),
  model: z.string().nullable(),
  prompt_version: z.string().nullable(),
  reviewed_at: datetime.nullable(),
  created_at: datetime,
  updated_at: datetime,
})
export type Relation = z.infer<typeof relationSchema>

export const DECIDE_ACTIONS = ["accepted", "rejected", "ignored"] as const
export type DecideAction = (typeof DECIDE_ACTIONS)[number]

// ===== 图谱与统计 =====

export const graphNodeSchema = z.object({
  id: z.string(),
  title: z.string(),
  domain: z.string().nullable(),
})
export type GraphNode = z.infer<typeof graphNodeSchema>

export const graphEdgeSchema = z.object({
  id: z.string(),
  source_id: z.string(),
  target_id: z.string(),
  relation_type: z.string(),
  explanation: z.string(),
})
export type GraphEdge = z.infer<typeof graphEdgeSchema>

export const graphSchema = z.object({
  nodes: z.array(graphNodeSchema),
  edges: z.array(graphEdgeSchema),
})
export type Graph = z.infer<typeof graphSchema>

export const growthStatsSchema = z.object({
  total_knowledge: z.number().int(),
  added_last_7_days: z.number().int(),
  total_reviews: z.number().int(),
  total_relations: z.number().int(),
  domains: z.record(z.string(), z.number().int()),
  generated_at: datetime,
})
export type GrowthStats = z.infer<typeof growthStatsSchema>

// ===== 详情页相关知识 =====

export const relatedForKnowledgeSchema = z.object({
  relations: z.array(
    z.object({
      id: z.string(),
      direction: z.enum(["source", "target"]),
      relation_type: z.string(),
      other_id: z.string(),
      other_title: z.string(),
      explanation: z.string(),
    }),
  ),
  candidates: z.array(candidatePairSchema),
})
export type RelatedForKnowledge = z.infer<typeof relatedForKnowledgeSchema>
