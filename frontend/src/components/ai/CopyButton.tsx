import { useState } from "react"
import { Check, Copy } from "lucide-react"

import { Button } from "@/components/ui/button"

/**
 * 复制一段文本到剪贴板。
 *
 * 桌面版是用 Chrome/Edge 的 --app 窗口打开的，那种窗口的右键菜单被精简掉了
 * 「复制」，光靠选中 + 右键复制不了，所以界面里自带一个复制按钮走剪贴板 API。
 * 回退用 execCommand 兜住非安全上下文或老浏览器；两条路都不通才返回 false，
 * 让按钮给出「没复制上」的诚实反馈，而不是假装成功。
 */
async function writeToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 权限或焦点问题，往下走 execCommand 回退
    }
  }
  try {
    const area = document.createElement("textarea")
    area.value = text
    area.setAttribute("readonly", "")
    area.style.position = "fixed"
    area.style.top = "-1000px"
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand("copy")
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}

const FEEDBACK_MS = 1500

export function CopyButton({ text, label = "复制" }: { text: string; label?: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle")

  const handleCopy = async () => {
    const ok = await writeToClipboard(text)
    setStatus(ok ? "copied" : "failed")
    window.setTimeout(() => setStatus("idle"), FEEDBACK_MS)
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="-ml-1.5 h-7 px-1.5 text-xs text-muted-foreground"
      onClick={handleCopy}
    >
      {status === "copied" ? (
        <Check aria-hidden="true" className="size-3.5" />
      ) : (
        <Copy aria-hidden="true" className="size-3.5" />
      )}
      {status === "copied" ? "已复制" : status === "failed" ? "没复制上" : label}
    </Button>
  )
}
