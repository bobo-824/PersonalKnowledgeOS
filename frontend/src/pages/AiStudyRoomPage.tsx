import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { BookPlus, Send, Trash2 } from "lucide-react"

import { ApiErrorNotice, EmptyState, Notice } from "@/components/feedback/Notice"
import { DraftConfirmPanel } from "@/components/ai/DraftConfirmPanel"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  extractDraft,
  streamChatMessage,
  AiStreamError,
} from "@/lib/ai-api"
import { AI_ERROR_KIND_HINTS, type KnowledgeDraft } from "@/lib/ai-schemas"
import {
  aiKeys,
  useAiConfig,
  useClearConversationHistory,
  useConversation,
  useConversations,
  useCreateConversation,
} from "@/lib/ai-queries"
import { knowledgeKeys } from "@/lib/knowledge-queries"
import { useQueryClient } from "@tanstack/react-query"

/**
 * AI 学习室（工作流 A + B 的界面）。
 *
 * 流式回复用本地 state 渲染而不进 React Query 缓存：流是持续产出事件的过程，
 * 等流结束（done）再把整个会话 refetch 一次，让服务端落库的完整消息
 * （含序号、耗时、token 数）接管界面。
 */

const TEXT_MAX = 8000

export function AiStudyRoomPage() {
  const queryClient = useQueryClient()
  const { data: conversations } = useConversations()
  const { data: config } = useAiConfig()
  const createConversation = useCreateConversation()
  const clearHistory = useClearConversationHistory()

  const [activeId, setActiveId] = useState<string | null>(null)
  const { data: conversation, error: conversationError } = useConversation(activeId ?? undefined)

  const [input, setInput] = useState("")
  // pendingUserText：自己刚发的那条消息，服务端落库前先就地显示
  const [pendingUserText, setPendingUserText] = useState<string | null>(null)
  const [streamingText, setStreamingText] = useState<string | null>(null)
  const [streamError, setStreamError] = useState<{ message: string; kind: string | null } | null>(
    null,
  )
  const [draft, setDraft] = useState<KnowledgeDraft | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [extractError, setExtractError] = useState<Error | null>(null)
  const [draftCostNote, setDraftCostNote] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)

  const busy = pendingUserText !== null || streamingText !== null
  const messages = conversation?.messages ?? []

  // 新消息到达或流式文字增长时滚到底部，让正在生成的回复始终可见
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, streamingText, pendingUserText])

  const handleSend = () => {
    const text = input.trim()
    if (!text || busy) return

    const send = async (conversationId: string) => {
      setPendingUserText(text)
      setStreamError(null)
      setStreamingText("")
      setDraftCostNote(null)
      let received = ""
      try {
        await streamChatMessage(conversationId, text, {
          onChunk: (chunk) => {
            received += chunk
            setStreamingText(received)
          },
        })
      } catch (error) {
        // AI 失败时用户消息和失败占位都已在服务端落库，refetch 一样能拿到
        setStreamError(
          error instanceof AiStreamError
            ? { message: error.message, kind: error.errorKind }
            : { message: error instanceof Error ? error.message : "发送失败。", kind: null },
        )
      } finally {
        setPendingUserText(null)
        setStreamingText(null)
        setInput("")
        await queryClient.invalidateQueries({ queryKey: aiKeys.conversation(conversationId) })
        await queryClient.invalidateQueries({ queryKey: aiKeys.conversations() })
      }
    }

    if (activeId) {
      void send(activeId)
      return
    }
    // 第一个对话：先建会话（第一句话用作标题，不让 AI 起名——省一次付费调用），
    // 再把同一句话作为消息发出去。
    createConversation.mutate(text, {
      onSuccess: (created) => {
        setActiveId(created.id)
        void send(created.id)
      },
      onError: () => {
        // 会话没建成，输入框里的内容还给用户，别让话白打
      },
    })
  }

  const handleExtract = () => {
    if (!activeId || busy) return
    // D-042：付费操作，点击前必须说明会调用一次 AI
    const confirmed = window.confirm(
      "将把这段对话发给 AI，请它整理出一份知识草稿。\n\n这会调用一次 AI（花一次请求的钱）。继续吗？",
    )
    if (!confirmed) return
    setExtracting(true)
    setExtractError(null)
    extractDraft(activeId)
      .then((result) => {
        setDraft(result)
        if (!result.user_understanding.trim()) {
          setDraftCostNote(
            "AI 认为这段对话里还没聊出可以沉淀的理解，可以继续聊聊再试。",
          )
        }
      })
      .catch(setExtractError)
      .finally(() => setExtracting(false))
  }

  const handleClearHistory = () => {
    const count = conversations?.length ?? 0
    if (count === 0) return
    const confirmed = window.confirm(
      `清空全部 ${count} 个对话？\n\n对话记录不可恢复。已经保存的知识、版本和审查记录不受影响。`,
    )
    if (!confirmed) return
    clearHistory.mutate(undefined, {
      onSuccess: () => setActiveId(null),
    })
  }

  if (config && !config.configured) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="font-heading text-xl font-semibold">AI 学习室</h1>
        <EmptyState
          icon={BookPlus}
          title="还没有配置 AI 模型"
          description="到设置页填入 API 地址和模型名就能开始。不配置也不影响手动记录知识。"
          action={
            <Button className="min-h-11 px-4" asChild>
              <Link to="/settings">去设置页配置</Link>
            </Button>
          }
        />
      </div>
    )
  }

  const activeTitle =
    conversations?.find((item) => item.id === activeId)?.title ?? "新对话"

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="font-heading text-xl font-semibold">AI 学习室</h1>
        <p className="text-sm text-muted-foreground">
          随便聊你在学的东西。AI 会先听你的理解，聊出内容后可以一键沉淀成知识草稿——
          草稿要你确认才会保存。
        </p>
      </header>

      <ApiErrorNotice error={conversationError} />
      <ApiErrorNotice error={clearHistory.error} />
      <ApiErrorNotice error={createConversation.error} />

      {/* 会话切换：移动端用原生 select（好按、好滚动），桌面端同款够用 */}
      <div className="flex flex-wrap items-center gap-2">
        <label className="sr-only" htmlFor="conversation-picker">
          选择历史对话
        </label>
        <select
          id="conversation-picker"
          value={activeId ?? ""}
          onChange={(event) => {
            setActiveId(event.target.value || null)
            setDraft(null)
            setStreamError(null)
          }}
          className="h-11 min-w-0 flex-1 rounded-lg border border-input bg-transparent px-3 text-base md:text-sm"
        >
          <option value="">＋ 新对话</option>
          {conversations?.map((item) => (
            <option key={item.id} value={item.id}>
              {item.title}
            </option>
          ))}
        </select>
        {conversations && conversations.length > 0 && (
          <Button
            variant="outline"
            size="icon-lg"
            className="size-11 shrink-0"
            aria-label="清空全部对话历史"
            disabled={clearHistory.isPending}
            onClick={handleClearHistory}
          >
            <Trash2 aria-hidden="true" />
          </Button>
        )}
      </div>

      {/* 消息区 */}
      <div
        ref={scrollRef}
        className="flex max-h-[55vh] min-h-64 flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-card p-4"
      >
        {messages.length === 0 && !busy && (
          <div className="my-auto text-center text-sm text-muted-foreground">
            <p>还没有对话。在下面输入框里说说你正在学什么、卡在哪里。</p>
            <p className="mt-1 text-xs">AI 被要求先听你讲，不直接灌输——所以放心用自己的话说。</p>
          </div>
        )}
        {messages.map((message) =>
          message.role === "user" ? (
            <div key={message.id} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary/10 px-3.5 py-2.5">
                <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                  {message.content}
                </p>
              </div>
            </div>
          ) : (
            <div key={message.id} className="flex justify-start">
              <div
                className={
                  message.error_kind
                    ? "max-w-[85%] rounded-2xl rounded-bl-sm border border-destructive/40 bg-destructive/5 px-3.5 py-2.5"
                    : "max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-3.5 py-2.5"
                }
              >
                {message.error_kind && (
                  <Badge variant="outline" className="mb-1.5">
                    没能回复
                  </Badge>
                )}
                <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                  {message.content}
                </p>
              </div>
            </div>
          ),
        )}

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
            <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-3.5 py-2.5">
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
        </Notice>
      )}

      {/* 输入区 */}
      <div className="flex items-end gap-2">
        <Textarea
          aria-label="输入消息"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault()
              handleSend()
            }
          }}
          placeholder={
            activeId ? `接着「${activeTitle}」聊…` : "说说你正在学什么、卡在哪里…"
          }
          maxLength={TEXT_MAX}
          className="min-h-11 flex-1"
        />
        <Button
          className="min-h-11 px-4"
          disabled={busy || input.trim().length === 0 || createConversation.isPending}
          onClick={handleSend}
        >
          <Send aria-hidden="true" />
          发送
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Enter 发送，Shift+Enter 换行 · 只发送当前对话的内容，其他知识不会外发
      </p>

      {/* 提取入口（D-042：付费操作要提前说明） */}
      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
        <Button
          variant="outline"
          className="min-h-11 px-4"
          disabled={!activeId || busy || extracting || messages.length === 0}
          onClick={handleExtract}
        >
          <BookPlus aria-hidden="true" />
          {extracting ? "正在整理草稿…" : "把这段对话沉淀成知识"}
        </Button>
        <p className="text-xs text-muted-foreground">
          {activeId
            ? "会调用一次 AI，生成草稿后由你确认才保存。"
            : "先有一个对话才能提取。"}
        </p>
      </div>

      {extractError && <ApiErrorNotice error={extractError} />}
      {draftCostNote && <Notice tone="info">{draftCostNote}</Notice>}
      {draft && (
        <DraftConfirmPanel
          draft={draft}
          onDiscard={() => {
            setDraft(null)
            // 草稿已丢弃，幂等键随面板销毁；下次提取是全新草稿
            void queryClient.invalidateQueries({ queryKey: knowledgeKeys.all })
          }}
        />
      )}
    </div>
  )
}
