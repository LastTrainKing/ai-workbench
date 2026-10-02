import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Bot,
  CalendarRange,
  CornerDownLeft,
  FilePlus2,
  FileText,
  FolderKanban,
  LayoutDashboard,
  ListChecks,
  ListPlus,
  LogOut,
  MessagesSquare,
  Network,
  NotebookPen,
  Search,
  Settings,
  Sun,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { filterPaletteItems, groupPaletteItems, type PaletteItem } from '@/lib/palette'
import { useUiStore } from '@/store/ui'
import { useAuthStore } from '@/store/auth'
import { useToastStore } from '@/store/toast'

const ICONS: Record<string, typeof Search> = {
  chat: MessagesSquare,
  planner: CalendarRange,
  task: ListChecks,
  newtask: ListPlus,
  upload: FilePlus2,
  dashboard: LayoutDashboard,
  settings: Settings,
  graph: Network,
  runs: Activity,
  notes: NotebookPen,
  agent: Bot,
  project: FolderKanban,
  document: FileText,
  theme: Sun,
  logout: LogOut,
}

const STATIC_ITEMS: PaletteItem[] = [
  { id: 'a-chat', label: '向知识库提问', group: 'AI 操作', action: 'navigate', to: '/chat', icon: 'chat', keywords: ['rag', 'ask', '问答', '提问'] },
  { id: 'a-plan', label: '生成执行计划', group: 'AI 操作', action: 'navigate', to: '/planner', icon: 'planner', keywords: ['plan', 'planner', '计划'] },
  { id: 'a-agent', label: '运行 Agent', group: 'AI 操作', action: 'navigate', to: '/agent', icon: 'agent', keywords: ['agent', '工具', 'tool'] },
  { id: 'a-task', label: '新建任务', group: 'AI 操作', action: 'navigate', to: '/tasks?new=1', icon: 'newtask', keywords: ['task', 'create', '任务'] },
  { id: 'a-upload', label: '上传文档', group: 'AI 操作', action: 'navigate', to: '/documents?upload=1', icon: 'upload', keywords: ['upload', 'document', '上传'] },
  { id: 'n-dashboard', label: '打开仪表盘', group: '导航', action: 'navigate', to: '/', icon: 'dashboard' },
  { id: 'n-projects', label: '打开项目', group: '导航', action: 'navigate', to: '/projects', icon: 'project' },
  { id: 'n-knowledge', label: '打开知识库', group: '导航', action: 'navigate', to: '/knowledge', icon: 'document' },
  { id: 'n-documents', label: '打开文档', group: '导航', action: 'navigate', to: '/documents', icon: 'document' },
  { id: 'n-search', label: '打开检索', group: '导航', action: 'navigate', to: '/search', icon: 'document' },
  { id: 'n-notes', label: '打开笔记', group: '导航', action: 'navigate', to: '/notes', icon: 'notes' },
  { id: 'n-runs', label: '打开 AI 运行记录', group: '导航', action: 'navigate', to: '/ai-runs', icon: 'runs' },
  { id: 'n-graph', label: '打开知识图谱', group: '导航', action: 'navigate', to: '/graph', icon: 'graph' },
  { id: 'n-settings', label: '打开设置', group: '导航', action: 'navigate', to: '/settings', icon: 'settings' },
  { id: 's-theme', label: '切换明暗主题', group: '系统', action: 'toggle-theme', icon: 'theme', keywords: ['theme', 'dark', 'light', '主题'] },
  { id: 's-logout', label: '退出登录', group: '系统', action: 'logout', icon: 'logout', keywords: ['logout', 'sign out', '退出'] },
]

export function CommandPalette() {
  const navigate = useNavigate()
  const open = useUiStore((state) => state.commandPaletteOpen)
  const setOpen = useUiStore((state) => state.setCommandPalette)
  const toggleTheme = useUiStore((state) => state.toggleTheme)
  const logout = useAuthStore((state) => state.logout)
  const toast = useToastStore((state) => state.success)

  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const documents = useQuery({
    queryKey: ['palette', 'documents'],
    queryFn: ({ signal }) => api.documents.list({ page_size: 30 }, signal),
    enabled: open,
    staleTime: 30_000,
  })
  const tasks = useQuery({
    queryKey: ['palette', 'tasks'],
    queryFn: ({ signal }) => api.tasks.list({ page_size: 30 }, signal),
    enabled: open,
    staleTime: 30_000,
  })
  const projects = useQuery({
    queryKey: ['palette', 'projects'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 30 }, signal),
    enabled: open,
    staleTime: 60_000,
  })
  const notes = useQuery({
    queryKey: ['palette', 'notes'],
    queryFn: ({ signal }) => api.notes.list({ page_size: 30 }, signal),
    enabled: open,
    staleTime: 30_000,
  })

  const items = useMemo<PaletteItem[]>(() => {
    const dynamic: PaletteItem[] = [
      ...(projects.data?.items ?? []).map<PaletteItem>((project) => ({
        id: `p-${project.id}`,
        label: project.name,
        hint: '项目',
        group: '项目',
        to: `/projects/${project.id}`,
        icon: 'project',
      })),
      ...(documents.data?.items ?? []).map<PaletteItem>((doc) => ({
        id: `d-${doc.id}`,
        label: doc.title,
        hint: `${doc.status} · ${doc.chunk_count} chunks`,
        group: '文档',
        to: `/documents?doc=${doc.id}`,
        icon: 'document',
      })),
      ...(tasks.data?.items ?? []).map<PaletteItem>((task) => ({
        id: `t-${task.id}`,
        label: task.title,
        hint: `${task.status} · ${task.priority}`,
        group: '任务',
        to: `/tasks?task=${task.id}`,
        icon: 'task',
      })),
      ...(notes.data?.items ?? []).map<PaletteItem>((note) => ({
        id: `n-${note.id}`,
        label: note.title,
        hint: note.is_ai_generated ? 'AI 生成' : '笔记',
        group: '笔记',
        to: `/notes?note=${note.id}`,
        icon: 'notes',
      })),
    ]
    return [...STATIC_ITEMS, ...dynamic]
  }, [projects.data, documents.data, tasks.data, notes.data])

  const results = useMemo(() => filterPaletteItems(query, items, 40), [query, items])
  const groups = useMemo(() => groupPaletteItems(results), [results])

  useEffect(() => {
    setActiveIndex(0)
  }, [query, open])

  useEffect(() => {
    if (open) {
      setQuery('')
      setActiveIndex(0)
      window.setTimeout(() => inputRef.current?.focus(), 30)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const container = listRef.current
    const active = container?.querySelector<HTMLElement>('[data-active="true"]')
    active?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, open])

  if (!open) return null

  const execute = async (item: PaletteItem) => {
    setOpen(false)
    if (item.action === 'toggle-theme') {
      toggleTheme()
      return
    }
    if (item.action === 'logout') {
      await logout()
      toast('已退出登录')
      return
    }
    if (item.to) navigate(item.to)
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((index) => (results.length === 0 ? 0 : (index + 1) % results.length))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((index) => (results.length === 0 ? 0 : (index - 1 + results.length) % results.length))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const item = results[activeIndex]
      if (item) void execute(item)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
    }
  }

  const loading = documents.isFetching || tasks.isFetching || projects.isFetching || notes.isFetching

  return (
    <div className="fixed inset-0 z-[65] flex items-start justify-center px-4 pt-[12vh]">
      <div className="fixed inset-0 bg-overlay animate-fade-in" onClick={() => setOpen(false)} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        className="relative z-10 w-full max-w-xl overflow-hidden rounded-lg border border-subtle bg-surface shadow-pop animate-slide-up"
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-2 border-b border-subtle px-3">
          <Search className="size-4 shrink-0 text-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索文档、任务、项目、笔记，或执行操作…"
            className="h-11 flex-1 bg-transparent text-sm text-primary outline-none placeholder:text-faint"
            aria-label="命令面板输入"
            aria-controls="palette-list"
            aria-activedescendant={results[activeIndex] ? `palette-${results[activeIndex].id}` : undefined}
          />
          {loading ? <span className="text-2xs text-faint">加载中…</span> : null}
          <kbd className="aiw-kbd">Esc</kbd>
        </div>

        <div ref={listRef} id="palette-list" role="listbox" className="max-h-[52vh] overflow-y-auto py-1">
          {results.length === 0 ? (
            <p className="px-3 py-8 text-center text-xs text-faint">没有匹配的结果</p>
          ) : (
            groups.map((group) => (
              <div key={group.group} className="py-0.5">
                <p className="px-3 py-1 text-2xs font-semibold uppercase tracking-wider text-faint">
                  {group.group}
                </p>
                {group.items.map((item) => {
                  const globalIndex = results.indexOf(item)
                  const Icon = ICONS[item.icon ?? ''] ?? Search
                  const active = globalIndex === activeIndex
                  return (
                    <button
                      key={item.id}
                      id={`palette-${item.id}`}
                      role="option"
                      aria-selected={active}
                      data-active={active}
                      type="button"
                      onMouseEnter={() => setActiveIndex(globalIndex)}
                      onClick={() => void execute(item)}
                      className={cn(
                        'flex w-full items-center gap-2.5 px-3 py-2 text-left transition-colors',
                        active ? 'bg-accent-soft' : 'hover:bg-elevated',
                      )}
                    >
                      <Icon className={cn('size-3.5 shrink-0', active ? 'text-accent' : 'text-faint')} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium text-primary">{item.label}</span>
                        {item.hint ? (
                          <span className="block truncate text-2xs text-faint">{item.hint}</span>
                        ) : null}
                      </span>
                      {active ? <CornerDownLeft className="size-3 shrink-0 text-faint" /> : null}
                    </button>
                  )
                })}
              </div>
            ))
          )}
        </div>

        <div className="flex items-center justify-between border-t border-subtle px-3 py-1.5 text-2xs text-faint">
          <span className="flex items-center gap-2">
            <span className="flex items-center gap-0.5">
              <kbd className="aiw-kbd">↑</kbd>
              <kbd className="aiw-kbd">↓</kbd> 选择
            </span>
            <span className="flex items-center gap-0.5">
              <kbd className="aiw-kbd">↵</kbd> 执行
            </span>
          </span>
          <span>{results.length} 个结果</span>
        </div>
      </div>
    </div>
  )
}
