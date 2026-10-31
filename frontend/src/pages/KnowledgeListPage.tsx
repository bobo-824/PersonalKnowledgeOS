import { useEffect, useState } from "react"
import { Library, Plus, Search, SearchX, X } from "lucide-react"
import { Link, useNavigate } from "react-router-dom"

import { ApiErrorNotice, EmptyState } from "@/components/feedback/Notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import type { StatusFilter } from "@/lib/api"
import { collapseWhitespace, formatRelative } from "@/lib/format"
import {
  useDomainSuggestions,
  useKnowledgePage,
  useKnowledgeSearch,
} from "@/lib/knowledge-queries"
import { useDebouncedValue } from "@/lib/use-debounced-value"
import { MATCHED_BY_LABELS, STATUS_LABELS, type KnowledgeStatus } from "@/lib/schemas"

const PAGE_SIZE = 20

/**
 * 列表行和搜索结果行的共同形状。
 *
 * 两个接口返回的字段不完全一样（搜索结果没有版本数和更新时间），
 * 统一成这一个类型，下面渲染时就不用维护两套 JSX。
 */
type Row = {
  id: string
  title: string
  domain: string | null
  status: KnowledgeStatus
  excerpt: string | null
  versionCount: number | null
  updatedAt: string | null
  matchedBy: keyof typeof MATCHED_BY_LABELS | null
}

const STRATEGY_LABELS = {
  fts: "全文索引",
  like: "关键词匹配",
} as const

// 状态词加了书名号：模板是「…」在${label}里没有匹配项，
// 不加的话「在用」会拼成「在在用的知识里」。
function statusScopeLabel(status: StatusFilter): string {
  if (status === "active") return "「在用」的知识"
  if (status === "archived") return "「已归档」的知识"
  return "全部知识"
}

export function KnowledgeListPage() {
  const navigate = useNavigate()

  const [searchInput, setSearchInput] = useState("")
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("active")
  const [domainFilter, setDomainFilter] = useState("")
  const [offset, setOffset] = useState(0)

  // 中文输入法在候选词阶段会连续触发 onChange，防抖之后才发请求
  const debouncedSearch = useDebouncedValue(searchInput, 300)
  const isSearching = debouncedSearch.trim().length > 0

  // 换筛选条件就回到第一页，否则会停在第 5 页看一个只有 2 条的结果集
  useEffect(() => {
    setOffset(0)
  }, [debouncedSearch, statusFilter, domainFilter])

  const { data: domainSuggestions = [] } = useDomainSuggestions()

  const listQuery = useKnowledgePage(
    {
      status: statusFilter,
      domain: domainFilter || null,
      limit: PAGE_SIZE,
      offset,
    },
    !isSearching,
  )

  const searchQuery = useKnowledgeSearch(
    {
      q: debouncedSearch,
      status: statusFilter,
      limit: PAGE_SIZE,
      offset,
    },
    isSearching,
  )

  const activeQuery = isSearching ? searchQuery : listQuery

  const rows: Row[] = isSearching
    ? (searchQuery.data?.items ?? []).map((item) => ({
        id: item.knowledge_id,
        title: item.title,
        domain: item.domain,
        status: item.status,
        excerpt: item.snippet,
        versionCount: null,
        updatedAt: null,
        matchedBy: item.matched_by,
      }))
    : (listQuery.data?.items ?? []).map((item) => ({
        id: item.id,
        title: item.title,
        domain: item.domain,
        status: item.status,
        excerpt: item.current_understanding
          ? collapseWhitespace(item.current_understanding, 90)
          : null,
        versionCount: item.version_count,
        updatedAt: item.updated_at,
        matchedBy: null,
      }))

  const total = activeQuery.data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const loading = activeQuery.isPending
  const error = activeQuery.error

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h1 className="font-heading text-xl font-semibold">知识库</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {loading ? "正在读取…" : `共 ${total} 条`}
          </p>
        </div>
        <Button className="min-h-11 px-4" onClick={() => navigate("/knowledge/new")}>
          <Plus aria-hidden="true" />
          记一条
        </Button>
      </header>

      {/* 搜索与筛选 */}
      <div className="flex flex-col gap-3">
        <div className="relative">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            type="search"
            value={searchInput}
            onChange={(event) => setSearchInput(event.target.value)}
            placeholder="搜索标题或你写过的理解"
            aria-label="搜索知识"
            className="pr-12 pl-9"
          />
          {searchInput && (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="清空搜索词"
              className="absolute top-1/2 right-0 size-11 -translate-y-1/2"
              onClick={() => setSearchInput("")}
            >
              <X aria-hidden="true" />
            </Button>
          )}
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="status-filter">状态</Label>
            <Select
              id="status-filter"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
            >
              <option value="active">在用</option>
              <option value="archived">已归档</option>
              <option value="all">全部</option>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="domain-filter">所属领域</Label>
            <Select
              id="domain-filter"
              value={domainFilter}
              // 搜索时禁用：后端搜索接口不支持按领域过滤。
              // 与其让下拉框看起来能用实际没用，不如直接说明并禁用。
              disabled={isSearching}
              onChange={(event) => setDomainFilter(event.target.value)}
            >
              <option value="">全部领域</option>
              {domainSuggestions.map((suggestion) => (
                <option key={suggestion.domain} value={suggestion.domain}>
                  {suggestion.domain}（{suggestion.count}）
                </option>
              ))}
            </Select>
            {isSearching && (
              <p className="text-xs text-muted-foreground">
                搜索时不能按领域筛选，清空搜索词即可使用。
              </p>
            )}
          </div>
        </div>
      </div>

      {isSearching && searchQuery.data && (
        <p className="text-xs text-muted-foreground">
          找到 {searchQuery.data.total} 条 · 检索方式：
          {STRATEGY_LABELS[searchQuery.data.strategy]}
          {searchQuery.data.strategy === "like" && "（搜索词较短，走关键词匹配）"}
        </p>
      )}

      <ApiErrorNotice error={error} />

      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">正在读取…</p>
      ) : rows.length === 0 ? (
        isSearching ? (
          <EmptyState
            icon={SearchX}
            title="没有找到相关内容"
            description={`「${debouncedSearch.trim()}」在${statusScopeLabel(statusFilter)}里没有匹配项。换个说法，或者把状态筛选改成「全部」再试试。`}
          />
        ) : (
          <EmptyState
            icon={Library}
            title={statusFilter === "archived" ? "没有已归档的知识" : "还没有记录"}
            description={
              statusFilter === "archived"
                ? "归档的知识会出现在这里。归档不是删除，随时可以恢复。"
                : "记下第一条你刚学会的东西。重点不是抄原文，而是用自己的话说一遍——写不出来的地方，就是还没学会的地方。"
            }
            action={
              statusFilter === "active" ? (
                <Button className="min-h-11 px-4" onClick={() => navigate("/knowledge/new")}>
                  <Plus aria-hidden="true" />
                  记一条
                </Button>
              ) : undefined
            }
          />
        )
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((row) => (
            <li key={row.id}>
              {/* 整行都是链接：手机上手指点哪儿都能进详情，不用瞄准小图标 */}
              <Link
                to={`/knowledge/${row.id}`}
                className="flex flex-col gap-1.5 rounded-xl border border-border bg-card p-3.5 transition-colors focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:bg-muted"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="min-w-0 flex-1 text-sm font-medium break-words">
                    {row.title}
                  </h2>
                  {row.status === "archived" && (
                    <Badge variant="secondary">{STATUS_LABELS[row.status]}</Badge>
                  )}
                </div>

                {row.excerpt && (
                  <p className="text-sm break-words text-muted-foreground">{row.excerpt}</p>
                )}

                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  {row.domain && <span>{row.domain}</span>}
                  {row.versionCount !== null && <span>{row.versionCount} 个版本</span>}
                  {row.updatedAt && <span>更新于 {formatRelative(row.updatedAt)}</span>}
                  {row.matchedBy && <span>{MATCHED_BY_LABELS[row.matchedBy]}</span>}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {total > PAGE_SIZE && (
        <nav
          aria-label="分页"
          className="flex items-center justify-between gap-2 border-t border-border pt-3"
        >
          <Button
            variant="outline"
            className="min-h-11 px-4"
            disabled={offset === 0 || loading}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            上一页
          </Button>
          <span className="text-xs tabular-nums text-muted-foreground">
            第 {currentPage} / {pageCount} 页
          </span>
          <Button
            variant="outline"
            className="min-h-11 px-4"
            disabled={offset + PAGE_SIZE >= total || loading}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            下一页
          </Button>
        </nav>
      )}
    </div>
  )
}
