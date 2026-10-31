import { Badge } from "@/components/ui/badge"
import { CREATED_BY_LABELS, type KnowledgeVersion } from "@/lib/schemas"
import { formatDateTime, formatRelative } from "@/lib/format"
import { cn } from "@/lib/utils"

/**
 * 版本历史时间线。
 *
 * 两条界面层面的硬要求（Blueprint 1.5 原则 2）：
 *
 * 1. **用户原文完整显示，不截断、不改写**。这是"保留用户原始理解"在 UI 上的落点，
 *    所以用 `whitespace-pre-wrap` 保留用户自己打的换行。
 * 2. **AI 的建议必须和用户原文在视觉上分开**。`ai_revision` 单独一块、带明确标签，
 *    绝不和 `user_understanding` 混排在同一段文字里。
 */

function Section({ label, text }: { label: string; text: string | null }) {
  if (!text) return null
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      <p className="text-sm break-words whitespace-pre-wrap">{text}</p>
    </div>
  )
}

function VersionBody({ version }: { version: KnowledgeVersion }) {
  return (
    <div className="flex flex-col gap-3">
      <Section label="自己的理解" text={version.user_understanding} />
      <Section label="理解依据" text={version.reasoning} />
      <Section label="尚存疑问" text={version.open_questions} />

      {version.ai_revision && (
        <div className="flex flex-col gap-1 rounded-lg border border-dashed border-border bg-muted/40 p-2.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Badge variant="outline">AI 建议</Badge>
            仅供参考，未经你确认不会替换上面的原文
          </p>
          <p className="text-sm break-words whitespace-pre-wrap">
            {version.ai_revision}
          </p>
        </div>
      )}
    </div>
  )
}

function VersionMeta({ version }: { version: KnowledgeVersion }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      <time dateTime={version.created_at} title={formatDateTime(version.created_at)}>
        {formatRelative(version.created_at)}
      </time>
      <span aria-hidden="true">·</span>
      <span>{CREATED_BY_LABELS[version.created_by]}</span>
      {version.change_reason && (
        <>
          <span aria-hidden="true">·</span>
          <span className="text-foreground">{version.change_reason}</span>
        </>
      )}
    </div>
  )
}

type VersionHistoryProps = {
  versions: KnowledgeVersion[]
  currentVersionId: string | null
}

export function VersionHistory({ versions, currentVersionId }: VersionHistoryProps) {
  // 新的在上：最近改了什么，是打开这一页最想知道的事
  const ordered = [...versions].sort((a, b) => b.version_number - a.version_number)

  if (ordered.length === 0) {
    return <p className="text-sm text-muted-foreground">还没有版本记录。</p>
  }

  return (
    <ol className="flex flex-col gap-3">
      {ordered.map((version) => {
        const isCurrent = version.id === currentVersionId
        if (isCurrent) {
          return (
            <li
              key={version.id}
              className={cn(
                "flex flex-col gap-2 rounded-xl border border-primary/30 bg-primary/5 p-3.5",
              )}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-heading text-sm font-semibold">
                  v{version.version_number}
                </span>
                <Badge>当前生效</Badge>
              </div>
              <VersionMeta version={version} />
              <VersionBody version={version} />
            </li>
          )
        }

        // 旧版本折叠起来：一页里堆十几个展开的长文本，当前版本反而找不到了。
        // 用原生 <details> 而不是自己写开合状态——键盘、读屏、触屏都天然可用。
        return (
          <li key={version.id}>
            <details className="group rounded-xl border border-border bg-card p-3.5">
              <summary className="flex min-h-6 cursor-pointer list-none flex-col gap-1 [&::-webkit-details-marker]:hidden">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-heading text-sm font-medium text-muted-foreground">
                    v{version.version_number}
                  </span>
                  <span className="text-xs text-muted-foreground group-open:hidden">
                    点击查看当时的原文
                  </span>
                </div>
                <VersionMeta version={version} />
              </summary>
              <div className="mt-3 border-t border-border pt-3">
                <VersionBody version={version} />
              </div>
            </details>
          </li>
        )
      })}
    </ol>
  )
}
