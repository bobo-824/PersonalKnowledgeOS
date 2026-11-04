/**
 * 复习的 React Query 封装。
 *
 * 失效策略同 knowledge-queries.ts：任何写操作成功后 invalidate 整个
 * `["review"]` 前缀——提交评级会改变到期列表、条数和排期，
 * 分散失效迟早漏掉一个，表现为"评完分首页数字不变"。
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  fetchDueCount,
  fetchDueList,
  fetchReviewHistory,
  fetchSchedule,
  setSuspension,
  submitRating,
} from "@/lib/review-api"
import type { RatePayload } from "@/lib/review-schemas"

export const reviewKeys = {
  all: ["review"] as const,
  due: (limit: number) => ["review", "due", limit] as const,
  dueCount: () => ["review", "due-count"] as const,
  schedule: (id: string) => ["review", "schedule", id] as const,
  history: (id: string) => ["review", "history", id] as const,
}

export function useDueList(limit = 50) {
  return useQuery({
    queryKey: reviewKeys.due(limit),
    queryFn: () => fetchDueList(limit),
    placeholderData: keepPreviousData,
  })
}

/** 首页的"今天还剩几条"。条数小、查询轻，staleTime 给 30 秒免得频繁刷。 */
export function useDueCount() {
  return useQuery({
    queryKey: reviewKeys.dueCount(),
    queryFn: () => fetchDueCount(),
    staleTime: 30_000,
  })
}

export function useSchedule(knowledgeId: string | undefined) {
  return useQuery({
    queryKey: reviewKeys.schedule(knowledgeId ?? ""),
    queryFn: () => fetchSchedule(knowledgeId as string),
    enabled: Boolean(knowledgeId),
    // 知识不存在是确定结论，重试只是白等（与 useKnowledge 的 404 处理一致）
    retry: false,
  })
}

export function useReviewHistory(knowledgeId: string | undefined) {
  return useQuery({
    queryKey: reviewKeys.history(knowledgeId ?? ""),
    queryFn: () => fetchReviewHistory(knowledgeId as string),
    enabled: Boolean(knowledgeId),
  })
}

function useInvalidateAll() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: reviewKeys.all })
}

export function useSubmitRating(knowledgeId: string) {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: (payload: RatePayload) => submitRating(knowledgeId, payload),
    onSuccess: invalidate,
  })
}

export function useSetSuspension(knowledgeId: string) {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: (suspended: boolean) => setSuspension(knowledgeId, suspended),
    onSuccess: invalidate,
  })
}
