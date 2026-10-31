import { FileText } from "lucide-react"
import { Link, useParams } from "react-router-dom"

import { StagePlaceholder } from "@/components/layout/StagePlaceholder"
import { Button } from "@/components/ui/button"

export function KnowledgeDetailPage() {
  const { knowledgeId } = useParams<{ knowledgeId: string }>()

  return (
    <StagePlaceholder
      icon={FileText}
      title="知识详情"
      stage={1}
      stageName="知识库"
      planned={[
        "当前用户确认的理解、原始理解、AI 反馈、尚存疑问、学习来源",
        "复习状态与下次复习时间",
        "历史版本列表、版本差异对比、恢复到旧版本",
        "将 AI 建议合并为新版本（用户确认后，原文仍保留）",
        "相关知识与待审核关联",
      ]}
    >
      <div className="flex flex-col gap-2 border-t border-border pt-3">
        <p className="text-xs text-muted-foreground">
          路由参数已生效，当前知识 ID：
          <code className="ml-1 rounded bg-muted px-1 break-all">
            {knowledgeId ?? "（缺失）"}
          </code>
        </p>
        <Button variant="outline" size="sm" className="self-start" asChild>
          <Link to="/knowledge">返回知识库</Link>
        </Button>
      </div>
    </StagePlaceholder>
  )
}
