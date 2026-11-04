/**
 * 关系的 React Query 封装。失效策略同 review-queries.ts：
 * 任何写操作成功后 invalidate 整个 `["relations"]` 前缀——
 * 处置一条候选会同时改变待审核队列、图谱和统计卡，分散失效迟早漏掉一个。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  decideRelation,
  fetchCandidates,
  fetchGraph,
  fetchGrowthStats,
  fetchPendingRelations,
  fetchRelated,
  judgePairs,
  updateRelationType,
} from "@/lib/relation-api"
import type { DecideAction } from "@/lib/relation-schemas"

export const relationKeys = {
  all: ["relations"] as const,
  candidates: (limit: number) => ["relations", "candidates", limit] as const,
  pending: () => ["relations", "pending"] as const,
  graph: () => ["relations", "graph"] as const,
  stats: () => ["relations", "stats"] as const,
  related: (id: string) => ["relations", "related", id] as const,
}

export function useCandidates(limit = 20) {
  return useQuery({
    queryKey: relationKeys.candidates(limit),
    queryFn: () => fetchCandidates(limit),
  })
}

export function usePendingRelations() {
  return useQuery({
    queryKey: relationKeys.pending(),
    queryFn: fetchPendingRelations,
  })
}

export function useGraph() {
  return useQuery({
    queryKey: relationKeys.graph(),
    queryFn: fetchGraph,
  })
}

export function useGrowthStats() {
  return useQuery({
    queryKey: relationKeys.stats(),
    queryFn: fetchGrowthStats,
  })
}

export function useRelated(knowledgeId: string | undefined) {
  return useQuery({
    queryKey: relationKeys.related(knowledgeId ?? ""),
    queryFn: () => fetchRelated(knowledgeId as string),
    enabled: Boolean(knowledgeId),
    retry: false,
  })
}

function useInvalidateAll() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: relationKeys.all })
}

export function useJudgePairs() {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: judgePairs,
    onSuccess: invalidate,
  })
}

export function useDecideRelation() {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: DecideAction }) =>
      decideRelation(id, action),
    onSuccess: invalidate,
  })
}

export function useUpdateRelationType() {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: ({ id, relationType }: { id: string; relationType: string }) =>
      updateRelationType(id, relationType),
    onSuccess: invalidate,
  })
}
