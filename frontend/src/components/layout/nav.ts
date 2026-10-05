import type { ComponentType } from 'react'
import {
  ShieldCheck,
  Activity,
  Bot,
  CalendarRange,
  FileText,
  FolderKanban,
  LayoutDashboard,
  Library,
  ListChecks,
  MessagesSquare,
  Network,
  NotebookPen,
  Search,
  Settings,
} from 'lucide-react'

export interface NavItem {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
  /** Match nested routes (e.g. /knowledge/:id/documents). */
  prefix?: boolean
  hint?: string
}

export interface NavSection {
  title: string
  items: NavItem[]
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: '工作区',
    items: [
      { to: '/', label: '仪表盘', icon: LayoutDashboard },
      { to: '/projects', label: '项目', icon: FolderKanban, prefix: true },
    ],
  },
  {
    title: '知识',
    items: [
      { to: '/knowledge', label: '知识库', icon: Library, prefix: true },
      { to: '/documents', label: '文档', icon: FileText, prefix: true },
      { to: '/search', label: '检索', icon: Search },
      { to: '/chat', label: 'RAG 问答', icon: MessagesSquare },
    ],
  },
  {
    title: '执行',
    items: [
      { to: '/planner', label: '任务规划', icon: CalendarRange },
      { to: '/tasks', label: '任务', icon: ListChecks, prefix: true },
      { to: '/agent', label: 'Agent', icon: Bot },
    ],
  },
  {
    title: '记录',
    items: [
      { to: '/notes', label: '笔记', icon: NotebookPen, prefix: true },
      { to: '/ai-runs', label: 'AI 运行', icon: Activity, prefix: true },
      { to: '/settings/agent', label: 'Agent 权限', icon: ShieldCheck },
      { to: '/graph', label: '知识图谱', icon: Network },
    ],
  },
]

export const SETTINGS_ITEM: NavItem = { to: '/settings', label: '设置', icon: Settings }
