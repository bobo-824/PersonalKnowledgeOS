import { Plus, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label, LabelHint } from "@/components/ui/label"
import { Select } from "@/components/ui/select"
import type { SourcePayload } from "@/lib/api"
import { SOURCE_KINDS, SOURCE_KIND_LABELS, type SourceKind, type SourceRead } from "@/lib/schemas"

/**
 * 表单里的来源草稿。
 *
 * 和后端返回的 `SourceRead` 不同：这里所有字段都是非空字符串，
 * 因为受控输入框的值不能是 `null`（会变成 uncontrolled，React 会报警告）。
 * 提交时再由 `toSourcePayload` 把空字符串转回 `null`。
 */
export type SourceDraft = {
  key: string
  source_type: SourceKind
  title: string
  url_or_reference: string
  note: string
}

/**
 * 生成本地 key。
 *
 * ⚠️ 刻意不用 `crypto.randomUUID()`：它只在**安全上下文**下存在
 * （https 或 localhost）。而 Blueprint 6.4 要求手机通过局域网 IP 访问
 * 电脑上的服务，那种情况下地址是 `http://192.168.x.x:8000`，不是安全上下文，
 * `crypto.randomUUID` 会是 undefined，一点"添加来源"就崩。
 *
 * key 只需要在这个列表内唯一，用来做 React 的列表复用，计数器足够。
 */
let draftSequence = 0

export function nextDraftKey(): string {
  draftSequence += 1
  return `source-${draftSequence}`
}

export function emptySourceDraft(): SourceDraft {
  return {
    key: nextDraftKey(),
    source_type: "book",
    title: "",
    url_or_reference: "",
    note: "",
  }
}

export function toSourceDraft(source: SourceRead): SourceDraft {
  return {
    key: nextDraftKey(),
    source_type: source.source_type,
    title: source.title,
    url_or_reference: source.url_or_reference ?? "",
    note: source.note ?? "",
  }
}

/** 空字符串转成 null：后端把 null 和 "" 视作同一件事，但 null 才是"没有填"。 */
export function toSourcePayload(draft: SourceDraft): SourcePayload {
  return {
    source_type: draft.source_type,
    title: draft.title.trim(),
    url_or_reference: draft.url_or_reference.trim() || null,
    note: draft.note.trim() || null,
  }
}

/**
 * 选了「其他」却没写备注 —— 和后端同一条规则（DECISIONS D-029）。
 *
 * 前端提前挡一道是为了即时反馈；后端仍会再校验一次，
 * 前端校验只是体验，不是安全边界。
 */
export function sourceDraftError(draft: SourceDraft): string | null {
  if (draft.title.trim().length === 0) return "来源名称必填"
  if (draft.source_type === "other" && draft.note.trim().length === 0) {
    return "来源类型选「其他」时，备注必填：写清楚具体是什么来源"
  }
  return null
}

type SourceEditorProps = {
  sources: SourceDraft[]
  onChange: (sources: SourceDraft[]) => void
  /** 表单整体处于只读/提交中时禁用编辑 */
  disabled?: boolean
}

export function SourceEditor({ sources, onChange, disabled = false }: SourceEditorProps) {
  function patch(key: string, changes: Partial<Omit<SourceDraft, "key">>) {
    onChange(sources.map((item) => (item.key === key ? { ...item, ...changes } : item)))
  }

  function remove(key: string) {
    onChange(sources.filter((item) => item.key !== key))
  }

  return (
    <div className="flex flex-col gap-3">
      {sources.length === 0 && (
        <p className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-sm text-muted-foreground">
          还没有添加来源。记一下"这是从哪学的"，半年后才追得回去。
        </p>
      )}

      {sources.map((draft, index) => {
        const error = sourceDraftError(draft)
        const needsNote = draft.source_type === "other"
        return (
          <div
            key={draft.key}
            className="flex flex-col gap-3 rounded-lg border border-border bg-card p-3"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium text-muted-foreground">
                来源 {index + 1}
              </p>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={disabled}
                className="min-h-9 text-destructive"
                onClick={() => remove(draft.key)}
              >
                <Trash2 aria-hidden="true" />
                删除
              </Button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${draft.key}-type`}>类型</Label>
                <Select
                  id={`${draft.key}-type`}
                  value={draft.source_type}
                  disabled={disabled}
                  onChange={(event) =>
                    patch(draft.key, { source_type: event.target.value as SourceKind })
                  }
                >
                  {SOURCE_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {SOURCE_KIND_LABELS[kind]}
                    </option>
                  ))}
                </Select>
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`${draft.key}-title`}>
                  名称 <span className="text-destructive">*</span>
                </Label>
                <Input
                  id={`${draft.key}-title`}
                  value={draft.title}
                  disabled={disabled}
                  placeholder="《深度学习》第三章"
                  aria-invalid={draft.title.trim().length === 0}
                  onChange={(event) => patch(draft.key, { title: event.target.value })}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${draft.key}-url`}>
                链接或出处
                <LabelHint>选填</LabelHint>
              </Label>
              <Input
                id={`${draft.key}-url`}
                value={draft.url_or_reference}
                disabled={disabled}
                placeholder="https://… 或「书架第二层」"
                inputMode="url"
                onChange={(event) =>
                  patch(draft.key, { url_or_reference: event.target.value })
                }
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor={`${draft.key}-note`}>
                备注
                {needsNote ? (
                  <span className="text-destructive">*</span>
                ) : (
                  <LabelHint>选填</LabelHint>
                )}
              </Label>
              <Input
                id={`${draft.key}-note`}
                value={draft.note}
                disabled={disabled}
                aria-invalid={needsNote && draft.note.trim().length === 0}
                placeholder={
                  needsNote ? "例如：某 up 主的直播、和同事的讨论" : "补充说明"
                }
                onChange={(event) => patch(draft.key, { note: event.target.value })}
              />
              {needsNote && (
                <p className="text-xs text-muted-foreground">
                  「其他」必须写清楚到底是什么来源，否则这条记录半年后就失去意义了。
                </p>
              )}
              {error && (
                <p className="text-xs text-destructive" role="alert">
                  {error}
                </p>
              )}
            </div>
          </div>
        )
      })}

      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        className="min-h-11 self-start px-4"
        onClick={() => onChange([...sources, emptySourceDraft()])}
      >
        <Plus aria-hidden="true" />
        添加来源
      </Button>
    </div>
  )
}
