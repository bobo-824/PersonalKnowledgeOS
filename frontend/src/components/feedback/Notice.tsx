import { AlertCircle, AlertTriangle, CheckCircle2, Info, type LucideIcon } from "lucide-react"
import type * as React from "react"

import { ApiError } from "@/lib/api"
import { cn } from "@/lib/utils"

type Tone = "error" | "info" | "success" | "warning"

const TONES: Record<Tone, { className: string; icon: LucideIcon }> = {
  error: {
    className: "border-destructive/30 bg-destructive/5 text-destructive",
    icon: AlertCircle,
  },
  info: {
    className: "border-border bg-muted/60 text-foreground",
    icon: Info,
  },
  success: {
    className: "border-emerald-500/30 bg-emerald-500/5 text-foreground",
    icon: CheckCircle2,
  },
  warning: {
    className: "border-amber-500/30 bg-amber-500/5 text-foreground",
    icon: AlertTriangle,
  },
}

/**
 * 一条提示横幅。
 *
 * 用 `role="alert"`（错误）让读屏软件立刻播报，而不是等用户自己发现。
 * 颜色之外还有图标和文字，不靠单一颜色传达信息（色觉障碍也能看懂）。
 */
export function Notice({
  tone = "info",
  title,
  children,
  className,
}: {
  tone?: Tone
  title?: string
  children?: React.ReactNode
  className?: string
}) {
  const { className: toneClass, icon: Icon } = TONES[tone]
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm",
        toneClass,
        className,
      )}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children}
      </div>
    </div>
  )
}

/**
 * 把 `ApiError` 渲染成横幅。
 *
 * 字段级错误逐条列出——一次提交可能同时有几个字段不合格，
 * 揉成一句话用户就不知道还要改哪里。
 */
export function ApiErrorNotice({
  error,
  className,
}: {
  error: unknown
  className?: string
}) {
  if (!error) return null

  const message =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : "发生了未知错误。"
  const fieldErrors = error instanceof ApiError ? error.fieldErrors : []

  return (
    <Notice tone="error" className={className}>
      <p>{message}</p>
      {fieldErrors.length > 1 && (
        <ul className="mt-1.5 flex list-disc flex-col gap-1 pl-4 text-xs">
          {fieldErrors.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </Notice>
  )
}

/** 空状态。刻意区分"一条都没有"和"没搜到"，两者要引导用户做的事不一样。 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border px-6 py-12 text-center">
      <span className="flex size-11 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        <Icon aria-hidden="true" className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <p className="font-heading text-sm font-medium">{title}</p>
        {description && (
          <p className="mx-auto max-w-sm text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {action}
    </div>
  )
}
