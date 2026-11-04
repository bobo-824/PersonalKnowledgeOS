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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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
        <CardDescription>到期的知识会自动排队，评完即清。</CardDescription>
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
            现在没有到期的知识。该干嘛干嘛，到期了这里会提醒。
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
    <div className="flex flex-col gap-4">
      <DueReviewCard />

      <div className="flex items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Activity aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0">
          <h1 className="font-heading text-xl font-semibold">首页</h1>
          <p className="text-sm text-muted-foreground">
            阶段 0：项目骨架已跑通，下面是真实自检结果
          </p>
        </div>
      </div>

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
          <CardDescription>
            直接请求本机 <code className="rounded bg-muted px-1">/api/health</code>，
            不是模拟数据。
          </CardDescription>
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
              <div className="flex flex-col">
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
                <Row label="迁移版本" value={data.database.schema_version ?? "未迁移"} />
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
                      "未配置（阶段 2 才需要）"
                    )
                  }
                />
                <Row
                  label="前端构建产物"
                  value={data.frontend_built ? "已构建" : "开发模式"}
                />
              </div>

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

      <Card>
        <CardHeader>
          <CardTitle>阶段 0 已交付</CardTitle>
          <CardDescription>
            完整的路线图见 <code className="rounded bg-muted px-1">docs/ROADMAP.md</code>。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-1.5 text-sm">
            {[
              "本地 SQLite 数据库与 Alembic 迁移（knowledge / knowledge_versions / sources + 中文全文索引）",
              "统一配置层：端口、数据目录、AI 密钥全部来自 .env，代码里无硬编码",
              "单端口部署：同一个服务同时提供 API 与前端页面",
              "响应式外壳：桌面侧边栏 / 移动端抽屉，触屏可完整操作",
            ].map((item) => (
              <li key={item} className="flex gap-2">
                <CircleCheck
                  aria-hidden="true"
                  className="mt-0.5 size-4 shrink-0 text-green-600"
                />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
