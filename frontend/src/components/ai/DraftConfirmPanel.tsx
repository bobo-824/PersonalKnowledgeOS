import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { Check } from "lucide-react"

import { ApiErrorNotice, Notice } from "@/components/feedback/Notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { confirmDraft, type DraftConfirmPayload } from "@/lib/ai-api"
import type { KnowledgeDraft } from "@/lib/ai-schemas"
import { SOURCE_KIND_LABELS } from "@/lib/schemas"
import { hasSubstantiveContent } from "@/lib/format"

/**
 * 知识草稿确认面板（工作流 B 的第②③步）。
 *
 * 布局上把三类内容**分开摆**，这是 C1 在界面上的落点：
 * - 左「来自用户」：AI 从对话里誊写的你的原话，可编辑，但要自己核对；
 * - 右「AI 补充」：AI 认为你理解里缺的东西，只作参考——
 *   **没有"一键并入理解"按钮**。要采纳就得自己打字写进理解正文，
 *   程序一拼，这条记录就说不清哪句是谁说的了。
 */

type Props = {
  draft: KnowledgeDraft
  onDiscard: () => void
}

export function DraftConfirmPanel({ draft, onDiscard }: Props) {
  const [title, setTitle] = useState(draft.title)
  const [domain, setDomain] = useState(draft.domain ?? "")
  const [summary, setSummary] = useState(draft.summary ?? "")
  const [understanding, setUnderstanding] = useState(draft.user_understanding)
  const [reasoning, setReasoning] = useState(draft.reasoning ?? "")
  const [openQuestions, setOpenQuestions] = useState(draft.open_questions ?? "")

  const [saved, setSaved] = useState<{ knowledgeId: string; replayed: boolean } | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<Error | null>(null)

  // 幂等键在草稿加载时生成、确认成功前保持不变：
  // 保存后用户再点（或网络重试）服务端都会返回同一条记录。
  const idempotencyKey = useRef(crypto.randomUUID())

  // 换了一份新草稿（重新提取）就重置整个面板
  useEffect(() => {
    setTitle(draft.title)
    setDomain(draft.domain ?? "")
    setSummary(draft.summary ?? "")
    setUnderstanding(draft.user_understanding)
    setReasoning(draft.reasoning ?? "")
    setOpenQuestions(draft.open_questions ?? "")
    setSaved(null)
    setConfirmError(null)
    idempotencyKey.current = crypto.randomUUID()
  }, [draft])

  const canConfirm =
    hasSubstantiveContent(understanding) && title.trim().length > 0 && !confirming

  const handleConfirm = () => {
    setConfirming(true)
    setConfirmError(null)
    const payload: DraftConfirmPayload = {
      title: title.trim(),
      domain: domain.trim() || null,
      summary: summary.trim() || null,
      user_understanding: understanding,
      ai_supplements: draft.ai_supplements,
      reasoning: reasoning.trim() || null,
      open_questions: openQuestions.trim() || null,
      suggested_sources: draft.suggested_sources,
    }
    confirmDraft(payload, idempotencyKey.current)
      .then((result) => {
        setSaved({ knowledgeId: result.knowledge.id, replayed: result.replayed })
      })
      .catch(setConfirmError)
      .finally(() => setConfirming(false))
  }

  if (saved) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>已保存为知识</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {saved.replayed && (
            <Notice tone="info">
              这份草稿刚才已经保存过了，没有创建重复记录——这是防重复提交的保护在起作用。
            </Notice>
          )}
          <div className="flex flex-wrap gap-2">
            <Button className="min-h-11 px-4" asChild>
              <Link to={`/knowledge/${saved.knowledgeId}`}>
                <Check aria-hidden="true" />
                去看这条知识
              </Link>
            </Button>
            <Button variant="outline" className="min-h-11 px-4" onClick={onDiscard}>
              继续留在对话
            </Button>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>知识草稿 —— 确认后才算你的知识</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <ApiErrorNotice error={confirmError} />

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="draft-title">名称</Label>
            <Input
              id="draft-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="draft-domain">所属领域</Label>
            <Input
              id="draft-domain"
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
              placeholder="例如 Web 开发"
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="draft-summary">摘要</Label>
          <Textarea
            id="draft-summary"
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            className="min-h-16"
          />
        </div>

        {/* ===== 用户原文与 AI 补充分栏，是这一页最重要的一处布局 ===== */}
        <div className="grid gap-4 md:grid-cols-2">
          <div className="flex flex-col gap-1.5 rounded-lg border border-border p-3">
            <Label htmlFor="draft-understanding">我的理解（来自你，AI 已誊写）</Label>
            <p className="text-xs text-muted-foreground">
              这是 AI 从你的原话里整理的，逐字核对一下，AI 也有誊错的可能。
            </p>
            <Textarea
              id="draft-understanding"
              value={understanding}
              onChange={(event) => setUnderstanding(event.target.value)}
              className="min-h-32"
            />
            {!hasSubstantiveContent(understanding) && (
              <p className="text-xs text-destructive">
                「我的理解」需要写出实际内容才能保存——AI 补充的话不能替你理解。
              </p>
            )}
          </div>

          <div className="flex flex-col gap-2 rounded-lg border border-dashed border-border bg-muted/40 p-3">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              AI 补充（仅供参考）
            </p>
            <p className="text-xs text-muted-foreground">
              要采纳哪条，请自己组织成话写进左边「我的理解」——
              程序不会替你拼，这样每句话才说得清是谁的理解。
            </p>
            {draft.ai_supplements.length === 0 ? (
              <p className="text-sm text-muted-foreground">AI 这次没有补充。</p>
            ) : (
              <ul className="flex list-disc flex-col gap-1.5 pl-4">
                {draft.ai_supplements.map((item, index) => (
                  <li key={index} className="text-sm leading-relaxed break-words">
                    {item}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="draft-reasoning">理解依据（可选）</Label>
          <Textarea
            id="draft-reasoning"
            value={reasoning}
            onChange={(event) => setReasoning(event.target.value)}
            className="min-h-16"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="draft-questions">尚存疑问（可选）</Label>
          <Textarea
            id="draft-questions"
            value={openQuestions}
            onChange={(event) => setOpenQuestions(event.target.value)}
            className="min-h-16"
          />
        </div>

        {draft.suggested_sources.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
              AI 建议的学习来源（会一起保存，可之后在编辑页修改）
            </p>
            <ul className="flex flex-wrap gap-1.5">
              {draft.suggested_sources.map((source, index) => (
                <li key={index}>
                  <Badge variant="outline">
                    {SOURCE_KIND_LABELS[source.source_type]} · {source.title}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button className="min-h-11 px-4" disabled={!canConfirm} onClick={handleConfirm}>
            <Check aria-hidden="true" />
            {confirming ? "保存中…" : "确认保存为知识"}
          </Button>
          <Button variant="outline" className="min-h-11 px-4" onClick={onDiscard}>
            先不要，丢弃草稿
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
