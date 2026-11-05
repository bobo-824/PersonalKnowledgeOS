import { Suspense, useEffect, useRef, useState } from "react"
import { Menu, X, Terminal, Plus, ArrowUpRight } from "lucide-react"
import { Link, NavLink, Outlet, useLocation } from "react-router-dom"

import { NAV_ITEMS } from "@/app/navigation"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

const APP_NAME = "Personal Knowledge OS"
const APP_TAGLINE = "记录理解 · 检验理解 · 巩固记忆"
const SIDEBAR_WIDTH_KEY = "pkos.sidebar-width"
const clampWidth = (width: number) => Math.min(400, Math.max(224, width))

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex flex-col gap-2 px-4">
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
                "flex min-h-11 items-center gap-3 rounded-2xl px-4 py-3 text-sm transition-colors",
                "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                isActive
                  ? "nav-selected font-medium text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground active:bg-muted",
              )
            }
          >
            <Icon aria-hidden="true" className="size-4 shrink-0" />
            <span className="flex flex-col leading-tight">
              <span>{item.label}</span>
            </span>
          </NavLink>
        )
      })}
    </nav>
  )
}

export function AppShell() {
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_WIDTH_KEY)
      const width = saved === null ? 256 : Number(saved)
      return Number.isFinite(width) ? clampWidth(width) : 256
    } catch { return 256 }
  })
  const drag = useRef<{ x: number; width: number } | null>(null)
  const [resizing, setResizing] = useState(false)
  const location = useLocation()

  useEffect(() => {
    try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth)) } catch { /* Storage may be unavailable. */ }
  }, [sidebarWidth])

  useEffect(() => {
    if (!resizing) return
    const cursor = document.body.style.cursor
    const userSelect = document.body.style.userSelect
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
    return () => {
      document.body.style.cursor = cursor
      document.body.style.userSelect = userSelect
    }
  }, [resizing])

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
    <div className="workspace-shell flex min-h-dvh bg-background text-foreground">
      {/* 桌面端固定侧边栏 */}
      <aside id="desktop-sidebar" style={{ width: sidebarWidth }} className="workspace-sidebar sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-border bg-sidebar lg:flex">
        <header className="px-7 py-9">
          <div className="mb-4 flex items-center gap-3"><span className="brand-mark"><Terminal size={20} aria-hidden="true" /></span><span className="font-mono text-sm font-semibold tracking-widest">KNOWLEDGE / OS</span></div>
          <p className="text-xs text-muted-foreground">个人知识工作空间</p>
        </header>
        <Separator />
        <div className="px-5 pt-6 pb-3 text-xs font-medium tracking-[0.12em] text-muted-foreground">WORKSPACE</div>
        <ScrollArea className="flex-1 py-2">
          <NavList />
        </ScrollArea>
        <footer className="border-t border-border px-4 py-3 text-xs text-muted-foreground">
          <span className="font-mono text-xs tracking-widest">LOCAL WORKSPACE</span>
          <p className="mt-2">本地存储 · 自主掌控</p>
        </footer>
        <div
          role="separator"
          tabIndex={0}
          aria-label="调整侧栏宽度"
          aria-controls="desktop-sidebar"
          aria-orientation="vertical"
          aria-valuemin={224}
          aria-valuemax={400}
          aria-valuenow={sidebarWidth}
          aria-valuetext={`${sidebarWidth} 像素`}
          title="拖动调整宽度，双击恢复默认；也可使用左右方向键"
          className={cn("absolute inset-y-0 -right-1 z-40 w-2 touch-none cursor-col-resize outline-none transition-colors hover:bg-primary/25 focus-visible:bg-primary/30", resizing && "bg-primary/30")}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.preventDefault()
            event.currentTarget.focus()
            event.currentTarget.setPointerCapture(event.pointerId)
            drag.current = { x: event.clientX, width: sidebarWidth }
            setResizing(true)
          }}
          onPointerMove={(event) => {
            if (drag.current) setSidebarWidth(clampWidth(Math.round(drag.current.width + event.clientX - drag.current.x)))
          }}
          onPointerUp={(event) => {
            drag.current = null
            setResizing(false)
            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
          }}
          onPointerCancel={() => { drag.current = null; setResizing(false) }}
          onLostPointerCapture={() => { drag.current = null; setResizing(false) }}
          onDoubleClick={() => setSidebarWidth(256)}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return
            event.preventDefault()
            setSidebarWidth((width) => event.key === "Home" ? 224 : event.key === "End" ? 400 : clampWidth(width + (event.key === "ArrowRight" ? 8 : -8)))
          }}
        />
      </aside>

      {/* 移动端顶栏 + 抽屉。图标按钮一律 size-11（44px）：
          这是 Apple HIG / WCAG 2.5.5 的最小触摸目标，Button 的 icon-sm/icon-lg
          视觉尺寸（28/36px）在手指下太小，容易点空。 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="hidden h-16 items-center justify-between border-b border-border/70 px-12 lg:flex">
          <div className="flex items-center gap-3 text-xs text-muted-foreground"><span className="font-mono">workspace</span><span className="opacity-40">/</span><span className="text-foreground">{current}</span></div>
          <Link to="/knowledge/new" className="flex min-h-11 items-center gap-2 text-xs text-muted-foreground transition-colors hover:text-primary"><Plus size={14} aria-hidden="true" />记录知识<ArrowUpRight size={14} aria-hidden="true" /></Link>
        </div>
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
            <div className="workspace-sidebar absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-sidebar shadow-xl">
              <header className="flex items-start justify-between gap-2 px-4 py-4">
                <div>
                  <p className="font-heading text-base font-semibold">{APP_NAME}</p>
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

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-12 lg:py-12">
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
