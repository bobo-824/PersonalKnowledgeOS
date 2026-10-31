import { BrainCircuit } from "lucide-react"

import { StagePlaceholder } from "@/components/layout/StagePlaceholder"

export function ReviewCenterPage() {
  return (
    <StagePlaceholder
      icon={BrainCircuit}
      title="复习中心"
      stage={3}
      stageName="复习系统"
      planned={[
        "按到期时间列出待复习知识，支持单条复习与连续复习",
        "AI 根据知识内容与历史薄弱点提出开放式问题，优先要求独立解释",
        "回答不完整时继续追问，不提前泄露答案",
        "AI 从正确性、完整性、逻辑性、独立表达四个维度评价，并给出具体证据",
        "AI 只给评级建议，最终评级由用户确认或修改后才写入 FSRS",
        "完整保存问题、回答、评价、评级、耗时与下次复习时间",
      ]}
    >
      <p className="border-t border-border pt-3 text-sm text-muted-foreground">
        复习以主动回忆为主，不把重复阅读笔记当作复习方式。间隔算法使用经过验证的
        FSRS 实现，不自行编写。
      </p>
    </StagePlaceholder>
  )
}
