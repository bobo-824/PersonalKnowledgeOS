import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { RouterProvider } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { router } from "@/app/router"
import "./index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 本地单用户应用，不需要后台自动刷新；数据变更由操作方主动 invalidate
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30_000,
    },
  },
})

const container = document.getElementById("root")
if (!container) {
  throw new Error("找不到 #root 挂载点，请检查 frontend/index.html")
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
