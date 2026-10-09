import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Activity, ArrowUpRight, Search } from 'lucide-react'
import { api } from '@/lib/api'
import {
  compactNumber,
  formatMs,
  formatNumber,
  relativeTime,
  runStatusTone,
  runTypeLabel,
} from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/form'
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  Dot,
  PageContainer,
  Pagination,
  SectionHeader,
  StatTile,
  Tabs,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, Skeleton, SkeletonTable } from '@/components/ui/feedback'
import type { DailyMetric } from '@/lib/types'

const PAGE_SIZE = 20

const RUN_TYPES = [
  'RAG_ANSWER',
  'DOCUMENT_SUMMARY',
  'KNOWLEDGE_EXTRACTION',
  'TASK_PLANNER',
  'AGENT',
  'NOTE_GENERATION',
  'INTERVIEW_GENERATOR',
  'STUDY_PLAN',
  'EMBEDDING',
  'QUERY_REWRITE',
]

const RUN_STATUSES = [
  'RUNNING',
  'SUCCEEDED',
  'INCOMPLETE',
  'INTERRUPTED',
  'FAILED',
  'TIMEOUT',
  'CANCELLED',
]

/** Hand-rolled SVG chart: bars = runs/day, line = tokens/day. */
function DailyChart({ daily }: { daily: DailyMetric[] }) {
  if (daily.length === 0) {
    return <p className="py-8 text-center text-2xs text-faint">窗口内没有运行数据。</p>
  }

  const width = 720
  const height = 200
  const padding = { top: 16, right: 44, bottom: 26, left: 40 }
  const innerWidth = width - padding.left - padding.right
  const innerHeight = height - padding.top - padding.bottom

  const maxRuns = Math.max(1, ...daily.map((d) => d.runs))
  const maxTokens = Math.max(1, ...daily.map((d) => d.tokens))
  const step = innerWidth / daily.length
  const barWidth = Math.max(3, Math.min(18, step * 0.55))

  const tokenPoints = daily.map((d, index) => {
    const x = padding.left + step * index + step / 2
    const y = padding.top + innerHeight - (d.tokens / maxTokens) * innerHeight
    return `${x},${y}`
  })

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-[200px] w-full min-w-[560px]"
        role="img"
        aria-label="每日运行次数与 token 消耗"
      >
        {/* horizontal grid */}
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const y = padding.top + innerHeight * ratio
          return (
            <line
              key={ratio}
              x1={padding.left}
              x2={width - padding.right}
              y1={y}
              y2={y}
              stroke="rgb(var(--c-border-subtle))"
              strokeWidth={1}
            />
          )
        })}

        {/* left axis labels (runs) */}
        {[0, 0.5, 1].map((ratio) => {
          const y = padding.top + innerHeight * ratio
          return (
            <text
              key={ratio}
              x={padding.left - 6}
              y={y + 3}
              textAnchor="end"
              fontSize={9}
              fill="rgb(var(--c-faint))"
            >
              {Math.round(maxRuns * (1 - ratio))}
            </text>
          )
        })}

        {/* bars */}
        {daily.map((day, index) => {
          const barHeight = (day.runs / maxRuns) * innerHeight
          const x = padding.left + step * index + (step - barWidth) / 2
          const y = padding.top + innerHeight - barHeight
          return (
            <rect
              key={day.date}
              x={x}
              y={y}
              width={barWidth}
              height={Math.max(1, barHeight)}
              rx={2}
              fill="rgb(var(--c-accent))"
              opacity={0.75}
            >
              <title>{`${day.date}：${day.runs} 次运行 · ${day.tokens} tokens · ${day.avg_latency_ms} ms`}</title>
            </rect>
          )
        })}

        {/* token line */}
        <polyline
          points={tokenPoints.join(' ')}
          fill="none"
          stroke="rgb(var(--c-info))"
          strokeWidth={1.6}
          strokeLinejoin="round"
        />
        {daily.map((day, index) => {
          const [x, y] = tokenPoints[index].split(',')
          return (
            <circle key={`${day.date}-dot`} cx={Number(x)} cy={Number(y)} r={1.8} fill="rgb(var(--c-info))">
              <title>{`${day.date}：${day.tokens} tokens`}</title>
            </circle>
          )
        })}

        {/* x labels */}
        {daily.map((day, index) => {
          if (index % Math.ceil(daily.length / 8) !== 0 && index !== daily.length - 1) return null
          const x = padding.left + step * index + step / 2
          return (
            <text
              key={`${day.date}-label`}
              x={x}
              y={height - 8}
              textAnchor="middle"
              fontSize={9}
              fill="rgb(var(--c-faint))"
            >
              {day.date.slice(5)}
            </text>
          )
        })}
      </svg>

      <div className="mt-1 flex flex-wrap items-center gap-3 text-2xs text-faint">
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-sm bg-accent" /> 每日运行次数
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-info" /> 每日 tokens
        </span>
      </div>
    </div>
  )
}

function MetricsTab() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['ai-runs', 'metrics'],
    queryFn: ({ signal }) => api.aiRuns.metrics(30, signal),
    staleTime: 30_000,
  })

  if (isLoading) {
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-[74px]" />
          ))}
        </div>
        <Skeleton className="h-[240px]" />
      </div>
    )
  }

  if (isError) return <ErrorState title="指标加载失败" error={error} onRetry={() => void refetch()} />
  if (!data) return null

  const { metrics, daily } = data
  const byType = Object.entries(metrics.by_type ?? {}).sort((a, b) => b[1] - a[1])
  const byModel = Object.entries(metrics.by_model ?? {}).sort((a, b) => b[1] - a[1])

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        <StatTile
          label={`运行总数 · ${metrics.window_days}天`}
          value={formatNumber(metrics.total_runs)}
          sub={`失败 ${metrics.failed_runs} 次`}
          icon={<Activity className="size-3" />}
          tone="accent"
        />
        <StatTile
          label="失败率"
          value={`${(metrics.failure_rate * 100).toFixed(1)}%`}
          sub={metrics.failure_rate > 0.1 ? '高于 10%，请关注' : '处于健康区间'}
          tone={metrics.failure_rate > 0.1 ? 'danger' : 'success'}
        />
        <StatTile
          label="平均延迟"
          value={formatMs(metrics.avg_latency_ms)}
          sub="全部运行类型"
          tone="info"
        />
        <StatTile
          label="累计 Tokens"
          value={compactNumber(metrics.total_tokens)}
          sub={`估算成本 $${metrics.estimated_cost_usd.toFixed(4)}`}
          tone="neutral"
        />
      </div>

      <Card>
        <CardHeader title="每日运行与 Tokens" description={`最近 ${daily.length} 天`} />
        <CardBody>
          <DailyChart daily={daily} />
        </CardBody>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader title="按类型分布" />
          <CardBody className="space-y-1.5">
            {byType.length === 0 ? (
              <p className="text-2xs text-faint">暂无数据。</p>
            ) : (
              byType.map(([type, count]) => (
                <div key={type} className="flex items-center gap-2">
                  <span className="w-28 shrink-0 truncate text-2xs text-muted">{runTypeLabel(type)}</span>
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-elevated">
                    <div
                      className="h-full rounded-full bg-accent"
                      style={{ width: `${(count / (byType[0][1] || 1)) * 100}%` }}
                    />
                  </div>
                  <span className="w-8 shrink-0 text-right text-2xs text-faint tabular-nums">{count}</span>
                </div>
              ))
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="按模型分布" />
          <CardBody className="space-y-1.5">
            {byModel.length === 0 ? (
              <p className="text-2xs text-faint">暂无数据。</p>
            ) : (
              byModel.map(([model, count]) => (
                <div key={model} className="flex items-center gap-2">
                  <span className="w-28 shrink-0 truncate font-mono text-2xs text-muted">{model}</span>
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-elevated">
                    <div
                      className="h-full rounded-full bg-info"
                      style={{ width: `${(count / (byModel[0][1] || 1)) * 100}%` }}
                    />
                  </div>
                  <span className="w-8 shrink-0 text-right text-2xs text-faint tabular-nums">{count}</span>
                </div>
              ))
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}

export default function AIRunsPage() {
  const [tab, setTab] = useState<'list' | 'metrics'>('list')
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [runType, setRunType] = useState('')
  const [status, setStatus] = useState('')
  const [projectFilter, setProjectFilter] = useState('')

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })

  const listQuery = useQuery({
    queryKey: ['ai-runs', 'list', { page, search, runType, status, projectFilter }],
    queryFn: ({ signal }) =>
      api.aiRuns.list(
        {
          page,
          page_size: PAGE_SIZE,
          q: search || undefined,
          run_type: runType || undefined,
          status: status || undefined,
          project_id: projectFilter || undefined,
        },
        signal,
      ),
    enabled: tab === 'list',
    // Poll only while something is still running.
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? []
      return items.some((run) => run.status === 'RUNNING') ? 2500 : false
    },
  })

  const items = listQuery.data?.items ?? []
  const filtersActive = Boolean(search || runType || status || projectFilter)

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="AI 运行"
        description="每一次 AI 调用都会留下完整的执行轨迹：提示词版本、检索元数据、工具调用与引用。"
      />

      <Tabs
        value={tab}
        onChange={(key) => setTab(key as 'list' | 'metrics')}
        items={[
          { key: 'list', label: '运行列表', icon: <Activity className="size-3" /> },
          { key: 'metrics', label: '指标', icon: <ArrowUpRight className="size-3" /> },
        ]}
      />

      {tab === 'metrics' ? (
        <MetricsTab />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[180px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
              <Input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value)
                  setPage(1)
                }}
                placeholder="搜索 run_id / 模型 / 提示词…"
                className="pl-8"
                aria-label="搜索运行记录"
              />
            </div>
            <Select
              value={runType}
              onChange={(event) => {
                setRunType(event.target.value)
                setPage(1)
              }}
              className="w-auto min-w-[150px]"
              aria-label="按类型筛选"
            >
              <option value="">全部类型</option>
              {RUN_TYPES.map((type) => (
                <option key={type} value={type}>
                  {runTypeLabel(type)}
                </option>
              ))}
            </Select>
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value)
                setPage(1)
              }}
              className="w-auto min-w-[130px]"
              aria-label="按状态筛选"
            >
              <option value="">全部状态</option>
              {RUN_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
            <Select
              value={projectFilter}
              onChange={(event) => {
                setProjectFilter(event.target.value)
                setPage(1)
              }}
              className="w-auto min-w-[150px]"
              aria-label="按项目筛选"
            >
              <option value="">全部项目</option>
              {(projectsQuery.data?.items ?? []).map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </Select>
          </div>

          {listQuery.isLoading ? (
            <SkeletonTable rows={10} cols={7} />
          ) : listQuery.isError ? (
            <ErrorState title="运行记录加载失败" error={listQuery.error} onRetry={() => void listQuery.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={<Activity className="size-4" />}
              title={filtersActive ? '没有匹配的运行记录' : '还没有 AI 运行'}
              description={
                filtersActive
                  ? '调整筛选条件再试一次。'
                  : '发起一次 RAG 问答、生成计划或执行 Agent 后，运行记录会出现在这里。'
              }
              action={
                <Link to="/chat">
                  <Button size="sm" variant="secondary">
                    去提问
                  </Button>
                </Link>
              }
            />
          ) : (
            <>
              <Card className="overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[980px] text-left text-xs">
                    <thead className="border-b border-subtle bg-elevated text-2xs uppercase tracking-wide text-faint">
                      <tr>
                        <th className="px-3 py-2 font-medium">类型</th>
                        <th className="w-[100px] px-3 py-2 font-medium">状态</th>
                        <th className="w-[150px] px-3 py-2 font-medium">模型</th>
                        <th className="w-[170px] px-3 py-2 font-medium">提示词版本</th>
                        <th className="w-[90px] px-3 py-2 text-right font-medium">延迟</th>
                        <th className="w-[100px] px-3 py-2 text-right font-medium">Tokens</th>
                        <th className="w-[80px] px-3 py-2 text-right font-medium">工具</th>
                        <th className="w-[80px] px-3 py-2 text-right font-medium">检索</th>
                        <th className="w-[110px] px-3 py-2 font-medium">时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((run) => (
                        <tr key={run.run_id} className="border-b border-subtle last:border-b-0 hover:bg-elevated">
                          <td className="px-3 py-2">
                            <Link
                              to={`/ai-runs/${run.run_id}`}
                              className="flex items-center gap-2 text-primary hover:text-accent hover:underline"
                            >
                              <Dot tone={runStatusTone(run.status)} />
                              {runTypeLabel(run.run_type)}
                            </Link>
                            <p className="truncate font-mono text-2xs text-faint">{run.run_id}</p>
                          </td>
                          <td className="px-3 py-2">
                            <Badge tone={runStatusTone(run.status)}>{run.status}</Badge>
                            {run.error_code ? (
                              <p className="mt-0.5 truncate text-2xs text-danger" title={run.error_message}>
                                {run.error_code}
                              </p>
                            ) : null}
                          </td>
                          <td className="px-3 py-2">
                            <p className="truncate text-2xs text-muted">{run.model || '—'}</p>
                            <p className="truncate text-2xs text-faint">{run.provider}</p>
                          </td>
                          <td className="px-3 py-2">
                            <p className="truncate text-2xs text-muted">{run.prompt_name || '—'}</p>
                            <p className="font-mono text-2xs text-faint">v{run.prompt_version}</p>
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">
                            {formatMs(run.latency_ms)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">
                            {formatNumber(run.total_tokens)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">
                            {run.tool_call_count}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-muted">
                            {run.retrieval_count}
                          </td>
                          <td className="px-3 py-2 text-faint">{relativeTime(run.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Pagination
                page={listQuery.data?.meta.page ?? 1}
                pageSize={listQuery.data?.meta.page_size ?? PAGE_SIZE}
                total={listQuery.data?.meta.total ?? 0}
                onPage={setPage}
              />
            </>
          )}
        </>
      )}
    </PageContainer>
  )
}
