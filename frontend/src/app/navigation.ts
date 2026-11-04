import {
  BrainCircuit,
  Home,
  Library,
  MessagesSquare,
  Settings,
  Share2,
  type LucideIcon,
} from "lucide-react"

export type NavItem = {
  to: string
  label: string
  description: string
  icon: LucideIcon
}

export const NAV_ITEMS: NavItem[] = [
  {
    to: "/",
    label: "首页",
    description: "今天最值得做的事",
    icon: Home,
  },
  {
    to: "/knowledge",
    label: "知识库",
    description: "管理已沉淀的知识",
    icon: Library,
  },
  {
    to: "/study",
    label: "AI 学习室",
    description: "对话中创建知识",
    icon: MessagesSquare,
  },
  {
    to: "/review",
    label: "复习中心",
    description: "主动回忆与间隔复习",
    icon: BrainCircuit,
  },
  {
    to: "/graph",
    label: "知识图谱",
    description: "已确认的知识关系",
    icon: Share2,
  },
  {
    to: "/settings",
    label: "设置",
    description: "模型、数据与备份",
    icon: Settings,
  },
]
