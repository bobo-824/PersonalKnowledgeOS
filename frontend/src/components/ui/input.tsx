import type * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 单行输入框。
 *
 * 高度固定 `h-11`（44px）：这是 iOS/Android 推荐的最小可点击尺寸，
 * Blueprint 6.5 要求移动端友好，不能靠桌面端的紧凑尺寸。
 *
 * 字号在窄屏上是 `text-base`（16px），只有 `md` 以上才降到 `text-sm`。
 * 这不是审美选择：iOS Safari 在聚焦字号小于 16px 的输入框时会**自动放大整个页面**，
 * 放大后布局会错位，用户得手动缩回来。
 */
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "flex h-11 w-full min-w-0 rounded-lg border border-input bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none",
        "placeholder:text-muted-foreground",
        "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
        "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
        "aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20",
        "md:text-sm",
        className,
      )}
      {...props}
    />
  )
}

export { Input }
