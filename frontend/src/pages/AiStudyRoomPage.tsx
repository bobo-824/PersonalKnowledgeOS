import { MessagesSquare } from "lucide-react"

import { StagePlaceholder } from "@/components/layout/StagePlaceholder"

export function AiStudyRoomPage() {
  return (
    <StagePlaceholder
      icon={MessagesSquare}
      title="AI 学习室"
      stage={2}
      stageName="AI 教练"
      planned={[
        "自由对话，不要求先填结构化表单",
        "AI 识别值得沉淀的知识，必要时追问以澄清理解、依据与疑问",
        "生成知识草稿，明确区分「来自用户」与「AI 补充」",
        "用户预览、编辑、确认后才转为正式知识单元",
        "AI 审查输出经后端 Schema 校验：正确点、错误、遗漏、追问、待核实",
      ]}
    >
      <p className="border-t border-border pt-3 text-sm text-muted-foreground">
        使用前需要在「设置」里填入模型 API 地址与密钥。密钥只保存在后端环境变量中，
        不会出现在前端代码、日志或版本库里。AI 服务不可用时，手动记录知识仍然可用。
      </p>
    </StagePlaceholder>
  )
}
