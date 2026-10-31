import { Construction, type LucideIcon } from "lucide-react"
import type { ReactNode } from "react"

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

type StagePlaceholderProps = {
  icon: LucideIcon
  title: string
  stage: number
  stageName: string
  /** 这个页面将来要做什么，逐条列出。阶段 0 不放任何假数据。 */
  planned: string[]
  children?: ReactNode
}

export function StagePlaceholder({
  icon: Icon,
  title,
  stage,
  stageName,
  planned,
  children,
}: StagePlaceholderProps) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon aria-hidden="true" className="size-5" />
        </span>
        <div className="min-w-0">
          <h1 className="font-heading truncate text-xl font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">
            阶段 {stage}：{stageName}
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Construction aria-hidden="true" className="size-4 text-amber-500" />
            尚未实现
          </CardTitle>
          <CardDescription>
            这个页面属于阶段 {stage}。当前是阶段 0（项目骨架），此处不放演示数据，
            以免把未实现的功能伪装成已完成。
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div>
            <p className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              计划包含
            </p>
            <ul className="flex flex-col gap-1.5 text-sm">
              {planned.map((item) => (
                <li key={item} className="flex gap-2">
                  <span aria-hidden="true" className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
          {children}
        </CardContent>
      </Card>
    </div>
  )
}
