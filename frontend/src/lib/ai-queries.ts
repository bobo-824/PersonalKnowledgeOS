/**
 * AI 接口的 React Query 封装。
 *
 * 失效策略：
 * - 写设置 → 失效 `["ai", "config"]`（首页的自检卡也读 health，但它自己会重查）。
 * - 采纳审查 → 失效整个 `["knowledge"]`（版本数变了，列表和详情都要刷新）
 *   加这条知识的审查列表。
 * - 会话消息在流结束后由页面手动 refetch：SSE 期间 React Query 看不到变化。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  acceptReview,
  clearConversationHistory,
  createConversation,
  createReview,
  decideReviewItem,
  fetchAiConfig,
  fetchConversation,
  fetchConversations,
  listReviews,
  updateAiConfig,
  type AiConfigChanges,
  type AcceptReviewPayload,
} from "@/lib/ai-api"
import type { Review, ReviewCategory, ReviewDecision } from "@/lib/ai-schemas"
import { knowledgeKeys } from "@/lib/knowledge-queries"

export const aiKeys = {
  all: ["ai"] as const,
  conversations: () => ["ai", "conversations"] as const,
  conversation: (id: string) => ["ai", "conversation", id] as const,
  config: () => ["ai", "config"] as const,
  reviews: (knowledgeId: string) => ["ai", "reviews", knowledgeId] as const,
}

export function useConversations(limit = 50) {
  return useQuery({
    queryKey: aiKeys.conversations(),
    queryFn: () => fetchConversations(limit),
  })
}

export function useConversation(id: string | undefined) {
  return useQuery({
    queryKey: aiKeys.conversation(id ?? ""),
    queryFn: () => fetchConversation(id as string),
    enabled: Boolean(id),
    // 正在流式回复时不让后台重取打断滚动位置，页面会在流结束后手动刷新
    refetchOnWindowFocus: false,
  })
}

export function useAiConfig() {
  return useQuery({
    queryKey: aiKeys.config(),
    queryFn: fetchAiConfig,
  })
}

export function useReviews(knowledgeId: string | undefined) {
  return useQuery({
    queryKey: aiKeys.reviews(knowledgeId ?? ""),
    queryFn: () => listReviews(knowledgeId as string),
    enabled: Boolean(knowledgeId),
  })
}

export function useCreateConversation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (text: string) => createConversation(text),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: aiKeys.conversations() }),
  })
}

export function useClearConversationHistory() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => clearConversationHistory(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: aiKeys.all }),
  })
}

export function useUpdateAiConfig() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (changes: AiConfigChanges) => updateAiConfig(changes),
    onSuccess: (result) => {
      queryClient.setQueryData(aiKeys.config(), result.config)
    },
  })
}

export function useCreateReview(knowledgeId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => createReview(knowledgeId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: aiKeys.reviews(knowledgeId) }),
  })
}

export function useDecideReviewItem(reviewId: string, knowledgeId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { category: ReviewCategory; itemIndex: number; decision: ReviewDecision }) =>
      decideReviewItem(reviewId, input.category, input.itemIndex, input.decision),
    // 后端返回整份审查，直接写入缓存，界面立即反映新处置
    onSuccess: (review) => {
      queryClient.setQueryData(aiKeys.reviews(knowledgeId), (old: Review[] | undefined) =>
        old?.map((item) => (item.id === review.id ? review : item)) ?? [review],
      )
    },
  })
}

export function useAcceptReview(knowledgeId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { reviewId: string; payload: AcceptReviewPayload }) =>
      acceptReview(input.reviewId, input.payload),
    onSuccess: () => {
      // 新版本影响知识列表（版本数、当前理解）和详情页
      queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
      queryClient.invalidateQueries({ queryKey: aiKeys.reviews(knowledgeId) })
    },
  })
}
