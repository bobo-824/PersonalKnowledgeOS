import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import {
  ArrowLeft,
  CalendarCheck2,
  Check,
  Eye,
  ListChecks,
  MessageSquareQuote,
  Send,
  Sparkles,
} from "lucide-react"

import { ApiErrorNotice, EmptyState, Notice } from "@/components/feedback/Notice"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import { AiStreamError, streamChatMessage } from "@/lib/ai-api"
import { AI_ERROR_KIND_HINTS } from "@/lib/ai-schemas"
import { formatDateTime, formatRelative } from "@/lib/format"
import { useKnowledge } from "@/lib/knowledge-queries"
import { fetchRatingSuggestion, startReviewSession } from "@/lib/review-api"
import { useDueList, useSubmitRating } from "@/lib/review-queries"
import {
  MODE_LABELS,
  RATING_HINTS,
  RATING_LABELS,
  RATINGS,
  STATE_LABELS,
  type DueItem,
  type RatingSuggestion,
  type ReviewMode,
  type ReviewOutcome,
  type ReviewRating,
} from "@/lib/review-schemas"
import { useQueryClient } from "@tanstack/react-query"

/**
 * 复习中心（D-060~D-063 的界面）。
 *
 * 两种模式共用同一套四级评级按钮，提交的 `rating` 永远来自用户的一次点击：
 * AI 的评级建议只在按钮旁高亮提示（C7），用户点了哪个才算哪个。
 * AI 起步或中途失败都不拦路——降级成纯自测照样能完成复习（D-062）。
 */

const CHAT_TEXT_MAX = 4000

type ChatMessage = { role: "user" | "assistant"; content: string }

/** 四级评级按钮。`highlight` 是 AI 建议（只有建议，没有决定）。 */
function RatingButtons({
  highlight,
  disabled,
  onRate,
}: {
  highlight: ReviewRating | null
  disabled: boolean
  onRate: (rating: ReviewRating) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {RATINGS.map((rating) => (
        <Button
          key={rating}
          variant={highlight === rating ? "default" : "outline"}
          className="min-h-14 flex-col items-start gap-0.5 px-3 py-2"
          disabled={disabled}
          onClick={() => onRate(rating)}
        >
          <span className="flex w-full flex-wrap items-center gap-1 text-sm font-medium">
            {RATING_LABELS[rating]}
            {highlight === rating && (
              <Badge variant="secondary" className="px-1 py-0 text-[10px]">
                AI 建议
              </Badge>
            )}
          </span>
          <span className="text-xs font-normal text-muted-foreground">
            {RATING_HINTS[rating]}
          </span>
        </Button>
      ))}
    </div>
  )
}

function OutcomeCard({ outcome, onBack }: { outcome: ReviewOutcome; onBack: () => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Check aria-hidden="true" className="size-5 text-green-600" />
          这次复习记下了
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm">
          你评了「{RATING_LABELS[outcome.rating]}」（{MODE_LABELS[outcome.mode]}），
          下次复习安排在{" "}
          <span className="font-medium">{formatDateTime(outcome.due)}</span>
          （{formatRelative(outcome.due)}）。
        </p>
        <p className="text-xs text-muted-foreground">
          累计复习 {outcome.stats.reps} 次，其中忘记 {outcome.stats.lapses} 次；当前阶段是
          「{STATE_LABELS[outcome.state]}」。
        </p>
        <Button variant="outline" className="min-h-11 self-start px-4" onClick={onBack}>
          <ListChecks aria-hidden="true" />
          回到期列表
        </Button>
      </CardContent>
    </Card>
  )
}

function PageHeader({ count }: { count: number | null }) {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="font-heading text-xl font-semibold">复习中心</h1>
      <p className="text-sm text-muted-foreground">
        {count === null
          ? "到期就会出现在下面的列表里，凭记忆回答，再诚实评级。"
          : count === 0
            ? "今天暂时没有要复习的。"
            : `还有 ${count} 条等你复习。`}
      </p>
    </header>
  )
}

export function ReviewCenterPage() {
  const queryClient = useQueryClient()
  const { data: due, isPending, error } = useDueList()

  const [active, setActive] = useState<DueItem | null>(null)
  const [mode, setMode] = useState<ReviewMode | null>(null)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [outcome, setOutcome] = useState<ReviewOutcome | null>(null)

  // AI 对话（工作流 D）：消息本地渲染，会话原文由服务端落库；评级建议单独取
  const [chat, setChat] = useState<{ conversationId: string; messages: ChatMessage[] } | null>(
    null,
  )
  const [chatInput, setChatInput] = useState("")
  const [pendingUserText, setPendingUserText] = useState<string | null>(null)
  const [streamingText, setStreamingText] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<{ message: string; kind: string | null } | null>(
    null,
  )
  const [aiStartError, setAiStartError] = useState<Error | null>(null)
  const [suggestion, setSuggestion] = useState<RatingSuggestion | null>(null)
  const [suggestionLoading, setSuggestionLoading] = useState(false)
  const [suggestionError, setSuggestionError] = useState<Error | null>(null)

  const ratingMutation = useSubmitRating(active?.knowledge_id ?? "")

  // 自测模式翻开答案后才取理解正文——列表刻意不带答案，这里补上
  const { data: knowledge } = useKnowledge(
    mode === "self_test" && revealed ? active?.knowledge_id : undefined,
  )

  const scrollRef = useRef<HTMLDivElement>(null)
  const chatBusy = pendingUserText !== null || streamingText !== null

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chat?.messages.length, streamingText, pendingUserText])

  const reset = () => {
    setActive(null)
    setMode(null)
    setStartedAt(null)
    setRevealed(false)
    setOutcome(null)
    setChat(null)
    setChatInput("")
    setPendingUserText(null)
    setStreamingText(null)
    setStreamError(null)
    setAiStartError(null)
    setSuggestion(null)
    setSuggestionLoading(false)
    setSuggestionError(null)
  }

  const beginSelfTest = () => {
    setMode("self_test")
    setRevealed(false)
    setStartedAt(Date.now())
  }

  const beginAiChat = () => {
    if (!active) return
    // D-042：付费操作，点击前说明
    const confirmed = window.confirm(
      `用 AI 对话复习「${active.title}」？\n\nAI 会先提一个问题，你用自己的话回答，它再追问，最后给评级建议。\n开这一场会调用 AI（花请求的钱），之后每次追问、要建议各花一次。继续吗？\n\n不想花钱可以选「纯自测」，同样记入复习排期。`,
    )
    if (!confirmed) return
    setAiStartError(null)
    startReviewSession(active.knowledge_id)
      .then((result) => {
        setMode("ai_chat")
        setStartedAt(Date.now())
        setChat({
          conversationId: result.conversation_id,
          messages: [{ role: "assistant", content: result.first_question }],
        })
      })
      .catch((error: unknown) => {
        setAiStartError(error instanceof Error ? error : new Error(String(error)))
      })
  }

  const sendChat = () => {
    const text = chatInput.trim()
    if (!chat || !text || chatBusy) return
    setPendingUserText(text)
    setStreamError(null)
    setStreamingText("")
    let received = ""
    streamChatMessage(chat.conversationId, text, {
      onChunk: (chunk) => {
        received += chunk
        setStreamingText(received)
      },
    })
      .then(() => {
        setChat((prev) =>
          prev
            ? {
                ...prev,
                messages: [
                  ...prev.messages,
                  { role: "user", content: text },
                  { role: "assistant", content: received },
                ],
              }
            : prev,
        )
      })
      .catch((error: unknown) => {
        setStreamError(
          error instanceof AiStreamError
            ? { message: error.message, kind: error.errorKind }
            : {
                message: error instanceof Error ? error.message : "发送失败。",
                kind: null,
              },
        )
        // 用户那句话服务端已落库，本地也要显示出来，别让话"消失"
        setChat((prev) =>
          prev
            ? { ...prev, messages: [...prev.messages, { role: "user", content: text }] }
            : prev,
        )
      })
      .finally(() => {
        setPendingUserText(null)
        setStreamingText(null)
        setChatInput("")
      })
  }

  const askSuggestion = () => {
    if (!active || !chat || chatBusy || suggestionLoading) return
    const confirmed = window.confirm(
      "把这场复习对话发给 AI，请它给出评级建议？\n\n这会调用一次 AI（花一次请求的钱）。建议仅供参考，最后还是你自己点评级。",
    )
    if (!confirmed) return
    setSuggestionLoading(true)
    setSuggestionError(null)
    fetchRatingSuggestion(active.knowledge_id, chat.conversationId)
      .then(setSuggestion)
      .catch((error: unknown) => {
        setSuggestionError(error instanceof Error ? error : new Error(String(error)))
      })
      .finally(() => setSuggestionLoading(false))
  }

  const handleRate = (rating: ReviewRating) => {
    if (!active || !mode) return
    // 用户点击是唯一权威（C7）；AI 建议只进存档，排期计算不读它
    ratingMutation.mutate(
      {
        rating,
        mode,
        suggested_rating: suggestion?.suggested_rating,
        ai_comment: suggestion?.evaluation,
        conversation_id: chat?.conversationId,
        duration_ms: startedAt !== null ? Date.now() - startedAt : undefined,
      },
      {
        onSuccess: (result) => {
          setOutcome(result)
          // 排期变了：到期列表、条数一起刷新（mutation 内部已失效 ["review"] 前缀，
          // 这里只为确保界面上"还有几条"立即重算）
          void queryClient.invalidateQueries({ queryKey: ["review"] })
        },
      },
    )
  }

  // ===== 结果卡 =====
  if (active && outcome) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader count={due?.count ?? null} />
        <p className="text-sm font-medium break-words">{active.title}</p>
        <OutcomeCard outcome={outcome} onBack={reset} />
      </div>
    )
  }

  // ===== 到期列表 =====
  if (!active) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader count={due?.count ?? null} />

        <ApiErrorNotice error={error} />

        {isPending && (
          <p className="py-10 text-center text-sm text-muted-foreground">正在读取…</p>
        )}

        {due && due.items.length === 0 && (
          <EmptyState
            icon={CalendarCheck2}
            title="现在没有到期的知识"
            description="到期后会自动出现在这里。也可以先去记点新知识。"
            action={
              <Button variant="outline" className="min-h-11 px-4" asChild>
                <Link to="/knowledge">去知识库</Link>
              </Button>
            }
          />
        )}

        {due && due.items.length > 0 && (
          <ul className="flex flex-col gap-2">
            {due.items.map((item) => (
              <li key={item.knowledge_id}>
                <button
                  type="button"
                  onClick={() => {
                    reset()
                    setActive(item)
                  }}
                  className="flex w-full flex-col gap-1.5 rounded-xl border border-border bg-card p-3.5 text-left transition-colors hover:border-primary/40"
                >
                  <span className="text-sm font-medium break-words">{item.title}</span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    {item.domain && <Badge variant="outline">{item.domain}</Badge>}
                    <Badge variant="secondary">{STATE_LABELS[item.state]}</Badge>
                    <span className="text-xs text-muted-foreground">
                      已复习 {item.reps} 次 · 到期 {formatRelative(item.due)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {due && due.items.length > 0 && (
          <p className="text-xs text-muted-foreground">
            点进去先自己回忆，再对照答案或与 AI 对答，最后诚实评级——评得越低，下次来得越快。
          </p>
        )}
      </div>
    )
  }

  // ===== 复习进行中 =====
  const currentVersion = knowledge?.versions.find(
    (version) => version.id === knowledge.current_version_id,
  )

  return (
    <div className="flex flex-col gap-4">
      <PageHeader count={null} />

      <div className="flex flex-col gap-1">
        <Button variant="ghost" size="sm" className="min-h-9 -ml-2 self-start" onClick={reset}>
          <ArrowLeft aria-hidden="true" />
          回到期列表
        </Button>
        <h1 className="font-heading text-lg leading-snug font-semibold break-words">
          {active.title}
        </h1>
      </div>

      <ApiErrorNotice error={ratingMutation.error} />

      {/* 还没选模式 */}
      {!mode && (
        <>
          <ApiErrorNotice error={aiStartError} />
          <Card>
            <CardContent className="flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">
                先在脑子里把这条知识的要点回忆一遍，再选一种方式作答。
              </p>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Button className="min-h-11 px-4" onClick={beginSelfTest}>
                  <Eye aria-hidden="true" />
                  纯自测
                </Button>
                <Button variant="outline" className="min-h-11 px-4" onClick={beginAiChat}>
                  <MessageSquareQuote aria-hidden="true" />
                  AI 对话复习（花请求的钱）
                </Button>
              </div>
              {aiStartError && (
                <Notice tone="info">
                  <p>AI 对话没开起来，纯自测不受影响，复习照常进行。</p>
                </Notice>
              )}
              <p className="text-xs text-muted-foreground">
                两种方式效果都记入复习排期；AI 不在线时纯自测照常可用。
              </p>
            </CardContent>
          </Card>
        </>
      )}

      {/* 纯自测 */}
      {mode === "self_test" && (
        <Card>
          <CardHeader>
            <CardTitle>回忆之后，对照你当时写的理解</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {!revealed ? (
              <>
                <p className="text-sm text-muted-foreground">
                  先别偷看。试着把要点完整说一遍，再点下面的按钮对照。
                </p>
                <Button className="min-h-11 self-start px-4" onClick={() => setRevealed(true)}>
                  <Eye aria-hidden="true" />
                  显示我的理解
                </Button>
              </>
            ) : !knowledge ? (
              <p className="text-sm text-muted-foreground">正在读取你的理解…</p>
            ) : (
              <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                {currentVersion?.user_understanding}
              </p>
            )}

            {revealed && (
              <>
                <p className="border-t border-border pt-3 text-sm font-medium">
                  对照之后，刚才想起来得有多顺？
                </p>
                <RatingButtons
                  highlight={null}
                  disabled={ratingMutation.isPending}
                  onRate={handleRate}
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* AI 对话（工作流 D） */}
      {mode === "ai_chat" && chat && (
        <Card>
          <CardHeader>
            <CardTitle>回答 AI 的问题，之后自己定评级</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div
              ref={scrollRef}
              className="flex max-h-[45vh] min-h-48 flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-muted/30 p-3"
            >
              {chat.messages.map((message, index) => (
                <div
                  key={index}
                  className={message.role === "user" ? "flex justify-end" : "flex justify-start"}
                >
                  <div
                    className={
                      message.role === "user"
                        ? "max-w-[85%] rounded-2xl rounded-br-sm bg-primary/10 px-3.5 py-2.5"
                        : "max-w-[85%] rounded-2xl rounded-bl-sm bg-background px-3.5 py-2.5"
                    }
                  >
                    <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                      {message.content}
                    </p>
                  </div>
                </div>
              ))}
              {pendingUserText !== null && (
                <div className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary/10 px-3.5 py-2.5">
                    <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                      {pendingUserText}
                    </p>
                  </div>
                </div>
              )}
              {streamingText !== null && (
                <div className="flex justify-start">
                  <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-background px-3.5 py-2.5">
                    <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                      {streamingText || "…"}
                    </p>
                  </div>
                </div>
              )}
            </div>

            {streamError && (
              <Notice tone="error">
                <p>{streamError.message}</p>
                {streamError.kind && AI_ERROR_KIND_HINTS[streamError.kind] && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {AI_ERROR_KIND_HINTS[streamError.kind]}
                  </p>
                )}
                <p className="mt-1 text-xs text-muted-foreground">
                  不影响完成复习：直接在下面点评级就行。
                </p>
              </Notice>
            )}

            <div className="flex items-end gap-2">
              <Textarea
                aria-label="输入回答"
                value={chatInput}
                onChange={(event) => setChatInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault()
                    sendChat()
                  }
                }}
                placeholder="用自己的话回答，说不全也没关系…"
                maxLength={CHAT_TEXT_MAX}
                className="min-h-11 flex-1"
              />
              <Button
                className="min-h-11 px-4"
                disabled={chatBusy || chatInput.trim().length === 0}
                onClick={sendChat}
              >
                <Send aria-hidden="true" />
                发送
              </Button>
            </div>

            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
              <Button
                variant="outline"
                className="min-h-11 px-4"
                disabled={chatBusy || suggestionLoading || chat.messages.length < 2}
                onClick={askSuggestion}
              >
                <Sparkles aria-hidden="true" />
                {suggestionLoading ? "AI 正在评估…" : "问 AI 要评级建议"}
              </Button>
              <p className="text-xs text-muted-foreground">
                {suggestion
                  ? "建议仅供参考，你点哪个算哪个。"
                  : "可选。觉得已经有数了就直接点评级。"}
              </p>
            </div>

            {suggestionError && <ApiErrorNotice error={suggestionError} />}

            {suggestion && (
              <Notice tone="info">
                <p>
                  AI 建议「{RATING_LABELS[suggestion.suggested_rating]}」，理由：
                  {suggestion.evaluation}
                </p>
              </Notice>
            )}

            <p className="text-sm font-medium">你觉得这次回忆得怎么样？</p>
            <RatingButtons
              highlight={suggestion?.suggested_rating ?? null}
              disabled={ratingMutation.isPending || chatBusy}
              onRate={handleRate}
            />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
