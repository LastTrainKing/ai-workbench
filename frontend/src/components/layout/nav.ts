import type { ComponentType } from 'react'
import {
  Activity,
  Bot,
  CalendarRange,
  FileText,
  FolderKanban,
  Home,
  Library,
  ListChecks,
  Network,
  NotebookPen,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UserCircle,
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
  /**
   * Start collapsed. Used for the section that holds the machinery — see the
   * note on `NAV_SECTIONS` below.
   */
  collapsible?: boolean
}

/**
 * The sidebar, ordered by how often a person actually needs each thing.
 *
 * The rule behind the ordering and the wording: **the user should never have to
 * read the word "RAG", "vector", "runtime" or "tool registry"**. Those are the
 * engine room. What the user sees is: this is my AI, it knows me, it does things
 * for me, and I can see what it did.
 *
 * So the sections are 首页 / 我的 / 工作 / 高级, not 工作区 / 知识 / 执行 / 记录.
 * The technical surfaces are not removed — every old route still resolves and
 * every page still exists — they move into a section that starts collapsed,
 * because they are for looking into the system rather than using it.
 *
 * Renaming a section is a presentation change. Deleting a route would not be,
 * which is why nothing below was deleted.
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    title: '首页',
    items: [{ to: '/', label: '主页', icon: Home }],
  },
  {
    title: '我的',
    items: [
      { to: '/me', label: '我的 AI', icon: UserCircle, hint: '档案、记忆与偏好' },
      { to: '/notes', label: '笔记', icon: NotebookPen, prefix: true },
    ],
  },
  {
    title: '工作',
    items: [
      { to: '/workbench', label: '工作台', icon: Bot, prefix: true, hint: '它正在做什么' },
      { to: '/projects', label: '项目', icon: FolderKanban, prefix: true },
      { to: '/tasks', label: '任务', icon: ListChecks, prefix: true },
      { to: '/planner', label: '计划', icon: CalendarRange },
      { to: '/knowledge', label: '资料', icon: Library, prefix: true },
      { to: '/documents', label: '文档', icon: FileText, prefix: true },
      { to: '/search', label: '查找', icon: Search },
      { to: '/chat', label: '问答', icon: Sparkles },
    ],
  },
  {
    // Everything a user opens only when they want to understand or control the
    // machinery. Collapsed by default so the first three sections stay scannable.
    title: '高级',
    collapsible: true,
    items: [
      { to: '/ai-runs', label: '执行记录', icon: Activity, prefix: true },
      { to: '/agent', label: 'Agent 控制台', icon: Bot, prefix: true },
      { to: '/settings/agent', label: '权限', icon: ShieldCheck },
      { to: '/graph', label: '知识图谱', icon: Network },
      { to: '/settings', label: '设置', icon: SlidersHorizontal },
    ],
  },
]

export const SETTINGS_ITEM: NavItem = { to: '/settings', label: '设置', icon: Settings }
