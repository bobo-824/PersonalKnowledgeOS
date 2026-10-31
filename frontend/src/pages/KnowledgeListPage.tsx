import { Library } from "lucide-react"

import { StagePlaceholder } from "@/components/layout/StagePlaceholder"

export function KnowledgeListPage() {
  return (
    <StagePlaceholder
      icon={Library}
      title="知识库"
      stage={1}
      stageName="知识库"
      planned={[
        "新建知识：名称、所属领域、自己的理解、理解依据、尚存疑问、学习来源",
        "列表、搜索（中文全文 + 关键词兜底）、按领域与状态筛选",
        "归档而非物理删除，归档项默认不显示",
        "每次重要修改生成新版本，旧版本永不覆盖",
      ]}
    />
  )
}
