import { useState, type FormEvent } from "react"
import { History, Lightbulb } from "lucide-react"

import { ApiErrorNotice, Notice } from "@/components/feedback/Notice"
import {
  SourceEditor,
  sourceDraftError,
  toSourceDraft,
  toSourcePayload,
  type SourceDraft,
} from "@/components/knowledge/SourceEditor"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label, LabelHint, RequiredMark } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useDebouncedValue } from "@/lib/use-debounced-value"
import { hasSubstantiveContent } from "@/lib/format"
import {
  useCreateKnowledge,
  useDomainSuggestions,
  useSimilarTitles,
  useUpdateKnowledge,
} from "@/lib/knowledge-queries"
import {
  currentVersionOf,
  latestVersionNumberOf,
  type KnowledgeRead,
} from "@/lib/schemas"

/**
 * 会触发新版本的三个字段。
 *
 * ⚠️ 这是 `backend/domain/versioning.py` 里 `UNDERSTANDING_FIELDS` 的一份拷贝。
 * 后端才是权威：真正决定要不要建版本的是它。前端算一遍只是为了**提前告诉用户
 * 这次保存会发生什么**——省得他改完标题发现版本号涨了、或者改了理解以为没留痕。
 * 两边万一漂移，后果是这句提示不准，数据本身仍然正确。
 */
const UNDERSTANDING_FIELDS = ["user_understanding", "reasoning", "open_questions"] as const

type UnderstandingField = (typeof UNDERSTANDING_FIELDS)[number]

type UnderstandingSnapshot = Record<UnderstandingField, string>

/** 空白和 null 视为同一件事，与后端 `_normalize` 一致。 */
function normalize(value: string | null | undefined): string {
  return (value ?? "").trim()
}

function changedUnderstandingFields(
  current: UnderstandingSnapshot,
  draft: UnderstandingSnapshot,
): UnderstandingField[] {
  return UNDERSTANDING_FIELDS.filter(
    (field) => normalize(current[field]) !== normalize(draft[field]),
  )
}

const FIELD_HINTS: Record<UnderstandingField, string> = {
  user_understanding: "自己的理解",
  reasoning: "理解依据",
  open_questions: "尚存疑问",
}

type FormState = {
  title: string
  domain: string
  summary: string
  user_understanding: string
  reasoning: string
  open_questions: string
  change_reason: string
}

function emptyForm(): FormState {
  return {
    title: "",
    domain: "",
    summary: "",
    user_understanding: "",
    reasoning: "",
    open_questions: "",
    change_reason: "",
  }
}

function formFromKnowledge(knowledge: KnowledgeRead): FormState {
  const current = currentVersionOf(knowledge)
  return {
    title: knowledge.title,
    domain: knowledge.domain ?? "",
    summary: knowledge.summary ?? "",
    user_understanding: current?.user_understanding ?? "",
    reasoning: current?.reasoning ?? "",
    open_questions: current?.open_questions ?? "",
    change_reason: "",
  }
}

type KnowledgeFormProps = {
  mode: "create" | "edit"
  /** edit 模式下必有；create 模式忽略 */
  existing?: KnowledgeRead
  onSubmitted: (knowledge: KnowledgeRead) => void
  onCancel: () => void
}

export function KnowledgeForm({
  mode,
  existing,
  onSubmitted,
  onCancel,
}: KnowledgeFormProps) {
  const [form, setForm] = useState<FormState>(() =>
    mode === "edit" && existing ? formFromKnowledge(existing) : emptyForm(),
  )
  const [sources, setSources] = useState<SourceDraft[]>(() =>
    mode === "edit" && existing ? existing.sources.map(toSourceDraft) : [],
  )
  const [showErrors, setShowErrors] = useState(false)

  const isEdit = mode === "edit"
  const createMutation = useCreateKnowledge()
  const updateMutation = useUpdateKnowledge(existing?.id ?? "")
  const mutation = isEdit ? updateMutation : createMutation

  // 领域自动补全（DECISIONS D-031）：用原生 <datalist>，手机上是系统自带的候选条
  const { data: domainSuggestions = [] } = useDomainSuggestions()

  // 新建时提示"你是不是已经记过了"。防抖，否则每敲一个字就发一次请求
  const debouncedTitle = useDebouncedValue(form.title, 350)
  const { data: similarTitles = [] } = useSimilarTitles(
    isEdit ? "" : debouncedTitle,
  )

  function patch(changes: Partial<FormState>) {
    setForm((previous) => ({ ...previous, ...changes }))
  }

  // ===== 版本预告 =====
  //
  // 只有编辑模式需要：新建必然是 v1。
  const originalUnderstanding: UnderstandingSnapshot = isEdit && existing
    ? {
        user_understanding: currentVersionOf(existing)?.user_understanding ?? "",
        reasoning: currentVersionOf(existing)?.reasoning ?? "",
        open_questions: currentVersionOf(existing)?.open_questions ?? "",
      }
    : { user_understanding: "", reasoning: "", open_questions: "" }

  const changedFields = changedUnderstandingFields(originalUnderstanding, {
    user_understanding: form.user_understanding,
    reasoning: form.reasoning,
    open_questions: form.open_questions,
  })
  const willCreateVersion = changedFields.length > 0
  const nextVersionNumber = existing ? latestVersionNumberOf(existing) + 1 : 1

  // ===== 校验 =====
  const titleError = form.title.trim() ? null : "名称必填"
  const understandingError = !form.user_understanding.trim()
    ? "「自己的理解」必填"
    : !hasSubstantiveContent(form.user_understanding)
      ? "「自己的理解」需要写出实际内容，不能只填标点符号"
      : null
  const sourceErrors = sources.map(sourceDraftError).filter((item) => item !== null)
  const formErrors = [titleError, understandingError, ...sourceErrors].filter(
    (item): item is string => item !== null,
  )

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (formErrors.length > 0) {
      setShowErrors(true)
      return
    }

    const payload = {
      title: form.title.trim(),
      domain: form.domain.trim() || null,
      summary: form.summary.trim() || null,
      user_understanding: form.user_understanding.trim(),
      reasoning: form.reasoning.trim() || null,
      open_questions: form.open_questions.trim() || null,
      sources: sources.map(toSourcePayload),
    }

    if (isEdit && existing) {
      updateMutation.mutate(
        {
          ...payload,
          // 修改说明只在真的产生新版本时才有意义，否则不传
          change_reason: willCreateVersion ? form.change_reason.trim() || null : null,
        },
        { onSuccess: onSubmitted },
      )
      return
    }

    createMutation.mutate(payload, { onSuccess: onSubmitted })
  }

  const submitting = mutation.isPending

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5" noValidate>
      <ApiErrorNotice error={mutation.error} />

      {showErrors && formErrors.length > 0 && (
        <Notice tone="error" title="还有几处需要修改">
          <ul className="mt-1 flex list-disc flex-col gap-1 pl-4 text-xs">
            {formErrors.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Notice>
      )}

      {!isEdit && similarTitles.length > 0 && (
        <Notice tone="info" title="你是不是已经记过了？">
          <ul className="mt-1 flex list-disc flex-col gap-1 pl-4 text-xs">
            {similarTitles.map((title) => (
              <li key={title}>{title}</li>
            ))}
          </ul>
          <p className="mt-1.5 text-xs text-muted-foreground">
            同一条知识记两遍，复习时会重复出现。已经记过的话，去那条上补充更好。
          </p>
        </Notice>
      )}

      {/* 名称 */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="title">
          名称 <RequiredMark />
        </Label>
        <Input
          id="title"
          value={form.title}
          disabled={submitting}
          placeholder="梯度下降"
          maxLength={200}
          aria-invalid={showErrors && titleError !== null}
          onChange={(event) => patch({ title: event.target.value })}
        />
        {showErrors && titleError && (
          <p className="text-xs text-destructive" role="alert">
            {titleError}
          </p>
        )}
      </div>

      {/* 所属领域 */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="domain">
          所属领域
          <LabelHint>选填</LabelHint>
        </Label>
        <Input
          id="domain"
          list="domain-options"
          value={form.domain}
          disabled={submitting}
          placeholder="机器学习"
          maxLength={100}
          autoComplete="off"
          onChange={(event) => patch({ domain: event.target.value })}
        />
        {/* 原生 datalist：桌面端是下拉候选，手机上是输入法的候选条，都不用额外代码 */}
        <datalist id="domain-options">
          {domainSuggestions.map((suggestion) => (
            <option key={suggestion.domain} value={suggestion.domain}>
              {`已记录 ${suggestion.count} 条`}
            </option>
          ))}
        </datalist>
        <p className="text-xs text-muted-foreground">
          输入时会提示你已经用过的写法。同一个领域写成两种名字，将来就筛不到一起了。
        </p>
      </div>

      {/* 摘要 */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="summary">
          摘要
          <LabelHint>选填</LabelHint>
        </Label>
        <Input
          id="summary"
          value={form.summary}
          disabled={submitting}
          placeholder="一句话说明这条知识是什么"
          onChange={(event) => patch({ summary: event.target.value })}
        />
      </div>

      {/* 自己的理解 —— 整个表单最重要的一项 */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="user-understanding">
          自己的理解 <RequiredMark />
          <LabelHint>最重要的一项</LabelHint>
        </Label>
        <Textarea
          id="user-understanding"
          value={form.user_understanding}
          disabled={submitting}
          rows={6}
          aria-invalid={showErrors && understandingError !== null}
          placeholder="用你自己的话说清楚这个知识是什么。不要抄原文——抄一遍不等于学会。"
          onChange={(event) => patch({ user_understanding: event.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          写不出来的地方，就是还没学会的地方。这段话将来会被拿出来让你自己回想。
        </p>
        {showErrors && understandingError && (
          <p className="text-xs text-destructive" role="alert">
            {understandingError}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="reasoning">
          理解依据
          <LabelHint>选填</LabelHint>
        </Label>
        <Textarea
          id="reasoning"
          value={form.reasoning}
          disabled={submitting}
          rows={3}
          placeholder="你是怎么想到的？依据什么推出来的？"
          onChange={(event) => patch({ reasoning: event.target.value })}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="open-questions">
          尚存疑问
          <LabelHint>选填</LabelHint>
        </Label>
        <Textarea
          id="open-questions"
          value={form.open_questions}
          disabled={submitting}
          rows={3}
          placeholder="还有哪些没想通？记下来，下次接着想。"
          onChange={(event) => patch({ open_questions: event.target.value })}
        />
      </div>

      {/* 版本预告 */}
      {isEdit && (
        <Notice tone="info">
          {willCreateVersion ? (
            <>
              <p className="flex items-center gap-1.5 font-medium">
                <History aria-hidden="true" className="size-4" />
                这次保存会生成新版本 v{nextVersionNumber}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                因为你改动了：
                {changedFields.map((field) => FIELD_HINTS[field]).join("、")}
                。旧版本会原样保留，随时能翻回去看自己当初是怎么想的。
              </p>
            </>
          ) : (
            <>
              <p className="flex items-center gap-1.5 font-medium">
                <Lightbulb aria-hidden="true" className="size-4" />
                这次保存不会生成新版本
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                只改了名称、领域、摘要或来源，属于整理性工作。
                改动「自己的理解 / 理解依据 / 尚存疑问」才会留下新版本。
              </p>
            </>
          )}
        </Notice>
      )}

      {/* 修改说明：只在会产生新版本时才问 */}
      {isEdit && willCreateVersion && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="change-reason">
            修改说明
            <LabelHint>选填</LabelHint>
          </Label>
          <Input
            id="change-reason"
            value={form.change_reason}
            disabled={submitting}
            maxLength={200}
            placeholder="复习时发现之前想错了"
            onChange={(event) => patch({ change_reason: event.target.value })}
          />
          <p className="text-xs text-muted-foreground">
            不填的话会自动写成「{changedFields.map((field) => FIELD_HINTS[field]).join("、")}」。
          </p>
        </div>
      )}

      {/* 学习来源 */}
      <fieldset className="flex flex-col gap-3" disabled={submitting}>
        <legend className="mb-1 text-sm font-medium">学习来源</legend>
        <SourceEditor sources={sources} onChange={setSources} disabled={submitting} />
      </fieldset>

      <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          className="min-h-11 px-4"
          onClick={onCancel}
        >
          取消
        </Button>
        <Button type="submit" disabled={submitting} className="min-h-11 px-5">
          {submitting ? "保存中…" : isEdit ? "保存修改" : "记下来"}
        </Button>
      </div>
    </form>
  )
}
