/**
 * 与后端 `/api` 通信的唯一出口。
 *
 * 所有请求都经过这里，好处是三件事只需做一遍：
 * 1. 相对路径 `/api/...`——开发模式走 Vite 代理，单端口模式同源，都不需要配地址
 * 2. 用 zod 校验响应，格式对不上就给出可读报错，而不是让 `undefined` 渗进界面
 * 3. 把 FastAPI 的错误结构翻译成中文，尤其是 422 的字段级校验信息
 *
 * ## 为什么不用 axios
 *
 * 需求就是 fetch + JSON + 错误翻译，浏览器原生 fetch 完全够用。
 * 少一个依赖，少一份要跟着升级的东西。
 */

import { z } from "zod"

import {
  domainSuggestionSchema,
  knowledgePageSchema,
  knowledgeReadSchema,
  searchResultSchema,
  type DomainSuggestion,
  type KnowledgePage,
  type KnowledgeRead,
  type KnowledgeSourceType,
  type SearchResult,
  type SourceKind,
} from "@/lib/schemas"

export type QueryValue = string | number | boolean | null | undefined

/** 字段名 → 界面上的中文标签，用于把 422 报错说成人话。 */
const FIELD_LABELS: Record<string, string> = {
  title: "名称",
  user_understanding: "自己的理解",
  domain: "所属领域",
  summary: "摘要",
  source_type: "来源类型",
  reasoning: "理解依据",
  open_questions: "尚存疑问",
  change_reason: "修改说明",
  sources: "学习来源",
  url_or_reference: "链接或出处",
  note: "备注",
  status: "状态",
  q: "搜索词",
  limit: "每页条数",
  offset: "起始位置",
}

type FastApiErrorItem = {
  type?: string
  loc?: (string | number)[]
  msg?: string
  ctx?: Record<string, unknown>
}

export type ApiErrorKind = "network" | "http" | "schema"

/**
 * 所有失败的请求都抛这个类型，界面只需要看 `message` 和 `fieldErrors`。
 *
 * `fieldErrors` 单独拆出来，是因为一次提交可能同时有好几个字段不合格，
 * 表单要把它们分别显示在对应位置附近，而不是揉成一句话。
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number
  readonly fieldErrors: string[]

  constructor(
    kind: ApiErrorKind,
    status: number,
    message: string,
    fieldErrors: string[] = [],
  ) {
    super(message)
    this.name = "ApiError"
    this.kind = kind
    this.status = status
    this.fieldErrors = fieldErrors
  }
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    // 空值一律不带：后端对空字符串有 min_length 校验，
    // 传过去只会换来一个 422，而"没有筛选条件"本来就不该出现在 URL 里。
    if (value === undefined || value === null || value === "") continue
    params.set(key, String(value))
  }
  const search = params.toString()
  return search ? `${path}?${search}` : path
}

/** 把 `loc` 翻译成中文位置描述，例如 `["body","sources",0]` → `学习来源 · 第 1 项`。 */
function describeLocation(loc: (string | number)[] | undefined): string {
  if (!loc) return ""
  const parts = loc.filter((part) => part !== "body" && part !== "query" && part !== "path")
  if (parts.length === 0) return ""
  return parts
    .map((part) =>
      typeof part === "number" ? `第 ${part + 1} 项` : (FIELD_LABELS[part] ?? part),
    )
    .join(" · ")
}

function stripQuotes(value: unknown): string {
  return String(value ?? "")
    .replace(/'/g, "")
    .replace(/ or /g, " / ")
}

/**
 * Pydantic 的英文报错 → 中文。
 *
 * 我们自己写的 `model_validator` 抛出的消息本来就是中文，
 * 会被 Pydantic 加上 `"Value error, "` 前缀，这里把前缀去掉原样返回。
 */
function translateMessage(item: FastApiErrorItem): string {
  const msg = item.msg ?? ""
  const VALUE_ERROR_PREFIX = "Value error, "
  if (msg.startsWith(VALUE_ERROR_PREFIX)) {
    return msg.slice(VALUE_ERROR_PREFIX.length)
  }

  switch (item.type) {
    case "missing":
      return "必填"
    case "extra_forbidden":
      return "字段名不存在，可能是拼错了"
    case "string_too_long":
      return `最多 ${item.ctx?.max_length ?? "?"} 个字符`
    case "string_too_short":
      return `至少 ${item.ctx?.min_length ?? "?"} 个字符`
    case "too_small":
      return `不能小于 ${item.ctx?.ge ?? item.ctx?.gt ?? item.ctx?.min_length ?? "?"}`
    case "too_big":
      return `不能大于 ${item.ctx?.le ?? item.ctx?.lt ?? item.ctx?.max_length ?? "?"}`
    case "literal_error":
    case "enum":
      return `只能是：${stripQuotes(item.ctx?.expected)}`
    case "value_error":
      // 中文消息丢了（极少数情况），至少别显示空白
      return msg || "内容不符合要求"
    default:
      return msg || "格式不正确"
  }
}

/** 把 FastAPI 的 422 `detail` 数组翻译成一组中文句子。 */
export function describeValidationErrors(detail: unknown): string[] {
  if (!Array.isArray(detail)) return []
  return detail
    .filter((item): item is FastApiErrorItem => typeof item === "object" && item !== null)
    .map((item) => {
      const text = translateMessage(item)
      const where = describeLocation(item.loc)
      return where ? `${where}：${text}` : text
    })
}

async function readErrorBody(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    // 响应体不是 JSON（比如代理返回了一段 HTML），没什么可解析的
    return null
  }
}

function httpErrorFrom(response: Response, body: unknown): ApiError {
  const detail =
    typeof body === "object" && body !== null && "detail" in body
      ? (body as { detail: unknown }).detail
      : null

  if (typeof detail === "string" && detail.length > 0) {
    return new ApiError("http", response.status, detail)
  }

  const fieldErrors = describeValidationErrors(detail)
  if (fieldErrors.length > 0) {
    return new ApiError("http", response.status, fieldErrors[0], fieldErrors)
  }

  if (response.status === 404) {
    return new ApiError("http", 404, "找不到这条记录，它可能已经被删除。")
  }
  if (response.status >= 500) {
    return new ApiError(
      "http",
      response.status,
      `后端处理出错了（HTTP ${response.status}）。详细原因请看后端窗口的日志。`,
    )
  }
  return new ApiError("http", response.status, `请求失败（HTTP ${response.status}）。`)
}

function schemaError(path: string, error: z.ZodError): ApiError {
  // 这类错误不是用户操作导致的，而是前后端契约对不上——必须说得足够明确，
  // 否则会被误当成"页面坏了"而查错方向。
  const issues = error.issues
    .slice(0, 3)
    .map((issue) => `${issue.path.join(".") || "(根)"}：${issue.message}`)
    .join("；")
  return new ApiError(
    "schema",
    200,
    `后端返回的数据和前端预期不一致（${path}）。${issues}`,
  )
}

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE"
  query?: Record<string, QueryValue>
  body?: unknown
  signal?: AbortSignal
}

async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", query, body, signal } = options
  const url = buildUrl(path, query)

  let response: Response
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error
    // 后端没起来、或者端口不对。这句话要让完全不懂技术的人也知道下一步做什么。
    throw new ApiError(
      "network",
      0,
      "连不上后端服务。请确认已经运行 start.ps1（或 start.sh），并且浏览器地址是 http://127.0.0.1:8000。",
    )
  }

  if (!response.ok) {
    throw httpErrorFrom(response, await readErrorBody(response))
  }

  // 204 No Content 之类没有响应体的情况
  if (response.status === 204) {
    return schema.parse(undefined)
  }

  const payload: unknown = await readErrorBody(response)
  const parsed = schema.safeParse(payload)
  if (!parsed.success) {
    throw schemaError(path, parsed.error)
  }
  return parsed.data
}

// ===== 知识单元接口 =====
//
// 每个函数对应后端 backend/api/knowledge.py 里的一个路由。
// 返回类型由 schemas.ts 的 zod 模型推断，后端改字段这里会立刻类型报错。

const BASE = "/api/knowledge"

export type SourcePayload = {
  source_type: SourceKind
  title: string
  url_or_reference?: string | null
  note?: string | null
}

export type KnowledgeCreatePayload = {
  title: string
  user_understanding: string
  domain?: string | null
  summary?: string | null
  source_type?: KnowledgeSourceType
  reasoning?: string | null
  open_questions?: string | null
  change_reason?: string | null
  sources?: SourcePayload[]
}

/**
 * 更新载荷。**字段缺席和显式 null 语义不同**：
 * - 缺席（`undefined`，`JSON.stringify` 会直接丢掉这个键）= 不改这个字段
 * - `null` = 把这个字段清空
 *
 * 后端靠 Pydantic 的 `model_fields_set` 区分两者，前端不要把它们混为一谈。
 * 编辑表单是一次性提交完整表单，所以每个字段都会显式出现。
 */
export type KnowledgeUpdatePayload = {
  title?: string | null
  domain?: string | null
  summary?: string | null
  user_understanding?: string | null
  reasoning?: string | null
  open_questions?: string | null
  change_reason?: string | null
  sources?: SourcePayload[] | null
}

/** 列表与搜索共用的状态筛选。`all` 表示连已归档的一起显示。 */
export type StatusFilter = "active" | "archived" | "all"

export type KnowledgePageParams = {
  status?: StatusFilter
  domain?: string | null
  limit?: number
  offset?: number
}

export type SearchParams = {
  q: string
  status?: StatusFilter
  limit?: number
  offset?: number
}

export function fetchKnowledgePage(
  params: KnowledgePageParams = {},
  signal?: AbortSignal,
): Promise<KnowledgePage> {
  return request(BASE, knowledgePageSchema, { query: { ...params }, signal })
}

export function fetchKnowledge(id: string, signal?: AbortSignal): Promise<KnowledgeRead> {
  return request(`${BASE}/${encodeURIComponent(id)}`, knowledgeReadSchema, { signal })
}

export function createKnowledge(payload: KnowledgeCreatePayload): Promise<KnowledgeRead> {
  return request(BASE, knowledgeReadSchema, { method: "POST", body: payload })
}

export function updateKnowledge(
  id: string,
  payload: KnowledgeUpdatePayload,
): Promise<KnowledgeRead> {
  return request(`${BASE}/${encodeURIComponent(id)}`, knowledgeReadSchema, {
    method: "PUT",
    body: payload,
  })
}

/** 归档不物理删除：数据完整保留，只是默认列表和搜索不再显示。 */
export function archiveKnowledge(id: string): Promise<KnowledgeRead> {
  return request(`${BASE}/${encodeURIComponent(id)}/archive`, knowledgeReadSchema, {
    method: "POST",
  })
}

export function restoreKnowledge(id: string): Promise<KnowledgeRead> {
  return request(`${BASE}/${encodeURIComponent(id)}/restore`, knowledgeReadSchema, {
    method: "POST",
  })
}

export function searchKnowledge(
  params: SearchParams,
  signal?: AbortSignal,
): Promise<SearchResult> {
  return request(`${BASE}/search`, searchResultSchema, { query: { ...params }, signal })
}

/** 已用过的领域名及条数，喂给输入框的 `<datalist>` 做自动补全。 */
export function fetchDomainSuggestions(
  limit = 50,
  signal?: AbortSignal,
): Promise<DomainSuggestion[]> {
  return request(`${BASE}/domains`, z.array(domainSuggestionSchema), {
    query: { limit },
    signal,
  })
}

/** 按标题找可能已经记过的知识，用于新建时提示"你是不是已经记过了"。 */
export function fetchSimilarTitles(
  title: string,
  limit = 5,
  signal?: AbortSignal,
): Promise<string[]> {
  return request(`${BASE}/similar`, z.array(z.string()), {
    query: { title, limit },
    signal,
  })
}
