import { createBrowserRouter } from "react-router-dom"

import { AppShell } from "@/components/layout/AppShell"
import { AiStudyRoomPage } from "@/pages/AiStudyRoomPage"
import { HomePage } from "@/pages/HomePage"
import { KnowledgeDetailPage } from "@/pages/KnowledgeDetailPage"
import { KnowledgeEditPage } from "@/pages/KnowledgeEditPage"
import { KnowledgeGraphPage } from "@/pages/KnowledgeGraphPage"
import { KnowledgeListPage } from "@/pages/KnowledgeListPage"
import { NotFoundPage } from "@/pages/NotFoundPage"
import { ReviewCenterPage } from "@/pages/ReviewCenterPage"
import { SettingsPage } from "@/pages/SettingsPage"

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
