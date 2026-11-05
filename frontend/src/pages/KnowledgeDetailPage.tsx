import {
  Archive,
  ArrowLeft,
  FileQuestion,
  Link2,
  Pencil,
  PauseCircle,
  PlayCircle,
  RotateCcw,
} from "lucide-react"
import { Link, useNavigate, useParams } from "react-router-dom"

import { ApiErrorNotice, EmptyState, Notice } from "@/components/feedback/Notice"
import { ReviewSection } from "@/components/ai/ReviewSection"
import { VersionHistory } from "@/components/knowledge/VersionHistory"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ApiError } from "@/lib/api"
import { formatDateTime, formatRelative } from "@/lib/format"
import {
  useArchiveKnowledge,
  useKnowledge,
  useRestoreKnowledge,
} from "@/lib/knowledge-queries"
import {
  relationTypeLabel,
} from "@/lib/relation-schemas"
import { useRelated } from "@/lib/relation-queries"
import {
  MODE_LABELS,
  RATING_LABELS,
  STATE_LABELS,
  type ReviewRating,
  type ReviewSessionRow,
} from "@/lib/review-schemas"
import { useReviewHistory, useSchedule, useSetSuspension } from "@/lib/review-queries"
import {
  KNOWLEDGE_SOURCE_TYPE_LABELS,
  SOURCE_KIND_LABELS,
  STATUS_LABELS,
  currentVersionOf,
} from "@/lib/schemas"

function Field({ label, text }: { label: string; text: string | null }) {
  if (!text) return null
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      {/* whitespace-pre-wrap：用户自己打的换行必须原样保留，
          这是"保留用户原始理解"在显示层的最低要求 */}
      <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">{text}</p>
    </div>
  )
}

function HistoryRow({ row }: { row: ReviewSessionRow }) {
  return (
    <li className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <span>{formatDateTime(row.reviewed_at)}</span>
      <Badge variant={row.rating === "again" ? "secondary" : "outline"}>
        {RATING_LABELS[row.rating as ReviewRating]}
      </Badge>
      <span>{MODE_LABELS[row.mode as "self_test" | "ai_chat"]}</span>
      {row.suggested_rating && row.suggested_rating !== row.rating && (
        <span>（AI 当时建议「{RATING_LABELS[row.suggested_rating]}」，你没采纳）</span>
      )}
      {row.duration_ms !== null && (
        <span>用了 {Math.max(1, Math.round(row.duration_ms / 1000))} 秒</span>
      )}
    </li>
  )
}

/**
 * 复习排期卡。辅助信息：加载失败或这条知识还没有排期行就整个不显示，
 * 不打扰"看理解"这个主任务。
 */
function ReviewScheduleCard({ knowledgeId }: { knowledgeId: string }) {
  const { data: schedule, isPending, error } = useSchedule(knowledgeId)
  const { data: history } = useReviewHistory(knowledgeId)
  const suspension = useSetSuspension(knowledgeId)

  if (isPending || error || !schedule) return null

  const suspended = schedule.status === "suspended"
  const rows = [...(history ?? [])].reverse().slice(0, 10)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">复习排期</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={suspended ? "secondary" : "outline"}>
            {suspended ? "已暂停" : "进行中"}
          </Badge>
          <Badge variant="outline">{STATE_LABELS[schedule.state]}</Badge>
          <span className="text-sm">
            下次复习：{formatDateTime(schedule.due)}（{formatRelative(schedule.due)}）
          </span>
        </div>
        {schedule.last_review && (
          <p className="text-xs text-muted-foreground">
            上次复习于 {formatDateTime(schedule.last_review)}
            {history && history.length > 0 && `，累计 ${history.length} 次`}
            。
          </p>
        )}

        <div className="flex flex-col gap-2">
          <Button
            variant="outline"
            className="min-h-11 self-start px-4"
            disabled={suspension.isPending}
            onClick={() => suspension.mutate(!suspended)}
          >
            {suspended ? <PlayCircle aria-hidden="true" /> : <PauseCircle aria-hidden="true" />}
            {suspension.isPending ? "处理中…" : suspended ? "恢复复习" : "暂停复习"}
          </Button>
          <p className="text-xs text-muted-foreground">
            {suspended
              ? "暂停期间不会出现在复习中心，恢复后从现在重新排起，错过的不会补。"
              : "嫌这条太吵可以先暂停，知识本身不受影响。"}
          </p>
        </div>

        {suspension.error && <ApiErrorNotice error={suspension.error} />}

        {rows.length > 0 && (
          <div className="flex flex-col gap-1.5 border-t border-border pt-2">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              最近的复习记录
            </p>
            <ul className="flex flex-col gap-1">
              {rows.map((row) => (
                <HistoryRow key={row.id} row={row} />
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * 「相关知识」卡。辅助信息：加载失败（后端未升级等）就整卡不显示，
 * 不打扰"看理解"这个主任务。已确认的关系点标题可跳转；
 * 候选只是提示，判断入口在图谱页。
 */
function RelatedKnowledgeCard({ knowledgeId }: { knowledgeId: string }) {
  const { data, isPending, error } = useRelated(knowledgeId)
  if (isPending || error || !data) return null

  const hasContent = data.relations.length > 0 || data.candidates.length > 0
  if (!hasContent) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>相关知识</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {data.relations.length > 0 && (
          <ul className="flex flex-col gap-2">
            {data.relations.map((rel) => (
              <li
                key={rel.id}
                className="flex flex-col gap-1 rounded-lg border border-border bg-card p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge>{relationTypeLabel(rel.relation_type)}</Badge>
                  <Link
                    to={`/knowledge/${rel.other_id}`}
                    className="min-w-0 text-sm font-medium underline-offset-4 break-words hover:underline"
                  >
                    {rel.other_title}
                  </Link>
                </div>
                <p className="text-xs break-words text-muted-foreground">
                  {rel.explanation}
                </p>
              </li>
            ))}
          </ul>
        )}
        {data.candidates.length > 0 && (
          <p className="text-xs text-muted-foreground">
            另有 {data.candidates.length} 条可能的关联等待 AI 判断——去{" "}
            <Link to="/graph" className="text-primary underline-offset-4 hover:underline">
              知识图谱
            </Link>{" "}
            页处理。
          </p>
        )}
      </CardContent>
    </Card>
  )
}

export function KnowledgeDetailPage() {
  const { knowledgeId } = useParams<{ knowledgeId: string }>()
  const navigate = useNavigate()

  const { data: knowledge, isPending, error } = useKnowledge(knowledgeId)
  const archiveMutation = useArchiveKnowledge()
  const restoreMutation = useRestoreKnowledge()

  if (isPending) {
    return <p className="py-10 text-center text-sm text-muted-foreground">正在读取…</p>
  }

  if (error) {
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
            title="找不到这条知识"
            description="它可能已经被删除，或者链接是旧的。"
          />
        ) : (
          <ApiErrorNotice error={error} />
        )}
      </div>
    )
  }

  if (!knowledge) return null

  const current = currentVersionOf(knowledge)
  const isArchived = knowledge.status === "archived"
  const statusMutation = isArchived ? restoreMutation : archiveMutation

  // 用箭头函数常量而不是 function 声明：function 声明会被提升，
  // TypeScript 认为它可能在 `if (!knowledge) return null` 之前就被调用，
  // 于是不把非空收窄带进函数体里，报 "possibly undefined"。
  const handleToggleArchive = () => {
    // 归档会把这条数据从默认列表和搜索里移走，误操作的代价比误改标题大，
    // 所以必须确认一次。用原生 confirm：手机上同样可用，也不用额外引入弹窗组件。
    const question = isArchived
      ? `把「${knowledge.title}」恢复到在用状态？`
      : `归档「${knowledge.title}」？\n\n归档不是删除，数据完整保留，只是默认列表和搜索里不再显示，随时可以恢复。`
    if (!window.confirm(question)) return
    statusMutation.mutate(knowledge.id)
  }

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <Button variant="ghost" size="sm" className="min-h-9 -ml-2 self-start" asChild>
        <Link to="/knowledge">
          <ArrowLeft aria-hidden="true" />
          知识库
        </Link>
      </Button>

      <ApiErrorNotice error={statusMutation.error} />

      <header className="flex flex-col gap-3">
        <div className="flex flex-col gap-2">
          <h1 className="page-title break-words">
            {knowledge.title}
          </h1>
          <div className="flex flex-wrap items-center gap-1.5">
            {knowledge.domain && <Badge variant="outline">{knowledge.domain}</Badge>}
            <Badge variant={isArchived ? "secondary" : "outline"}>
              {STATUS_LABELS[knowledge.status]}
            </Badge>
            <Badge variant="outline">
              {KNOWLEDGE_SOURCE_TYPE_LABELS[knowledge.source_type]}
            </Badge>
            {knowledge.versions.length > 1 && (
              <Badge variant="outline">{knowledge.versions.length} 个版本</Badge>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row">
          <Button
            className="min-h-11 px-4"
            onClick={() => navigate(`/knowledge/${knowledge.id}/edit`)}
          >
            <Pencil aria-hidden="true" />
            编辑
          </Button>
          <Button
            variant="outline"
            className="min-h-11 px-4"
            disabled={statusMutation.isPending}
            onClick={handleToggleArchive}
          >
            {isArchived ? <RotateCcw aria-hidden="true" /> : <Archive aria-hidden="true" />}
            {statusMutation.isPending ? "处理中…" : isArchived ? "恢复" : "归档"}
          </Button>
        </div>
      </header>

      {isArchived && (
        <Notice tone="info">
          这条知识已归档，默认列表和搜索里不会出现。数据完整保留，点「恢复」即可回到在用状态。
        </Notice>
      )}

      <Field label="摘要" text={knowledge.summary} />

      {/* 当前理解 —— 这一页的主体 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            我的理解
            {current && <Badge>v{current.version_number}</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {current ? (
            <>
              <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                {current.user_understanding}
              </p>
              <Field label="理解依据" text={current.reasoning} />
              <Field label="尚存疑问" text={current.open_questions} />
              {current.ai_revision && (
                <div className="flex flex-col gap-1 rounded-lg border border-dashed border-border bg-muted/40 p-2.5">
                  <p className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-muted-foreground">
                    <Badge variant="outline">AI 建议</Badge>
                    仅供参考。未经你确认，上面你自己写的原文不会被替换
                  </p>
                  <p className="text-sm break-words whitespace-pre-wrap">
                    {current.ai_revision}
                  </p>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              这条记录还没有理解内容——正常情况下不该出现，可能是数据有问题。
            </p>
          )}
        </CardContent>
      </Card>

      {/* AI 审查：意见要逐条表态，采纳后由用户重写成新版本（工作流 C） */}
      {current && !isArchived && (
        <ReviewSection
          knowledgeId={knowledge.id}
          currentVersionNumber={current.version_number}
          currentUnderstanding={current.user_understanding}
        />
      )}

      {/* 复习排期：辅助信息，没有排期行（老数据）就整卡不显示 */}
      <ReviewScheduleCard knowledgeId={knowledge.id} />

      {/* 相关知识：已确认的关系 + 等判断的候选，没有就整卡不显示 */}
      {!isArchived && <RelatedKnowledgeCard knowledgeId={knowledge.id} />}

      {/* 学习来源 */}
      <section className="flex flex-col gap-2">
        <h2 className="font-heading text-sm font-medium">学习来源</h2>
        {knowledge.sources.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            没有记录来源。半年后想追回"这是从哪学的"就没有线索了，建议编辑时补上。
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {knowledge.sources.map((source) => (
              <li
                key={source.id}
                className="flex flex-col gap-1 rounded-lg border border-border bg-card p-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">
                    {SOURCE_KIND_LABELS[source.source_type]}
                  </Badge>
                  <span className="min-w-0 text-sm font-medium break-words">
                    {source.title}
                  </span>
                </div>
                {source.url_or_reference && (
                  // 外部链接一律 rel="noreferrer noopener"：本地应用没有理由把
                  // referrer 传给第三方，也不该让新页面拿到 window.opener
                  <a
                    href={source.url_or_reference}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="flex items-center gap-1 text-xs text-primary underline-offset-4 break-all hover:underline"
                  >
                    <Link2 aria-hidden="true" className="size-3 shrink-0" />
                    {source.url_or_reference}
                  </a>
                )}
                {source.note && (
                  <p className="text-xs break-words text-muted-foreground">
                    {source.note}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* 版本历史 */}
      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-heading text-sm font-medium">理解的历史版本</h2>
          <p className="text-xs text-muted-foreground">
            追加式记录，旧版本永不被覆盖
          </p>
        </div>
        <VersionHistory
          versions={knowledge.versions}
          currentVersionId={knowledge.current_version_id}
        />
      </section>

      <footer className="flex flex-col gap-1 border-t border-border pt-3 text-xs text-muted-foreground">
        <span>创建于 {formatDateTime(knowledge.created_at)}</span>
        <span>最后修改于 {formatRelative(knowledge.updated_at)}</span>
      </footer>
    </div>
  )
}
