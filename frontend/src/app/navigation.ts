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
  /** 该页面在哪个阶段落地。阶段 0 只放占位，不用假数据冒充已实现功能。 */
  stage: number
}

/** 已经落地到哪个阶段。占位页要对用户说实话，所以每完成一个阶段就得改这里。 */
export const CURRENT_STAGE = 4

export const NAV_ITEMS: NavItem[] = [
  {
    to: "/",
    label: "首页",
    description: "今天最值得做的事",
    icon: Home,
    stage: 3,
  },
  {
    to: "/knowledge",
    label: "知识库",
    description: "管理已沉淀的知识",
    icon: Library,
    stage: 1,
  },
  {
    to: "/study",
    label: "AI 学习室",
    description: "对话中创建知识",
    icon: MessagesSquare,
    stage: 2,
  },
  {
    to: "/review",
    label: "复习中心",
    description: "主动回忆与间隔复习",
    icon: BrainCircuit,
    stage: 3,
  },
  {
    to: "/graph",
    label: "知识图谱",
    description: "已确认的知识关系",
    icon: Share2,
    stage: 4,
  },
  {
    to: "/settings",
    label: "设置",
    description: "模型、数据与备份",
    icon: Settings,
    stage: 2,
  },
]
