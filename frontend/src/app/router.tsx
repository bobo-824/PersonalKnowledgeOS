import { lazy } from "react"
import { createBrowserRouter } from "react-router-dom"

import { AppShell } from "@/components/layout/AppShell"
import { HomePage } from "@/pages/HomePage"

// 除首页外的页面全部懒加载（分包）：首屏只下载首页和外壳，
// 其余页面等导航到时才下载各自的包。首页保持静态导入，
// 让浏览器能立刻渲染首屏，不出现"打开应用先转一圈加载"。
const KnowledgeListPage = lazy(() =>
  import("@/pages/KnowledgeListPage").then((m) => ({ default: m.KnowledgeListPage })),
)
const KnowledgeDetailPage = lazy(() =>
  import("@/pages/KnowledgeDetailPage").then((m) => ({ default: m.KnowledgeDetailPage })),
)
const KnowledgeEditPage = lazy(() =>
  import("@/pages/KnowledgeEditPage").then((m) => ({ default: m.KnowledgeEditPage })),
)
const AiStudyRoomPage = lazy(() =>
  import("@/pages/AiStudyRoomPage").then((m) => ({ default: m.AiStudyRoomPage })),
)
const ReviewCenterPage = lazy(() =>
  import("@/pages/ReviewCenterPage").then((m) => ({ default: m.ReviewCenterPage })),
)
const KnowledgeGraphPage = lazy(() =>
  import("@/pages/KnowledgeGraphPage").then((m) => ({ default: m.KnowledgeGraphPage })),
)
const SettingsPage = lazy(() =>
  import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })),
)
const NotFoundPage = lazy(() =>
  import("@/pages/NotFoundPage").then((m) => ({ default: m.NotFoundPage })),
)

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "knowledge", element: <KnowledgeListPage /> },
      // `new` 和 `edit` 是静态段，react-router 的路由排名会让它们优先于
      // `:knowledgeId` 匹配，所以 `/knowledge/new` 不会被当成一个知识 id。
      // 这一点有实测验证（浏览器里直接访问 /knowledge/new）。
      { path: "knowledge/new", element: <KnowledgeEditPage /> },
      { path: "knowledge/:knowledgeId", element: <KnowledgeDetailPage /> },
      { path: "knowledge/:knowledgeId/edit", element: <KnowledgeEditPage /> },
      { path: "study", element: <AiStudyRoomPage /> },
      { path: "review", element: <ReviewCenterPage /> },
      { path: "graph", element: <KnowledgeGraphPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
])
