import { ChevronDown } from "lucide-react"
import type * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 原生 `<select>` 加样式。
 *
 * 刻意不用自绘下拉：原生 select 在手机上会调起系统选择器（iOS 的滚轮、
 * Android 的底部弹窗），单手可操作，也天然支持键盘和读屏。
 * 自绘下拉要做到同等水平得写几百行，还常常在触屏上出问题。
 *
 * `appearance-none` 只去掉系统自带的箭头，交互行为不变；箭头由我们自己画，
 * 这样亮暗色主题下都看得清。
 */
function Select({ className, children, ...props }: React.ComponentProps<"select">) {
  return (
    <div className="relative w-full">
      <select
        data-slot="select"
        className={cn(
          "h-11 w-full rounded-lg border border-input bg-transparent px-3 pr-9 text-base shadow-xs transition-[color,box-shadow] outline-none",
          "focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50",
          "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
          "aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20",
          "appearance-none md:text-sm",
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <ChevronDown
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  )
}

export { Select }
