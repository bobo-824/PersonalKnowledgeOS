import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import {
  Activity,
  BrainCircuit,
  CircleCheck,
  CircleX,
  Database,
  LoaderCircle,
  RefreshCw,
  Sparkles,
} from "lucide-react"
import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDueCount } from "@/lib/review-queries"

type HealthResponse = {
  status: "ok" | "degraded"
  app_version: string
  database: {
    reachable: boolean
    schema_version: string | null
    path: string
    error: string | null
  }
  ai_configured: boolean
  frontend_built: boolean
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 text-sm">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 break-all text-right">{value}</span>
    </div>
  )
}

function DueReviewCard() {
  const { data, isPending, isError } = useDueCount()
  const count = data?.count ?? 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BrainCircuit aria-hidden="true" className="size-5 text-primary" />
          今日复习
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3">
        {isPending && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
            正在读取…
          </p>
        )}
        {isError && (
          <p className="text-sm text-muted-foreground">暂时查不到复习排期。</p>
        )}
        {data && count === 0 && (
          <p className="text-sm text-muted-foreground">
            暂无待复习内容。
          </p>
        )}
        {data && count > 0 && (
          <>
            <p className="text-2xl font-semibold tabular-nums">{count}</p>
            <Button className="min-h-11 px-4" asChild>
              <Link to="/review">
                开始复习
              </Link>
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function HomePage() {
  const { data, isPending, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["health"],
    queryFn: async (): Promise<HealthResponse> => {
      const response = await fetch("/api/health")
      if (!response.ok) {
        throw new Error(`后端返回 ${response.status}`)
      }
      return (await response.json()) as HealthResponse
    },
  })

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <div className="flex items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Activity aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0">
          <h1 className="page-title ">首页</h1>
          <p className="text-sm text-muted-foreground">
            从今天的复习开始。
          </p>
        </div>
      </div>

      <DueReviewCard />

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            运行状态
            {isPending && <Badge variant="secondary">检查中</Badge>}
            {isError && <Badge variant="destructive">后端不可达</Badge>}
            {data && (
              <Badge variant={data.status === "ok" ? "default" : "destructive"}>
                {data.status === "ok" ? "正常" : "降级"}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {isPending && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
              正在连接后端…
            </p>
          )}

          {isError && (
            <div className="flex flex-col gap-2 rounded-lg bg-destructive/10 p-3 text-sm">
              <p className="flex items-center gap-2 font-medium text-destructive">
                <CircleX aria-hidden="true" className="size-4" />
                无法连接后端
              </p>
              <p className="break-all text-muted-foreground">
                {error instanceof Error ? error.message : String(error)}
              </p>
              <p className="text-muted-foreground">
                请确认已用 <code className="rounded bg-muted px-1">start.ps1</code> 启动服务，
                或前端开发模式下后端已监听 8000 端口。
              </p>
            </div>
          )}

          {data && (
            <>
              <details className="group">
                <summary className="cursor-pointer rounded-lg py-2 text-sm text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">查看运行详情</summary>
                <div className="mt-4 flex flex-col gap-3">
                <Row
                  label="后端数据库"
                  value={
                    data.database.reachable ? (
                      <span className="flex items-center justify-end gap-1.5 text-green-600">
                        <CircleCheck aria-hidden="true" className="size-4" />
                        可读写
                      </span>
                    ) : (
                      <span className="flex items-center justify-end gap-1.5 text-destructive">
                        <CircleX aria-hidden="true" className="size-4" />
                        不可达
                      </span>
                    )
                  }
                />
                <Row
                  label="数据库文件"
                  value={
                    <span className="flex items-center justify-end gap-1.5">
                      <Database aria-hidden="true" className="size-3.5 shrink-0 opacity-60" />
                      {data.database.path}
                    </span>
                  }
                />
                <Row label="应用版本" value={data.app_version} />
                <Row
                  label="AI 模型"
                  value={
                    data.ai_configured ? (
                      <span className="flex items-center justify-end gap-1.5">
                        <Sparkles aria-hidden="true" className="size-3.5" />
                        已配置密钥
                      </span>
                    ) : (
                      <Link to="/settings" className="text-primary underline-offset-2 hover:underline">
                        未配置，去设置页填写
                      </Link>
                    )
                  }
                />
                </div>
              </details>

              {data.database.error && (
                <p className="rounded-lg bg-destructive/10 p-3 text-xs break-all text-destructive">
                  {data.database.error}
                </p>
              )}

              <Button
                variant="outline"
                size="sm"
                className="self-start"
                onClick={() => void refetch()}
                disabled={isFetching}
              >
                {isFetching ? (
                  <LoaderCircle aria-hidden="true" className="animate-spin" data-icon="inline-start" />
                ) : (
                  <RefreshCw aria-hidden="true" data-icon="inline-start" />
                )}
                重新检查
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
