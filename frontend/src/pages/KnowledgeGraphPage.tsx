import { Share2 } from "lucide-react"

import { StagePlaceholder } from "@/components/layout/StagePlaceholder"

export function KnowledgeGraphPage() {
  return (
    <StagePlaceholder
      icon={Share2}
      title="知识图谱"
      stage={4}
      stageName="知识网络"
      planned={[
        "新增知识后检索少量相关候选，不做全库无差别扫描",
        "AI 判断候选关系并输出关系类型、解释、依据与置信度",
        "候选关系进入待审核列表：确认、拒绝、修改类型或暂时忽略",
        "正式图谱只显示已确认（accepted）的关系",
        "被拒绝的候选保留审核记录，避免重复骚扰",
        "按领域浏览知识与相关知识",
      ]}
    >
      <p className="border-t border-border pt-3 text-sm text-muted-foreground">
        这是不可破坏的设计原则之一：AI 发现的知识关联必须经过用户审核，
        未经确认的关系不得进入正式图谱。
      </p>
    </StagePlaceholder>
  )
}
