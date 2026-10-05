import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BrainCircuit,
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  Clock,
  Database,
  FileText,
  FolderKanban,
  Layers,
  NotebookPen,
  RefreshCw,
  Sparkles,
  TriangleAlert,
  Zap,
} from 'lucide-react'
import { api } from '@/lib/api'
import {
  compactNumber,
  formatBytes,
  formatMs,
  formatNumber,
  isOverdue,
  relativeTime,
  runStatusTone,
  runTypeLabel,
  taskStatusTone,
} from '@/lib/utils'
import { Badge, Card, CardBody, CardHeader, Dot, PageContainer, SectionHeader, StatTile } from '@/components/ui/primitives'
import { Button, IconButton } from '@/components/ui/Button'
import { EmptyState, ErrorState, ProgressBar, Skeleton } from '@/components/ui/feedback'
import { PriorityBadge, TaskStatusBadge } from '@/components/badges'
import type { DashboardStats } from '@/lib/types'
import { LaunchGrid } from '@/features/shell/LaunchGrid'

function KpiRow({ data }: { data: DashboardStats }) {
  const tiles = [
    {
      label: '今日到期',
      value: data.tasks.due_today.length,
      sub: `待办 ${data.tasks.open} 项`,
      icon: <CalendarClock className="size-3" />,
      tone: 'info' as const,
    },
    {
      label: '逾期任务',
      value: data.tasks.overdue,
      sub: data.tasks.overdue > 0 ? '需要尽快处理' : '暂无逾期',
      icon: <CircleAlert className="size-3" />,
      tone: data.tasks.overdue > 0 ? ('danger' as const) : ('neutral' as const),
    },
    {
      label: '本周完成',
      value: data.tasks.completed_this_week,
      sub: `累计完成 ${data.tasks.done}/${data.tasks.total}`,
      icon: <CheckCircle2 className="size-3" />,
      tone: 'success' as const,
    },
    {
      label: `AI 运行（${data.ai.window_days} 天）`,
      value: data.ai.total_runs,
      sub: `今日 ${data.ai.runs_today} 次 · 失败率 ${(data.ai.failure_rate * 100).toFixed(1)}%`,
      icon: <Zap className="size-3" />,
      tone: data.ai.failure_rate > 0.1 ? ('warn' as const) : ('accent' as const),
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
      {tiles.map((tile) => (
        <StatTile
          key={tile.label}
          label={tile.label}
          value={formatNumber(tile.value)}
          sub={tile.sub}
          icon={tile.icon}
          tone={tile.tone}
        />
      ))}
    </div>
  )
}

function KnowledgeCard({ data }: { data: DashboardStats }) {
  const k = data.knowledge
  const embeddedRatio = k.chunks > 0 ? 1 : 0
  const rows = [
    { label: '知识库', value: formatNumber(k.knowledge_bases), icon: <Layers className="size-3" /> },
    { label: '文档', value: formatNumber(k.documents), icon: <FileText className="size-3" /> },
    { label: '切片', value: formatNumber(k.chunks), icon: <Database className="size-3" /> },
    { label: '概念', value: formatNumber(k.concepts), icon: <BrainCircuit className="size-3" /> },
  ]

  const byStatus = Object.entries(k.documents_by_status ?? {})

  return (
    <Card>
      <CardHeader
        title="知识库概览"
        actions={
          <Link to="/knowledge" className="text-2xs text-accent hover:underline">
            管理
          </Link>
        }
      />
      <CardBody className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          {rows.map((row) => (
            <div key={row.label} className="rounded border border-subtle bg-elevated px-2.5 py-2">
              <div className="flex items-center gap-1 text-2xs text-faint">
                {row.icon}
                {row.label}
              </div>
              <p className="mt-0.5 text-base font-semibold text-primary tabular-nums">{row.value}</p>
            </div>
          ))}
        </div>

        {byStatus.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {byStatus.map(([status, count]) => (
              <Badge
                key={status}
                tone={
                  status === 'COMPLETED'
                    ? 'success'
                    : status === 'FAILED'
                      ? 'danger'
                      : status === 'PROCESSING'
                        ? 'info'
                        : 'warn'
                }
              >
                {status} {count}
              </Badge>
            ))}
          </div>
        ) : null}

        <div>
          <div className="mb-1 flex items-center justify-between text-2xs text-muted">
            <span>存储占用</span>
            <span className="tabular-nums">{formatBytes(k.storage_bytes)}</span>
          </div>
          <ProgressBar value={embeddedRatio * 100} tone="accent" />
          <p className="mt-1 text-2xs text-faint">
            {formatNumber(k.storage_files)} 个文件 · {formatNumber(k.chunk_chars)} 字符 ·{' '}
            {compactNumber(k.chunk_tokens)} tokens
          </p>
        </div>
      </CardBody>
    </Card>
  )
}

function AiCard({ data }: { data: DashboardStats }) {
  const a = data.ai
  const byType = Object.entries(a.by_type ?? {}).sort((x, y) => y[1] - x[1]).slice(0, 5)

  return (
    <Card>
      <CardHeader
        title={`AI 指标 · 近 ${a.window_days} 天`}
        actions={
          <Link to="/ai-runs" className="text-2xs text-accent hover:underline">
            运行记录
          </Link>
        }
      />
      <CardBody className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded border border-subtle bg-elevated px-2.5 py-2">
            <p className="text-2xs text-faint">平均延迟</p>
            <p className="mt-0.5 text-base font-semibold text-primary tabular-nums">
              {formatMs(a.avg_latency_ms)}
            </p>
          </div>
          <div className="rounded border border-subtle bg-elevated px-2.5 py-2">
            <p className="text-2xs text-faint">失败率</p>
            <p
              className={`mt-0.5 text-base font-semibold tabular-nums ${
                a.failure_rate > 0.1 ? 'text-danger' : 'text-primary'
              }`}
            >
              {(a.failure_rate * 100).toFixed(1)}%
            </p>
          </div>
          <div className="rounded border border-subtle bg-elevated px-2.5 py-2">
            <p className="text-2xs text-faint">累计 Tokens</p>
            <p className="mt-0.5 text-base font-semibold text-primary tabular-nums">
              {compactNumber(a.total_tokens)}
            </p>
          </div>
          <div className="rounded border border-subtle bg-elevated px-2.5 py-2">
            <p className="text-2xs text-faint">估算成本</p>
            <p className="mt-0.5 text-base font-semibold text-primary tabular-nums">
              ${a.estimated_cost_usd.toFixed(4)}
            </p>
          </div>
        </div>

        {byType.length > 0 ? (
          <div className="space-y-1.5">
            {byType.map(([type, count]) => {
              const max = byType[0][1] || 1
              return (
                <div key={type} className="flex items-center gap-2">
                  <span className="w-24 shrink-0 truncate text-2xs text-muted">{runTypeLabel(type)}</span>
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-elevated">
                    <div className="h-full rounded-full bg-accent" style={{ width: `${(count / max) * 100}%` }} />
                  </div>
                  <span className="w-6 shrink-0 text-right text-2xs text-faint tabular-nums">{count}</span>
                </div>
              )
            })}
          </div>
        ) : null}
      </CardBody>
    </Card>
  )
}

function ProvidersCard({ data }: { data: DashboardStats }) {
  const { llm, embedding } = data.providers
  const items = [
    { name: 'LLM', info: llm },
    { name: 'Embedding', info: embedding },
  ]
  return (
    <Card>
      <CardHeader title="模型与依赖" />
      <CardBody className="space-y-2.5">
        {items.map((item) => (
          <div key={item.name} className="rounded border border-subtle bg-elevated px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-2xs font-medium text-primary">{item.name}</span>
              <Badge tone={item.info.degraded ? 'warn' : 'success'}>
                {item.info.degraded ? '离线降级' : '已连接'}
              </Badge>
            </div>
            <p className="mt-1 font-mono text-2xs text-faint">
              {item.info.kind} · {item.info.model}
            </p>
            {item.info.degraded && item.info.reason ? (
              <p className="mt-1 text-2xs text-warn">{item.info.reason}</p>
            ) : null}
          </div>
        ))}

        <div className="flex flex-wrap gap-1">
          {Object.entries(data.jobs ?? {}).map(([state, count]) => (
            <Badge
              key={state}
              tone={
                state === 'FAILED'
                  ? 'danger'
                  : state === 'RUNNING'
                    ? 'info'
                    : state === 'SUCCEEDED'
                      ? 'success'
                      : 'neutral'
              }
            >
              {state} {count}
            </Badge>
          ))}
        </div>
      </CardBody>
    </Card>
  )
}

function HighPriorityCard({ data }: { data: DashboardStats }) {
  const tasks = data.tasks.high_priority
  return (
    <Card>
      <CardHeader
        title="高优先级任务"
        description={`待办 ${data.tasks.open} 项 · 逾期 ${data.tasks.overdue} 项`}
        actions={
          <Link to="/tasks" className="text-2xs text-accent hover:underline">
            全部任务
          </Link>
        }
      />
      <CardBody className="p-0">
        {tasks.length === 0 ? (
          <EmptyState
            compact
            icon={<CheckCircle2 className="size-4" />}
            title="没有高优先级任务"
            description="任务规划器或手动创建的任务会出现在这里。"
            action={
              <Link to="/planner">
                <Button size="xs" variant="secondary">
                  生成计划
                </Button>
              </Link>
            }
            className="border-0"
          />
        ) : (
          <ul className="divide-y divide-subtle">
            {tasks.slice(0, 7).map((task) => (
              <li key={task.id}>
                <Link
                  to={`/tasks?task=${task.id}`}
                  className="flex items-center gap-2.5 px-3.5 py-2 transition-colors hover:bg-elevated"
                >
                  <Dot tone={taskStatusTone(task.status)} />
                  <span className="min-w-0 flex-1 truncate text-xs text-primary">{task.title}</span>
                  {task.due_date ? (
                    <span
                      className={`shrink-0 text-2xs tabular-nums ${
                        isOverdue(task.due_date) ? 'text-danger' : 'text-faint'
                      }`}
                    >
                      {new Date(task.due_date).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}
                    </span>
                  ) : null}
                  <PriorityBadge priority={task.priority} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  )
}

function DueTodayCard({ data }: { data: DashboardStats }) {
  const tasks = data.tasks.due_today
  return (
    <Card>
      <CardHeader title="今日到期" description={`共 ${tasks.length} 项`} />
      <CardBody className="p-0">
        {tasks.length === 0 ? (
          <p className="px-3.5 py-4 text-2xs text-faint">今天没有到期任务。</p>
        ) : (
          <ul className="divide-y divide-subtle">
            {tasks.slice(0, 6).map((task) => (
              <li key={task.id} className="flex items-center gap-2.5 px-3.5 py-2">
                <Dot tone={taskStatusTone(task.status)} />
                <Link to={`/tasks?task=${task.id}`} className="min-w-0 flex-1 truncate text-xs text-primary hover:underline">
                  {task.title}
                </Link>
                <TaskStatusBadge status={task.status} />
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  )
}

function RecentDocumentsCard({ data }: { data: DashboardStats }) {
  const docs = data.recent.documents
  return (
    <Card>
      <CardHeader
        title="最近文档"
        actions={
          <Link to="/documents" className="text-2xs text-accent hover:underline">
            全部
          </Link>
        }
      />
      <CardBody className="p-0">
        {docs.length === 0 ? (
          <EmptyState
            compact
            icon={<FileText className="size-4" />}
            title="还没有文档"
            description="上传 Markdown、TXT 或 PDF 开始构建知识库。"
            action={
              <Link to="/documents?upload=1">
                <Button size="xs" variant="secondary">
                  上传文档
                </Button>
              </Link>
            }
            className="border-0"
          />
        ) : (
          <ul className="divide-y divide-subtle">
            {docs.slice(0, 6).map((doc) => (
              <li key={doc.id}>
                <Link
                  to={`/documents?doc=${doc.id}`}
                  className="flex items-center gap-2.5 px-3.5 py-2 transition-colors hover:bg-elevated"
                >
                  <FileText className="size-3.5 shrink-0 text-faint" />
                  <span className="min-w-0 flex-1 truncate text-xs text-primary">{doc.title}</span>
                  <span className="shrink-0 text-2xs text-faint tabular-nums">{doc.chunk_count} 切片</span>
                  <Badge
                    tone={
                      doc.status === 'COMPLETED' ? 'success' : doc.status === 'FAILED' ? 'danger' : 'info'
                    }
                  >
                    {doc.status}
                  </Badge>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  )
}

function RecentRunsCard({ data }: { data: DashboardStats }) {
  const runs = data.recent.ai_runs
  return (
    <Card>
      <CardHeader
        title="最近 AI 运行"
        actions={
          <Link to="/ai-runs" className="text-2xs text-accent hover:underline">
            全部
          </Link>
        }
      />
      <CardBody className="p-0">
        {runs.length === 0 ? (
          <p className="px-3.5 py-4 text-2xs text-faint">还没有运行记录。</p>
        ) : (
          <ul className="divide-y divide-subtle">
            {runs.slice(0, 6).map((run) => (
              <li key={run.run_id}>
                <Link
                  to={`/ai-runs/${run.run_id}`}
                  className="flex items-center gap-2.5 px-3.5 py-2 transition-colors hover:bg-elevated"
                >
                  <Dot tone={runStatusTone(run.status)} />
                  <span className="min-w-0 flex-1 truncate text-xs text-primary">
                    {runTypeLabel(run.run_type)}
                  </span>
                  <span className="hidden shrink-0 font-mono text-2xs text-faint sm:inline">
                    v{run.prompt_version}
                  </span>
                  <span className="shrink-0 text-2xs text-faint tabular-nums">{formatMs(run.latency_ms)}</span>
                  <ArrowUpRight className="size-3 shrink-0 text-faint" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  )
}

function NotesCard({ data }: { data: DashboardStats }) {
  const notes = data.recent.notes
  return (
    <Card>
      <CardHeader
        title="最近笔记"
        actions={
          <Link to="/notes" className="text-2xs text-accent hover:underline">
            全部
          </Link>
        }
      />
      <CardBody className="p-0">
        {notes.length === 0 ? (
          <p className="px-3.5 py-4 text-2xs text-faint">还没有笔记。</p>
        ) : (
          <ul className="divide-y divide-subtle">
            {notes.slice(0, 5).map((note) => (
              <li key={note.id}>
                <Link
                  to={`/notes?note=${note.id}`}
                  className="block px-3.5 py-2 transition-colors hover:bg-elevated"
                >
                  <div className="flex items-center gap-2">
                    <NotebookPen className="size-3.5 shrink-0 text-faint" />
                    <span className="min-w-0 flex-1 truncate text-xs text-primary">{note.title}</span>
                    {note.is_ai_generated ? <Badge tone="accent">AI</Badge> : null}
                  </div>
                  {note.excerpt ? (
                    <p className="mt-0.5 truncate pl-5 text-2xs text-faint">{note.excerpt}</p>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  )
}

function ActivityCard({ data }: { data: DashboardStats }) {
  const activity = data.recent.activity
  return (
    <Card>
      <CardHeader title="活动流" />
      <CardBody className="p-0">
        {activity.length === 0 ? (
          <p className="px-3.5 py-4 text-2xs text-faint">暂无活动。</p>
        ) : (
          <ol className="relative px-3.5 py-3">
            {activity.slice(0, 12).map((item, index) => (
              <li key={item.id} className="relative flex gap-2.5 pb-3 last:pb-0">
                <span className="relative flex w-3 shrink-0 justify-center">
                  <span className="mt-1.5 size-1.5 rounded-full bg-accent" />
                  {index < activity.length - 1 ? (
                    <span className="absolute top-3 h-full w-px bg-subtle" aria-hidden />
                  ) : null}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-2xs text-primary">{item.title}</p>
                  <p className="font-mono text-2xs text-faint">
                    {item.event_type} · {relativeTime(item.created_at)}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardBody>
    </Card>
  )
}

export default function DashboardPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['insights', 'dashboard'],
    queryFn: ({ signal }) => api.insights.dashboard(signal),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="仪表盘"
        description={
          data
            ? `数据生成于 ${new Date(data.generated_at).toLocaleString('zh-CN')}${
                data.cached ? ' · 来自缓存' : ''
              }`
            : '工作区实时概览'
        }
        actions={
          <>
            <Link to="/planner">
              <Button size="sm" variant="secondary" icon={<Sparkles className="size-3.5" />}>
                生成计划
              </Button>
            </Link>
            <IconButton label="刷新" size="sm" onClick={() => void refetch()} disabled={isFetching}>
              <RefreshCw className={isFetching ? 'size-3.5 animate-spin' : 'size-3.5'} />
            </IconButton>
          </>
        }
      />

      {/* The launch grid is the first thing on the page: the point of the
          product is "tell it what you want", not "read a dashboard". */}
      <LaunchGrid />

      {isLoading ? (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-[74px]" />
            ))}
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Skeleton className="h-64 lg:col-span-2" />
            <Skeleton className="h-64" />
          </div>
        </div>
      ) : isError ? (
        <ErrorState title="仪表盘加载失败" error={error} onRetry={() => void refetch()} />
      ) : data ? (
        <>
          {data.providers.llm.degraded ? (
            <div className="flex items-start gap-2.5 rounded-md border border-warn/40 bg-warn-soft px-3 py-2.5">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" />
              <div className="min-w-0 text-xs">
                <p className="font-medium text-primary">当前使用离线启发式引擎（未配置云端 Key）</p>
                <p className="mt-0.5 text-muted">
                  {data.providers.llm.reason} 回答由确定性的抽取式算法生成，引用仍然来自真实检索结果。
                </p>
              </div>
            </div>
          ) : null}

          <KpiRow data={data} />

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="space-y-4 lg:col-span-2">
              <HighPriorityCard data={data} />
              <div className="grid gap-4 sm:grid-cols-2">
                <DueTodayCard data={data} />
                <RecentDocumentsCard data={data} />
              </div>
              <RecentRunsCard data={data} />
            </div>

            <div className="space-y-4">
              <ProvidersCard data={data} />
              <KnowledgeCard data={data} />
              <AiCard data={data} />
              <NotesCard data={data} />
              <ActivityCard data={data} />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 text-2xs text-faint">
            <span className="flex items-center gap-1">
              <FolderKanban className="size-3" /> {data.workspace.projects} 个项目
            </span>
            <span className="flex items-center gap-1">
              <Activity className="size-3" /> {data.ai.total_runs} 次 AI 运行
            </span>
            <span className="flex items-center gap-1">
              <Clock className="size-3" /> 平均 {formatMs(data.ai.avg_latency_ms)}
            </span>
            <span className="flex items-center gap-1">
              <AlertTriangle className="size-3" /> 失败 {data.ai.failed_runs} 次
            </span>
          </div>
        </>
      ) : null}
    </PageContainer>
  )
}
