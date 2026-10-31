import { Settings } from "lucide-react"

import { StagePlaceholder } from "@/components/layout/StagePlaceholder"

export function SettingsPage() {
  return (
    <StagePlaceholder
      icon={Settings}
      title="设置"
      stage={2}
      stageName="AI 教练"
      planned={[
        "模型 Provider 配置：接口地址、API Key、模型名、超时时间",
        "数据目录位置显示与备份 / 恢复（使用 SQLite VACUUM INTO，正确处理 WAL）",
        "结构化导出知识与复习记录",
        "隐私说明：向 AI 发送数据的最小必要范围，明确告知后再发送",
        "知识归档与删除，并说明删除范围",
      ]}
    >
      <p className="border-t border-border pt-3 text-sm text-muted-foreground">
        当前配置方式：复制项目根目录的{" "}
        <code className="rounded bg-muted px-1">.env.example</code> 为{" "}
        <code className="rounded bg-muted px-1">.env</code>，填入{" "}
        <code className="rounded bg-muted px-1">PKOS_AI_*</code> 各项。
        <code className="rounded bg-muted px-1">.env</code> 已被 Git 忽略，不会提交。
      </p>
    </StagePlaceholder>
  )
}
