import { Link, useRouteError } from "react-router-dom"
import { RotateCw } from "lucide-react"

import { Button } from "@/components/ui/button"

/**
 * 路由级错误页（D-078）。
 *
 * 之前没有 errorElement，React Router 的默认错误页是英文的
 * "Unexpected Application Error! Failed to fetch dynamically imported module…"
 * 加一屏空白，对新手完全不友好。这里换成中文说明 + 一个「重新加载」出口。
 *
 * 最常见触发是「前端刚重建、旧 index.html 引用的哈希 chunk 已不在」——
 * router.tsx 的懒加载会先自动重载一次，这条错误页只在重载也救不回
 * （或不是 chunk 问题）时兜底显示。
 */
export function RouteError() {
  const error = useRouteError()
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "object" && error && "message" in error
        ? String((error as { message: unknown }).message)
        : String(error)
  const isStaleChunk = /dynamically imported|Failed to fetch/i.test(message)

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="page-title">页面没加载上</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        {isStaleChunk
          ? "应用刚更新过，这个页面的资源换了地址，而本地还拿着旧的。重新加载一次就好。"
          : "这个页面出了点意外。可以重新加载试试，或先回首页。"}
      </p>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button className="min-h-11 px-4" onClick={() => window.location.reload()}>
          <RotateCw aria-hidden="true" />
          重新加载
        </Button>
        <Button variant="outline" className="min-h-11 px-4" asChild>
          <Link to="/">回首页</Link>
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        反复出现的话，请把 logs/server.log 发给我。
      </p>
    </div>
  )
}
