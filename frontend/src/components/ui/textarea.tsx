import type * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 多行输入框。字号规则同 `Input`：窄屏 16px，避免 iOS Safari 聚焦时自动放大页面。
 *
 * `resize-y` 而不是 `resize-none`：「自己的理解」常常要写好几段，
 * 用户应该能自己把框拉大。禁止缩放会把长文本挤在一个看不见全貌的小窗里。
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-28 w-full rounded-lg border border-input bg-transparent px-3 py-2 text-base leading-relaxed shadow-xs transition-[color,box-shadow] outline-none",
        "placeholder:text-muted-foreground",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20",
        "resize-y md:text-sm",
        className,
      )}
      {...props}
    />
  )
}

export { Textarea }
