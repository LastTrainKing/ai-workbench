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
  collapsible?: boolean
}

/**
 * True on the conversation screens — `/` and `/c/:id`.
 *
 * One predicate rather than a `pathname === '/'` in each place that asks. Both routes are
 * the same screen (one with a conversation open), so every "hide the engine room here"
 * decision has to answer for both, and a second copy of the test is how one of them ends
 * up forgotten — the right panel reappearing the moment you reopened something from the
 * list.
 */
export function isConversation(pathname: string): boolean {
  return pathname === '/' || pathname.startsWith('/c/')
}

/**
 * Everything that is not the conversation, behind one gear.
 *
 * The user's instruction for this round:
 *
 *     把现在软件里的内核全部藏起来，前端最后就是一个对话框，
 *     哪怕像微信 qq 一样都可以
 *
 * and, about the sidebar specifically:
 *
 *     左侧只有会话列表，不是 16 个菜单项。
 *     记忆 / 知识库 / Skill / 设置 → 收进一个不显眼的入口（齿轮或头像菜单）
 *
 * So the sidebar is the conversation list and nothing else, and this is what the gear
 * opens. **Nothing was deleted.** Every route below still resolves and every page still
 * exists; `verify-sidebar.mjs` walks all of them from this menu, which is why the menu is
 * a real list of links rather than a decoration.
 *
 * The wording rule is unchanged from the last round: the user should never have to read
 * "RAG", "vector", "runtime" or "tool registry". Those are the engine room.
 */
export const MENU_SECTIONS: NavSection[] = [
  {
    title: '我的',
    items: [
      { to: '/me', label: '记忆与偏好', icon: UserCircle, hint: '它记住了什么' },
      { to: '/knowledge', label: '知识库', icon: Library, prefix: true, hint: '它能从哪些材料里找' },
      { to: '/skills', label: 'Skill', icon: Sparkles, prefix: true, hint: '它学会的做法' },
    ],
  },
  {
    title: '设置',
    items: [
      { to: '/settings', label: '模型与工作区', icon: SlidersHorizontal },
      { to: '/settings/agent', label: '权限', icon: ShieldCheck },
    ],
  },
  {
    // Everything a user opens only when they want to understand or control the
    // machinery. Collapsed by default so the two sections above stay scannable.
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
      { to: '/graph', label: '知识图谱', icon: Network },
      { to: '/home', label: '四区首页（旧）', icon: Home, hint: '上一版的首页，仍在' },
    ],
  },
]
