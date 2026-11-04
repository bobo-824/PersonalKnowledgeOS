import { useEffect, useState } from "react"
import { ShieldCheck } from "lucide-react"

import { ApiErrorNotice, Notice } from "@/components/feedback/Notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  REVIEW_CATEGORIES,
  REVIEW_CATEGORY_LABELS,
  REVIEW_DECISIONS,
  REVIEW_DECISION_LABELS,
  type Review,
  type ReviewCategory,
  type ReviewDecision,
} from "@/lib/ai-schemas"
import {
  useAcceptReview,
  useCreateReview,
  useDecideReviewItem,
  useReviews,
} from "@/lib/ai-queries"
import { formatRelative } from "@/lib/format"

/**
 * 知识详情页的审查区（工作流 C 的界面）。
 *
 * 三个设计决定直接来自后端约束：
 * - 处置按 (category, item_index) 定位，按钮状态以服务端返回为准；
 * - 采纳列表**不由前端提交**：服务端只认数据库里 decision='accepted' 的处置，
 *   所以界面上不存在"勾选要采纳哪几条"这个控件；
 * - `new_understanding` 必须由用户打字——预填的是用户自己当前的原文，
 *   在它基础上改仍然是用户的话；照抄 AI 原文且没有任何修改会被后端拦下。
 */

type Props = {
  knowledgeId: string
  currentVersionNumber: number
  currentUnderstanding: string
}

export function ReviewSection({ knowledgeId, currentVersionNumber, currentUnderstanding }: Props) {
  const { data: reviews, isPending, error } = useReviews(knowledgeId)
  const createMutation = useCreateReview(knowledgeId)

  const handleCreate = () => {
    // D-042：付费操作，点击前必须说明
    const confirmed = window.confirm(
      `将把这条知识第 ${currentVersionNumber} 版的理解发给 AI 审查。\n\n这会调用一次 AI（花一次请求的钱）。继续吗？`,
    )
    if (!confirmed) return
    createMutation.mutate()
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-heading text-sm font-medium">AI 审查</h2>
        <p className="text-xs text-muted-foreground">
          AI 只看不改：意见要你逐条表态，采纳后自己重写才会成为新版本
        </p>
      </div>

      <ApiErrorNotice error={error} />
      <ApiErrorNotice error={createMutation.error} />

      <div>
        <Button
          variant="outline"
          className="min-h-11 px-4"
          disabled={createMutation.isPending}
          onClick={handleCreate}
        >
          <ShieldCheck aria-hidden="true" />
          {createMutation.isPending
            ? "审查中…（大约十几秒）"
            : `让 AI 审查当前理解（v${currentVersionNumber}）`}
        </Button>
      </div>

      {isPending && <p className="text-sm text-muted-foreground">正在读取审查记录…</p>}

      {reviews && reviews.length === 0 && (
        <p className="text-sm text-muted-foreground">
          还没有审查记录。点上面的按钮，AI 会从正确点、错误、遗漏、追问、待核实五个方面看一遍。
        </p>
      )}

      {reviews?.map((review) => (
        <ReviewCard
          key={review.id}
          review={review}
          knowledgeId={knowledgeId}
          currentUnderstanding={currentUnderstanding}
        />
      ))}
    </section>
  )
}

/** 五类条目各有自己的字段形状，渲染差异收在这里，处置按钮三个类别共用。 */
function ReviewItem({
  category,
  item,
  decision,
  onDecide,
  deciding,
}: {
  category: ReviewCategory
  item: Record<string, unknown> | string
  decision: ReviewDecision | null
  onDecide: (decision: ReviewDecision) => void
  deciding: boolean
}) {
  const record: Record<string, unknown> | null =
    typeof item === "string" ? null : item
  const parts: { label: string; text: string }[] = []
  if (category === "correct_points") {
    parts.push({ label: "你说对了", text: String(record?.point ?? "") })
    parts.push({ label: "为什么", text: String(record?.why ?? "") })
  } else if (category === "errors") {
    parts.push({ label: "你原来说", text: String(record?.claim ?? "") })
    parts.push({ label: "更准确的说法", text: String(record?.correction ?? "") })
    parts.push({ label: "依据", text: String(record?.evidence ?? "") })
  } else if (category === "omissions") {
    parts.push({ label: "可能漏了", text: String(record?.missing ?? "") })
    parts.push({ label: "为什么重要", text: String(record?.why_it_matters ?? "") })
  } else if (category === "needs_verification") {
    parts.push({ label: "待核实", text: String(record?.item ?? "") })
    parts.push({ label: "怎么核实", text: String(record?.how_to_verify ?? "") })
  } else {
    // follow_up_questions 的条目本身就是一段话，不是对象
    parts.push({ label: "追问", text: typeof item === "string" ? item : "" })
  }

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3">
      {parts.map(
        (part) =>
          part.text && (
            <p key={part.label} className="text-sm leading-relaxed break-words">
              <span className="mr-1.5 text-xs text-muted-foreground">{part.label}：</span>
              {part.text}
            </p>
          ),
      )}
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="对这条意见表态">
        {REVIEW_DECISIONS.map((option) => {
          const active = decision === option
          return (
            <Button
              key={option}
              size="sm"
              variant={active ? "default" : "outline"}
              className="min-h-11 px-3"
              disabled={deciding}
              aria-pressed={active}
              onClick={() => onDecide(option)}
            >
              {REVIEW_DECISION_LABELS[option]}
            </Button>
          )
        })}
      </div>
    </li>
  )
}

function ReviewCard({
  review,
  knowledgeId,
  currentUnderstanding,
}: {
  review: Review
  knowledgeId: string
  currentUnderstanding: string
}) {
  const decideMutation = useDecideReviewItem(review.id, knowledgeId)
  const acceptMutation = useAcceptReview(knowledgeId)

  const [newUnderstanding, setNewUnderstanding] = useState(currentUnderstanding)
  const [changeReason, setChangeReason] = useState("")
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (acceptMutation.isSuccess) {
      setSaved(true)
      setChangeReason("")
    }
  }, [acceptMutation.isSuccess])

  const decisionOf = (category: ReviewCategory, itemIndex: number): ReviewDecision | null =>
    review.decisions.find((d) => d.category === category && d.item_index === itemIndex)
      ?.decision ?? null

  const acceptedCount = review.decisions.filter((d) => d.decision === "accepted").length

  const handleAccept = () => {
    acceptMutation.mutate({
      reviewId: review.id,
      payload: {
        new_understanding: newUnderstanding,
        change_reason: changeReason.trim() || null,
      },
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          这份审查针对 v{review.version_number}
          {review.is_empty && <Badge variant="secondary">AI 没有提出意见</Badge>}
          <span className="ml-auto text-xs font-normal text-muted-foreground">
            {review.model} · {formatRelative(review.created_at)}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ApiErrorNotice error={decideMutation.error} />
        <ApiErrorNotice error={acceptMutation.error} />

        {review.is_empty ? (
          <p className="text-sm text-muted-foreground">
            AI 看了这一版，没有提出修改意见。没意见也是一种结论，这次请求同样会产生费用。
          </p>
        ) : (
          REVIEW_CATEGORIES.map((category) => {
            const items = review.result[category]
            if (items.length === 0) return null
            return (
              <div key={category} className="flex flex-col gap-2">
                <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                  {REVIEW_CATEGORY_LABELS[category]}
                </p>
                <ul className="flex flex-col gap-2">
                  {items.map((item, index) => (
                    <ReviewItem
                      key={index}
                      category={category}
                      item={item}
                      decision={decisionOf(category, index)}
                      onDecide={(decision) =>
                        decideMutation.mutate({ category, itemIndex: index, decision })
                      }
                      deciding={decideMutation.isPending}
                    />
                  ))}
                </ul>
              </div>
            )
          })
        )}

        {/* 采纳区：重写理解 → 新版本。C1 的落点之一。 */}
        <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
          <Label htmlFor={`accept-${review.id}`}>
            重写你的理解，存为新版本（现在生效的是 v{review.version_number}）
          </Label>
          <p className="text-xs text-muted-foreground">
            下面预填的是你自己当前的原文，对照上面的意见修改它。
            AI 的原话不会被程序塞进来——每句话都得出自你的手。
            {!review.is_empty && acceptedCount === 0 && " 建议先对意见逐条表态再重写。"}
          </p>
          <Textarea
            id={`accept-${review.id}`}
            value={newUnderstanding}
            onChange={(event) => setNewUnderstanding(event.target.value)}
            className="min-h-28"
          />
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={`reason-${review.id}`} className="text-xs">
              修改说明（可选，会写进版本历史）
            </Label>
            <Input
              id={`reason-${review.id}`}
              value={changeReason}
              onChange={(event) => setChangeReason(event.target.value)}
              maxLength={200}
              placeholder="例如：采纳了关于 404 归因的纠正"
            />
          </div>
          {saved && acceptMutation.data && (
            <Notice tone="success">
              <p>
                已生成新版本。
                这份审查仍然留在 v{review.version_number}——历史记录不会被改写。
              </p>
            </Notice>
          )}
          <div>
            <Button
              className="min-h-11 px-4"
              disabled={
                acceptMutation.isPending ||
                newUnderstanding.trim().length === 0 ||
                newUnderstanding === currentUnderstanding
              }
              onClick={handleAccept}
            >
              {acceptMutation.isPending ? "保存中…" : "保存为新版本"}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
