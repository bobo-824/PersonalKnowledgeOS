/**
 * 知识单元的 React Query 封装。
 *
 * 页面里不直接调 `fetch`，都走这里的 hook，原因有两个：
 * 1. **失效策略集中在一处**。任何写操作成功后都 invalidate 整个
 *    `["knowledge"]` 前缀，列表、搜索、详情、领域补全会一起刷新。
 *    分散到各页面的话，迟早会漏掉一个，表现为"保存了但列表没变"。
 * 2. `enabled` 条件统一处理，空搜索词不会发请求。
 */

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import {
  ApiError,
  archiveKnowledge,
  createKnowledge,
  fetchDomainSuggestions,
  fetchKnowledge,
  fetchKnowledgePage,
  fetchSimilarTitles,
  restoreKnowledge,
  searchKnowledge,
  updateKnowledge,
  type KnowledgeCreatePayload,
  type KnowledgePageParams,
  type KnowledgeUpdatePayload,
  type SearchParams,
} from "@/lib/api"

export const knowledgeKeys = {
  all: ["knowledge"] as const,
  page: (params: KnowledgePageParams) => ["knowledge", "page", params] as const,
  detail: (id: string) => ["knowledge", "detail", id] as const,
  search: (params: SearchParams) => ["knowledge", "search", params] as const,
  domains: () => ["knowledge", "domains"] as const,
  similar: (title: string) => ["knowledge", "similar", title] as const,
}

export function useKnowledgePage(params: KnowledgePageParams, enabled = true) {
  return useQuery({
    queryKey: knowledgeKeys.page(params),
    queryFn: ({ signal }) => fetchKnowledgePage(params, signal),
    // 翻页时保留上一页内容，避免整块列表闪成空白
    placeholderData: keepPreviousData,
    enabled,
  })
}

export function useKnowledge(id: string | undefined) {
  return useQuery({
    queryKey: knowledgeKeys.detail(id ?? ""),
    queryFn: ({ signal }) => fetchKnowledge(id as string, signal),
    enabled: Boolean(id),
    // 详情页可能停留在旧数据上，切回来时不要显示上一次的缓存内容
    retry: (failureCount, error) => {
      // 404 不重试：记录不存在是确定结论，重试三次只是白等
      if (error instanceof ApiError && error.status === 404) return false
      return failureCount < 1
    },
  })
}

export function useKnowledgeSearch(params: SearchParams, enabled = true) {
  const query = params.q.trim()
  return useQuery({
    queryKey: knowledgeKeys.search({ ...params, q: query }),
    queryFn: ({ signal }) => searchKnowledge({ ...params, q: query }, signal),
    // 空搜索词不发请求：后端的 q 有 min_length=1，发过去只会换来 422
    enabled: enabled && query.length > 0,
    placeholderData: keepPreviousData,
  })
}

export function useDomainSuggestions(limit = 50) {
  return useQuery({
    queryKey: knowledgeKeys.domains(),
    queryFn: ({ signal }) => fetchDomainSuggestions(limit, signal),
    staleTime: 60_000,
  })
}

/**
 * 新建时的"你是不是已经记过了"提示。
 *
 * 后端对少于 2 个字的标题直接返回空，这里同样不发请求，
 * 因为单字标题的误报率高到没有参考价值。
 */
export function useSimilarTitles(title: string) {
  const query = title.trim()
  return useQuery({
    queryKey: knowledgeKeys.similar(query),
    queryFn: ({ signal }) => fetchSimilarTitles(query, 5, signal),
    enabled: query.length >= 2,
    staleTime: 30_000,
  })
}

function useInvalidateAll() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
}

export function useCreateKnowledge() {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: (payload: KnowledgeCreatePayload) => createKnowledge(payload),
    onSuccess: invalidate,
  })
}

export function useUpdateKnowledge(id: string) {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: (payload: KnowledgeUpdatePayload) => updateKnowledge(id, payload),
    onSuccess: invalidate,
  })
}

export function useArchiveKnowledge() {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: (id: string) => archiveKnowledge(id),
    onSuccess: invalidate,
  })
}

export function useRestoreKnowledge() {
  const invalidate = useInvalidateAll()
  return useMutation({
    mutationFn: (id: string) => restoreKnowledge(id),
    onSuccess: invalidate,
  })
}
