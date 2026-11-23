import { lazy, type ComponentType } from "react"
import { createBrowserRouter } from "react-router-dom"

import { AppShell } from "@/components/layout/AppShell"
import { RouteError } from "@/components/layout/RouteError"
import { HomePage } from "@/pages/HomePage"

// 除首页外的页面全部懒加载（分包）：首屏只下载首页和外壳，
// 其余页面等导航到时才下载各自的包。首页保持静态导入，
// 让浏览器能立刻渲染首屏，不出现"打开应用先转一圈加载"。
//
// 用 lazyWithReload 而不是裸 lazy：桌面 app 窗口可能停在旧构建上，
// 而前端每次重建都会换 chunk 哈希并清空旧名（D-077/D-078）。旧壳懒加载
// 新页面就会 404、抛 "Failed to fetch dynamically imported module"。
// 这种情况自动重载一次拿到新 index.html 即可自愈，不用用户手动刷新。
const RELOAD_TS_KEY = "pkos-chunk-reload-at"
const RELOAD_COOLDOWN_MS = 15000

function lazyWithReload(get: () => Promise<{ default: ComponentType }>) {
  return lazy(async () => {
    try {
      return await get()
    } catch (error) {
      const last = Number(sessionStorage.getItem(RELOAD_TS_KEY) ?? "0")
      // 冷却：15 秒内不重复自动重载，免得真坏了时无限刷。
      if (Date.now() - last > RELOAD_COOLDOWN_MS) {
        sessionStorage.setItem(RELOAD_TS_KEY, String(Date.now()))
        window.location.reload()
        return {
          default: () => (
            <p className="py-10 text-center text-sm text-muted-foreground">
              检测到应用已更新，正在重新加载…
            </p>
          ),
        }
      }
      throw error
    }
  })
}

const KnowledgeListPage = lazyWithReload(() =>
  import("@/pages/KnowledgeListPage").then((m) => ({ default: m.KnowledgeListPage })),
)
const KnowledgeDetailPage = lazyWithReload(() =>
  import("@/pages/KnowledgeDetailPage").then((m) => ({ default: m.KnowledgeDetailPage })),
)
const KnowledgeEditPage = lazyWithReload(() =>
  import("@/pages/KnowledgeEditPage").then((m) => ({ default: m.KnowledgeEditPage })),
)
const AiStudyRoomPage = lazyWithReload(() =>
  import("@/pages/AiStudyRoomPage").then((m) => ({ default: m.AiStudyRoomPage })),
)
const ReviewCenterPage = lazyWithReload(() =>
  import("@/pages/ReviewCenterPage").then((m) => ({ default: m.ReviewCenterPage })),
)
const KnowledgeGraphPage = lazyWithReload(() =>
  import("@/pages/KnowledgeGraphPage").then((m) => ({ default: m.KnowledgeGraphPage })),
)
const SettingsPage = lazyWithReload(() =>
  import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
)
const NotFoundPage = lazyWithReload(() =>
  import("@/pages/NotFoundPage").then((m) => ({ default: m.NotFoundPage })),
)

// 挂在子路由上而不是父路由：这样出错时外壳（侧栏/导航）仍在，
// 只有内容区换成 RouteError，用户还能直接点去别的页面。
const errored = { errorElement: <RouteError /> }

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "knowledge", element: <KnowledgeListPage />, ...errored },
      // `new` 和 `edit` 是静态段，react-router 的路由排名会让它们优先于
      // `:knowledgeId` 匹配，所以 `/knowledge/new` 不会被当成一个知识 id。
      // 这一点有实测验证（浏览器里直接访问 /knowledge/new）。
      { path: "knowledge/new", element: <KnowledgeEditPage />, ...errored },
      { path: "knowledge/:knowledgeId", element: <KnowledgeDetailPage />, ...errored },
      { path: "knowledge/:knowledgeId/edit", element: <KnowledgeEditPage />, ...errored },
      { path: "study", element: <AiStudyRoomPage />, ...errored },
      { path: "review", element: <ReviewCenterPage />, ...errored },
      { path: "graph", element: <KnowledgeGraphPage />, ...errored },
      { path: "settings", element: <SettingsPage />, ...errored },
      { path: "*", element: <NotFoundPage />, ...errored },
    ],
  },
])
