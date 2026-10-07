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
 * **Four entries, then everything else.** The user's complaint about the previous
 * arrangement was that it still read like a control panel: eight entries under
 * 工作, each one a different way of looking at the same work. A person who opens
 * this product wants to talk to it, look at what it knows about them, look at
 * their material, and change a setting — in that order. Everything else is for
 * looking into the system, and lives under 高级, collapsed.
 *
 * Nothing was deleted. Every old route still resolves and every page still exists
 * (`verify-sidebar.mjs` walks all seventeen of them); what changed is where they
 * sit and what they are called. Renaming a section is presentation. Deleting a
 * route would not be, which is why nothing below was deleted.
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    title: '对话',
    items: [
      { to: '/', label: '对话', icon: Home, hint: '说一件你想完成的事' },
      { to: '/me', label: '我的', icon: UserCircle, hint: '它记住了什么、你在意什么' },
      { to: '/knowledge', label: '资料', icon: Library, prefix: true, hint: '它可以从哪些材料里找' },
      { to: '/settings', label: '设置', icon: SlidersHorizontal },
    ],
  },
  {
    // Everything a user opens only when they want to understand or control the
    // machinery. Collapsed by default so the four entries above stay scannable.
    title: '高级',
    collapsible: true,
    items: [
      { to: '/workbench', label: '工作台', icon: Bot, prefix: true, hint: '它正在做什么' },
      { to: '/projects', label: '项目', icon: FolderKanban, prefix: true },
      { to: '/tasks', label: '任务', icon: ListChecks, prefix: true },
      { to: '/planner', label: '计划', icon: CalendarRange },
      { to: '/documents', label: '文档', icon: FileText, prefix: true },
      { to: '/notes', label: '笔记', icon: NotebookPen, prefix: true },
      { to: '/search', label: '查找', icon: Search },
      { to: '/chat', label: '问答', icon: Sparkles },
      { to: '/ai-runs', label: '执行记录', icon: Activity, prefix: true },
      { to: '/agent', label: 'Agent 控制台', icon: Bot, prefix: true },
      { to: '/settings/agent', label: '权限', icon: ShieldCheck },
      { to: '/graph', label: '知识图谱', icon: Network },
      { to: '/home', label: '四区首页（旧）', icon: Home, hint: '上一版的首页，仍在' },
    ],
  },
]

/**
 * Kept as a value so nothing that imports it breaks; not rendered in the sidebar.
 *
 * 设置 is one of the four main entries now, and listing it twice made the short
 * list look longer than it was — which is the thing this restructure was fixing.
 */
export const SETTINGS_ITEM: NavItem = { to: '/settings', label: '设置', icon: Settings }
