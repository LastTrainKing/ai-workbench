import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowUpRight,
  Bot,
  CalendarRange,
  FilePlus2,
  ListPlus,
  MessagesSquare,
  PanelRightClose,
  Sparkles,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatMs, relativeTime, runStatusTone, runTypeLabel } from '@/lib/utils'
import { useUiStore } from '@/store/ui'
import { Badge, Dot } from '@/components/ui/primitives'
import { IconButton } from '@/components/ui/Button'
import { ErrorState, Skeleton } from '@/components/ui/feedback'
import { ProviderBadge } from './TopBar'

const QUICK_ACTIONS = [
  { label: '向知识库提问', to: '/chat', icon: MessagesSquare },
  { label: '生成执行计划', to: '/planner', icon: CalendarRange },
  { label: '新建任务', to: '/tasks', icon: ListPlus },
  { label: '上传文档', to: '/documents', icon: FilePlus2 },
]

function QuickActions() {
  const navigate = useNavigate()
  return (
    <div className="grid grid-cols-2 gap-1.5">
      {QUICK_ACTIONS.map((action) => {
        const Icon = action.icon
        return (
          <button
            key={action.label}
            type="button"
            onClick={() => navigate(action.to)}
            className="flex flex-col items-start gap-1.5 rounded-md border border-subtle bg-elevated px-2.5 py-2 text-left transition-colors hover:border-strong hover:bg-surface"
          >
            <Icon className="size-3.5 text-accent" />
            <span className="text-2xs font-medium text-primary">{action.label}</span>
          </button>
        )
      })}
    </div>
  )
}

function RecentRuns() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['ai-runs', 'recent'],
    queryFn: ({ signal }) => api.aiRuns.list({ page_size: 6 }, signal),
    staleTime: 20_000,
  })

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-9" />
        ))}
      </div>
    )
  }

  if (isError) {
    return <ErrorState compact error={error} onRetry={() => void refetch()} title="运行记录加载失败" />
  }

  const items = data?.items ?? []
  if (items.length === 0) {
    return <p className="text-2xs text-faint">还没有 AI 运行记录。</p>
  }

  return (
    <ul className="space-y-1">
      {items.map((run) => (
        <li key={run.run_id}>
          <Link
            to={`/ai-runs/${run.run_id}`}
            className="flex items-center gap-2 rounded-md px-1.5 py-1.5 transition-colors hover:bg-elevated"
          >
            <Dot tone={runStatusTone(run.status)} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-2xs font-medium text-primary">
                {runTypeLabel(run.run_type)}
              </span>
              <span className="block truncate font-mono text-2xs text-faint">
                {formatMs(run.latency_ms)} · {run.total_tokens} tok · {relativeTime(run.created_at)}
              </span>
            </span>
            <ArrowUpRight className="size-3 shrink-0 text-faint" />
          </Link>
        </li>
      ))}
    </ul>
  )
}

function ContextTab() {
  const { data, isLoading } = useQuery({
    queryKey: ['insights', 'dashboard'],
    queryFn: ({ signal }) => api.insights.dashboard(signal),
    staleTime: 30_000,
  })

  if (isLoading || !data) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-16" />
        <Skeleton className="h-24" />
      </div>
    )
  }

  const rows: { label: string; value: string }[] = [
    { label: '项目', value: String(data.workspace.projects) },
    { label: '知识库', value: String(data.knowledge.knowledge_bases) },
    { label: '文档', value: String(data.knowledge.documents) },
    { label: '切片', value: String(data.knowledge.chunks) },
    { label: '笔记', value: String(data.workspace.notes) },
    { label: '待办任务', value: String(data.tasks.open) },
    { label: '逾期', value: String(data.tasks.overdue) },
    { label: 'AI 运行（窗口）', value: String(data.ai.total_runs) },
  ]

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">工作区计数</p>
        <dl className="grid grid-cols-2 gap-1.5">
          {rows.map((row) => (
            <div key={row.label} className="rounded border border-subtle bg-elevated px-2 py-1.5">
              <dt className="text-2xs text-faint">{row.label}</dt>
              <dd className="text-sm font-semibold text-primary tabular-nums">{row.value}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div>
        <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">依赖状态</p>
        <div className="flex flex-wrap gap-1">
          <Badge tone={data.providers.llm.degraded ? 'warn' : 'success'}>LLM</Badge>
          <Badge tone={data.providers.embedding.degraded ? 'warn' : 'success'}>Embedding</Badge>
          <Badge tone={(data.jobs.FAILED ?? 0) > 0 ? 'danger' : 'neutral'}>
            队列 {data.jobs.QUEUED ?? 0}/{data.jobs.RUNNING ?? 0}
          </Badge>
        </div>
      </div>

      <div>
        <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">快捷键</p>
        <ul className="space-y-1 text-2xs text-muted">
          <li className="flex items-center justify-between">
            <span>命令面板</span>
            <span className="flex gap-0.5">
              <kbd className="aiw-kbd">Ctrl</kbd>
              <kbd className="aiw-kbd">K</kbd>
            </span>
          </li>
          <li className="flex items-center justify-between">
            <span>全局检索</span>
            <kbd className="aiw-kbd">/</kbd>
          </li>
          <li className="flex items-center justify-between">
            <span>快捷键帮助</span>
            <kbd className="aiw-kbd">?</kbd>
          </li>
        </ul>
      </div>
    </div>
  )
}

export function RightPanel() {
  const open = useUiStore((state) => state.rightPanelOpen)
  const tab = useUiStore((state) => state.rightPanelTab)
  const setTab = useUiStore((state) => state.setRightPanelTab)
  const toggle = useUiStore((state) => state.toggleRightPanel)

  if (!open) return null

  return (
    <aside className="hidden w-[300px] shrink-0 flex-col border-l border-subtle bg-surface lg:flex">
      <header className="flex h-12 shrink-0 items-center gap-1 border-b border-subtle px-2">
        <div className="flex flex-1 items-center gap-0.5">
          {(
            [
              { key: 'assistant', label: 'AI 助手', icon: Sparkles },
              { key: 'context', label: '上下文', icon: Bot },
            ] as const
          ).map((item) => {
            const Icon = item.icon
            const active = tab === item.key
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setTab(item.key)}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs font-medium transition-colors',
                  active ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-elevated hover:text-primary',
                )}
              >
                <Icon className="size-3.5" />
                {item.label}
              </button>
            )
          })}
        </div>
        <IconButton label="收起面板" size="xs" onClick={toggle}>
          <PanelRightClose className="size-3.5" />
        </IconButton>
      </header>

      <div className="aiw-scroll flex-1 space-y-4 p-3">
        {tab === 'assistant' ? (
          <>
            <section className="space-y-2">
              <p className="text-2xs font-semibold uppercase tracking-wider text-faint">快捷操作</p>
              <QuickActions />
            </section>
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-2xs font-semibold uppercase tracking-wider text-faint">最近 AI 运行</p>
                <Link to="/ai-runs" className="text-2xs text-accent hover:underline">
                  全部
                </Link>
              </div>
              <RecentRuns />
            </section>
            <section className="space-y-2">
              <p className="text-2xs font-semibold uppercase tracking-wider text-faint">模型状态</p>
              <ProviderBadge className="max-w-none" />
            </section>
          </>
        ) : (
          <ContextTab />
        )}
      </div>
    </aside>
  )
}
