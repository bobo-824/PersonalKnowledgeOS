import { createBrowserRouter } from "react-router-dom"

import { AppShell } from "@/components/layout/AppShell"
import { AiStudyRoomPage } from "@/pages/AiStudyRoomPage"
import { HomePage } from "@/pages/HomePage"
import { KnowledgeDetailPage } from "@/pages/KnowledgeDetailPage"
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
      { path: "knowledge/:knowledgeId", element: <KnowledgeDetailPage /> },
      { path: "study", element: <AiStudyRoomPage /> },
      { path: "review", element: <ReviewCenterPage /> },
      { path: "graph", element: <KnowledgeGraphPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
])
