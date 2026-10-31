import type * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 表单标签。
 *
 * 用原生 `<label>` + `htmlFor` 而不是自绘：点标签就能聚焦输入框，
 * 屏幕阅读器也能正确读出关联关系。这两件事自绘组件经常漏掉。
 */
function Label({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      data-slot="label"
      className={cn(
        "flex items-center gap-1.5 text-sm leading-none font-medium select-none",
        className,
      )}
      {...props}
    />
  )
}

/** 字段旁的可选说明文字，与标签同一行靠右显示。 */
function LabelHint({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="label-hint"
      className={cn("ml-auto text-xs font-normal text-muted-foreground", className)}
      {...props}
    />
  )
}

/** 必填标记。用文字而不是纯星号，避免读屏软件只念出一个"星"。 */
function RequiredMark() {
  return (
    <span className="text-destructive" aria-hidden="true">
      *
    </span>
  )
}

export { Label, LabelHint, RequiredMark }
