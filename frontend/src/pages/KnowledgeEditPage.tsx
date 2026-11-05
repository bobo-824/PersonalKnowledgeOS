import { ArrowLeft, FileQuestion, NotebookPen } from "lucide-react"
import { Link, useNavigate, useParams } from "react-router-dom"

import { ApiErrorNotice, EmptyState } from "@/components/feedback/Notice"
import { KnowledgeForm } from "@/components/knowledge/KnowledgeForm"
import { Button } from "@/components/ui/button"
import { ApiError } from "@/lib/api"
import { useKnowledge } from "@/lib/knowledge-queries"
import type { KnowledgeRead } from "@/lib/schemas"

/**
 * 新建与编辑共用一个页面。
 *
 * 路由 `/knowledge/new` 是新建，`/knowledge/:knowledgeId/edit` 是编辑。
 * react-router 会给静态段更高的匹配优先级，所以 `new` 不会被 `:knowledgeId` 抢走。
 *
 * 用独立页面而不是弹窗：表单字段多、要写长文本，手机上弹窗会被键盘挤得没法用。
 */
export function KnowledgeEditPage() {
  const { knowledgeId } = useParams<{ knowledgeId: string }>()
  const navigate = useNavigate()
  const isCreate = knowledgeId === undefined

  const {
    data: knowledge,
    isPending,
    error,
  } = useKnowledge(isCreate ? undefined : knowledgeId)

  function handleSubmitted(saved: KnowledgeRead) {
    // replace：保存完不该让"后退"又回到表单页，那会重复提交
    navigate(`/knowledge/${saved.id}`, { replace: true })
  }

  function handleCancel() {
    navigate(isCreate ? "/knowledge" : `/knowledge/${knowledgeId}`, { replace: true })
  }

  if (!isCreate && isPending) {
    return <p className="py-10 text-center text-sm text-muted-foreground">正在读取…</p>
  }

  if (!isCreate && error) {
    const notFound = error instanceof ApiError && error.status === 404
    return (
      <div className="flex flex-col gap-6 sm:gap-8">
        <Button variant="outline" size="sm" className="min-h-11 self-start px-4" asChild>
          <Link to="/knowledge">
            <ArrowLeft aria-hidden="true" />
            返回知识库
          </Link>
        </Button>
        {notFound ? (
          <EmptyState
            icon={FileQuestion}
            title="找不到要编辑的知识"
            description="它可能已经被删除，或者链接是旧的。"
          />
        ) : (
          <ApiErrorNotice error={error} />
        )}
      </div>
    )
  }

  // 表单只在数据到位之后才挂载：它的初始值来自 props，
  // 先挂一个空表单再等数据，用户已经输入的内容会被覆盖掉。
  if (!isCreate && !knowledge) return null

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <Button variant="ghost" size="sm" className="min-h-9 -ml-2 self-start" asChild>
        <Link to={isCreate ? "/knowledge" : `/knowledge/${knowledgeId}`}>
          <ArrowLeft aria-hidden="true" />
          {isCreate ? "知识库" : "返回详情"}
        </Link>
      </Button>

      <header className="flex items-start gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <NotebookPen aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0">
          <h1 className="page-title break-words">
            {isCreate ? "记一条新知识" : `编辑：${knowledge?.title ?? ""}`}
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {isCreate
              ? "重点是「自己的理解」那一栏——用自己的话说一遍，记录本身就完成了第一次学习。"
              : "改动「自己的理解 / 理解依据 / 尚存疑问」会生成新版本，旧版本原样保留。"}
          </p>
        </div>
      </header>

      {isCreate ? (
        <KnowledgeForm
          mode="create"
          onSubmitted={handleSubmitted}
          onCancel={handleCancel}
        />
      ) : (
        <KnowledgeForm
          mode="edit"
          existing={knowledge}
          onSubmitted={handleSubmitted}
          onCancel={handleCancel}
        />
      )}
    </div>
  )
}
