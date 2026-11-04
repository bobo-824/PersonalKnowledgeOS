import { Suspense, useEffect, useState } from "react"
import { Menu, X } from "lucide-react"
import { NavLink, Outlet, useLocation } from "react-router-dom"

import { NAV_ITEMS } from "@/app/navigation"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

const APP_NAME = "Personal Knowledge OS"
const APP_TAGLINE = "记录理解 · 检验理解 · 巩固记忆"

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-1 px-2">
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                // 触屏优先：整个条目都是点击目标，最小高度 44px，不依赖 hover 才能操作
                "flex min-h-11 items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                isActive
                  ? "bg-primary/10 font-medium text-primary"
                  : "text-muted-foreground active:bg-muted",
              )
            }
          >
            <Icon aria-hidden="true" className="size-4 shrink-0" />
            <span className="flex flex-col leading-tight">
              <span>{item.label}</span>
              <span className="text-xs opacity-70">{item.description}</span>
            </span>
          </NavLink>
        )
      })}
    </nav>
  )
}

export function AppShell() {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()

  // 路由跳转后收起移动端抽屉，否则返回时页面被遮罩挡住
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // 抽屉打开时锁住背景滚动，避免移动端页面跟着一起滑
  useEffect(() => {
    document.body.style.overflow = drawerOpen ? "hidden" : ""
    return () => {
      document.body.style.overflow = ""
    }
  }, [drawerOpen])

  // Esc 关闭抽屉：遮罩和 X 都是给鼠标和手指的，键盘用户需要一个等价出口
  useEffect(() => {
    if (!drawerOpen) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false)
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [drawerOpen])

  const current =
    NAV_ITEMS.find((item) =>
      item.to === "/"
        ? location.pathname === "/"
        : location.pathname.startsWith(item.to),
    )?.label ?? "Personal Knowledge OS"

  return (
    <div className="flex min-h-dvh bg-background text-foreground">
      {/* 桌面端固定侧边栏 */}
      <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-border bg-sidebar lg:flex">
        <header className="px-4 py-5">
          <p className="font-heading text-sm font-semibold">{APP_NAME}</p>
          <p className="mt-1 text-xs text-muted-foreground">{APP_TAGLINE}</p>
        </header>
        <Separator />
        <ScrollArea className="flex-1 py-3">
          <NavList />
        </ScrollArea>
        <footer className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
          本地优先 · 数据不出本机
        </footer>
      </aside>

      {/* 移动端顶栏 + 抽屉。图标按钮一律 size-11（44px）：
          这是 Apple HIG / WCAG 2.5.5 的最小触摸目标，Button 的 icon-sm/icon-lg
          视觉尺寸（28/36px）在手指下太小，容易点空。 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border bg-background/95 px-3 backdrop-blur lg:hidden">
          <Button
            variant="ghost"
            size="icon-lg"
            className="size-11"
            aria-label={drawerOpen ? "关闭导航" : "打开导航"}
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen((open) => !open)}
          >
            {drawerOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
          </Button>
          <span className="truncate text-sm font-medium">{current}</span>
        </header>

        {drawerOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <button
              type="button"
              aria-label="关闭导航（点击空白处）"
              className="absolute inset-0 bg-black/50"
              onClick={() => setDrawerOpen(false)}
            />
            <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-sidebar shadow-xl">
              <header className="flex items-start justify-between gap-2 px-4 py-4">
                <div>
                  <p className="font-heading text-sm font-semibold">{APP_NAME}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{APP_TAGLINE}</p>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="size-11"
                  aria-label="关闭导航"
                  onClick={() => setDrawerOpen(false)}
                >
                  <X aria-hidden="true" />
                </Button>
              </header>
              <Separator />
              <ScrollArea className="flex-1 py-3">
                <NavList onNavigate={() => setDrawerOpen(false)} />
              </ScrollArea>
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-5xl">
            {/* 页面分包后，导航到新页面时要等它的包下载完；
                fallback 给一个居中的轻提示而不是白屏。本地环境几乎看不到它。 */}
            <Suspense
              fallback={
                <p role="status" className="py-16 text-center text-sm text-muted-foreground">
                  页面加载中…
                </p>
              }
            >
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>
    </div>
  )
}
