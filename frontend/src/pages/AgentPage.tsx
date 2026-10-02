import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronDown,
  Cpu,
  Play,
  Search,
  Wrench,
  XCircle,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatMs } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select, Textarea } from '@/components/ui/form'
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  CodeBlock,
  PageContainer,
  SectionHeader,
  Tabs,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, InlineAlert, SkeletonText } from '@/components/ui/feedback'
import { Markdown } from '@/components/Markdown'
import { KnowledgeBasePicker } from '@/features/knowledge/KnowledgeBasePicker'
import { useToastStore, toastApiError } from '@/store/toast'
import type { AgentRunResult } from '@/lib/types'

const STOPPED_REASON_LABEL: Record<string, string> = {
  final_answer: '已给出最终答案',
  max_steps: '达到最大步数上限',
  loop_detected: '检测到循环调用',
  empty_response: '模型返回为空',
}

function TrajectoryTimeline({ result }: { result: AgentRunResult }) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({ 0: true })

  if (result.steps.length === 0) {
    return <InlineAlert tone="info" title="这次运行没有产生工具调用步骤">Agent 直接给出了回答。</InlineAlert>
  }

  return (
    <ol className="relative space-y-2.5">
      {result.steps.map((step, index) => {
        const open = Boolean(expanded[index])
        return (
          <li key={`${step.index}-${index}`} className="relative flex gap-3">
            <div className="flex w-6 shrink-0 flex-col items-center">
              <span
                className={cn(
                  'flex size-6 items-center justify-center rounded-full border text-2xs font-semibold',
                  step.ok
                    ? 'border-success/40 bg-success-soft text-success'
                    : 'border-danger/40 bg-danger-soft text-danger',
                )}
              >
                {step.ok ? <CheckCircle2 className="size-3" /> : <XCircle className="size-3" />}
              </span>
              {index < result.steps.length - 1 ? (
                <span className="mt-1 w-px flex-1 bg-subtle" aria-hidden />
              ) : null}
            </div>

            <div className="min-w-0 flex-1 pb-1">
              <button
                type="button"
                onClick={() => setExpanded((prev) => ({ ...prev, [index]: !open }))}
                aria-expanded={open}
                className="flex w-full items-center gap-2 rounded-md border border-subtle bg-surface px-2.5 py-1.5 text-left transition-colors hover:border-strong"
              >
                <span className="font-mono text-2xs text-faint">#{step.index}</span>
                <Wrench className="size-3 shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-primary">
                  {step.tool || '（无工具）'}
                </span>
                <Badge tone="neutral">{formatMs(step.latency_ms)}</Badge>
                <ChevronDown className={cn('size-3.5 shrink-0 text-faint transition-transform', open && 'rotate-180')} />
              </button>

              {open ? (
                <div className="mt-1.5 space-y-2 pl-1">
                  {step.thought ? (
                    <div className="rounded-md border border-subtle bg-elevated px-2.5 py-2">
                      <p className="mb-1 flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">
                        <Cpu className="size-3" />
                        思考
                      </p>
                      <p className="text-xs leading-relaxed text-muted">{step.thought}</p>
                    </div>
                  ) : null}

                  <div>
                    <p className="mb-1 text-2xs font-semibold uppercase tracking-wider text-faint">
                      参数
                    </p>
                    <CodeBlock value={step.arguments ?? {}} maxHeight={160} />
                  </div>

                  {step.summary ? (
                    <div className="rounded-md border border-subtle bg-inset px-2.5 py-2">
                      <p className="mb-1 text-2xs font-semibold uppercase tracking-wider text-faint">
                        结果摘要
                      </p>
                      <p className="text-xs leading-relaxed text-muted">{step.summary}</p>
                    </div>
                  ) : null}

                  {step.error ? (
                    <InlineAlert tone="danger" title="工具执行失败">
                      {step.error}
                    </InlineAlert>
                  ) : null}
                </div>
              ) : null}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function ToolsCatalogue() {
  const [search, setSearch] = useState('')
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['agents', 'tools'],
    queryFn: ({ signal }) => api.agents.tools(signal),
    staleTime: 5 * 60_000,
  })

  const tools = (data?.tools ?? []).filter((tool) => {
    const term = search.trim().toLowerCase()
    if (!term) return true
    return (
      tool.name.toLowerCase().includes(term) ||
      tool.description.toLowerCase().includes(term) ||
      tool.category.toLowerCase().includes(term)
    )
  })

  const categories = [...new Set((data?.tools ?? []).map((tool) => tool.category))]

  if (isLoading) return <SkeletonText lines={6} />
  if (isError) return <ErrorState compact error={error} onRetry={() => void refetch()} />

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索工具…"
            className="pl-8"
            aria-label="搜索工具"
          />
        </div>
        <Badge tone="neutral">{data?.count ?? 0} 个工具</Badge>
      </div>

      <div className="flex flex-wrap gap-1">
        {categories.map((category) => (
          <Badge key={category} tone="accent">
            {category}
          </Badge>
        ))}
      </div>

      <ul className="space-y-2">
        {tools.map((tool) => (
          <li key={tool.name}>
            <Card>
              <CardBody className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="font-mono text-xs font-semibold text-primary">{tool.name}</code>
                  <Badge tone={tool.is_write ? 'warn' : 'neutral'}>
                    {tool.is_write ? '写入型' : '只读'}
                  </Badge>
                  <Badge tone="neutral">{tool.category}</Badge>
                </div>
                <p className="text-2xs leading-relaxed text-muted">{tool.description}</p>
                {tool.parameters && Object.keys(tool.parameters).length > 0 ? (
                  <details className="text-2xs">
                    <summary className="cursor-pointer text-faint hover:text-primary">参数结构</summary>
                    <div className="mt-1.5">
                      <CodeBlock value={tool.parameters} maxHeight={200} />
                    </div>
                  </details>
                ) : null}
              </CardBody>
            </Card>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function AgentPage() {
  const success = useToastStore((state) => state.success)
  const [tab, setTab] = useState<'run' | 'tools'>('run')

  const [goal, setGoal] = useState('')
  const [kbIds, setKbIds] = useState<string[]>([])
  const [projectId, setProjectId] = useState('')
  const [maxSteps, setMaxSteps] = useState(8)
  const [traceId, setTraceId] = useState('')

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })

  const runMutation = useMutation({
    mutationFn: () =>
      api.agents.run({
        goal: goal.trim(),
        project_id: projectId || null,
        knowledge_base_ids: kbIds,
        max_steps: maxSteps,
      }),
    onSuccess: () => success('Agent 执行完成'),
    onError: (error) => toastApiError(error, 'Agent 执行失败'),
  })

  const traceQuery = useQuery({
    queryKey: ['agents', 'trace', traceId],
    queryFn: ({ signal }) => api.agents.trace(traceId, signal),
    enabled: traceId.trim().length > 0,
  })

  const result = runMutation.data?.data ?? (traceQuery.data as AgentRunResult | undefined)

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="Agent"
        description="Agent 会自主规划并调用工具（检索、建任务、写笔记），每一步都可回放。"
      />

      <Tabs
        value={tab}
        onChange={(key) => setTab(key as 'run' | 'tools')}
        items={[
          { key: 'run', label: '执行', icon: <Play className="size-3" /> },
          { key: 'tools', label: '工具目录', icon: <Wrench className="size-3" /> },
        ]}
      />

      {tab === 'tools' ? (
        <ToolsCatalogue />
      ) : (
        <>
          <Card>
            <CardBody className="space-y-3.5">
              <Field label="目标" htmlFor="agent-goal" required hint="描述得越具体，Agent 越容易选对工具">
                <Textarea
                  id="agent-goal"
                  rows={2}
                  value={goal}
                  maxLength={2000}
                  onChange={(event) => setGoal(event.target.value)}
                  placeholder="例如：阅读分布式系统资料，整理出 3 个复习任务并写一条总结笔记"
                />
              </Field>

              <Field label="参考资料（知识库）">
                <KnowledgeBasePicker selected={kbIds} onChange={setKbIds} projectId={projectId || undefined} />
              </Field>

              <div className="grid gap-3.5 sm:grid-cols-2">
                <Field label="关联项目" htmlFor="agent-project">
                  <Select
                    id="agent-project"
                    value={projectId}
                    onChange={(event) => setProjectId(event.target.value)}
                  >
                    <option value="">不关联项目</option>
                    {(projectsQuery.data?.items ?? []).map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="最大步数" htmlFor="agent-steps" hint="1–16，步数越多越可能完成复杂目标">
                  <Input
                    id="agent-steps"
                    type="number"
                    min={1}
                    max={16}
                    value={maxSteps}
                    onChange={(event) => setMaxSteps(Number(event.target.value))}
                  />
                </Field>
              </div>

              <Button
                variant="primary"
                size="md"
                loading={runMutation.isPending}
                disabled={goal.trim().length < 2}
                icon={<Play className="size-3.5" />}
                onClick={() => runMutation.mutate()}
              >
                执行
              </Button>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="加载历史轨迹" description="按 run_id 查看之前一次 Agent 执行的完整步骤" />
            <CardBody className="flex flex-wrap items-end gap-2">
              <Field label="run_id" className="min-w-[220px] flex-1">
                <Input
                  value={traceId}
                  onChange={(event) => setTraceId(event.target.value)}
                  placeholder="run_xxxxxxxx"
                />
              </Field>
              <Button
                size="sm"
                variant="secondary"
                loading={traceQuery.isFetching}
                disabled={!traceId.trim()}
                onClick={() => void traceQuery.refetch()}
              >
                加载
              </Button>
            </CardBody>
          </Card>

          {runMutation.isPending ? (
            <Card>
              <CardBody className="space-y-3">
                <div className="flex items-center gap-2 text-xs text-muted">
                  <Bot className="size-4 animate-pulse text-accent" />
                  Agent 正在规划并调用工具…
                </div>
                <SkeletonText lines={6} />
              </CardBody>
            </Card>
          ) : runMutation.isError ? (
            <ErrorState title="Agent 执行失败" error={runMutation.error} onRetry={() => runMutation.mutate()} />
          ) : traceQuery.isError ? (
            <ErrorState title="轨迹加载失败" error={traceQuery.error} onRetry={() => void traceQuery.refetch()} />
          ) : result ? (
            <div className="space-y-3">
              <Card>
                <CardBody className="space-y-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={result.status === 'SUCCEEDED' ? 'success' : 'warn'}>{result.status}</Badge>
                    <Badge tone="neutral">{result.steps.length} 步</Badge>
                    <Badge tone="info">{formatMs(result.latency_ms)}</Badge>
                    {result.stopped_reason ? (
                      <Badge tone="neutral">
                        {STOPPED_REASON_LABEL[result.stopped_reason] ?? result.stopped_reason}
                      </Badge>
                    ) : null}
                    <span className="ml-auto font-mono text-2xs text-faint">{result.run_id}</span>
                    <Link to={`/ai-runs/${result.run_id}`} className="text-2xs text-accent hover:underline">
                      完整轨迹
                    </Link>
                  </div>

                  <p className="text-xs text-muted">
                    <span className="text-faint">目标：</span>
                    {result.goal}
                  </p>

                  {result.created &&
                  ((result.created.tasks?.length ?? 0) > 0 || (result.created.notes?.length ?? 0) > 0) ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-2xs text-faint">创建实体：</span>
                      {(result.created.tasks ?? []).map((id) => (
                        <Link key={id} to={`/tasks?task=${id}`}>
                          <Badge tone="accent">任务 {id.slice(0, 8)}</Badge>
                        </Link>
                      ))}
                      {(result.created.notes ?? []).map((id) => (
                        <Link key={id} to={`/notes?note=${id}`}>
                          <Badge tone="info">笔记 {id.slice(0, 8)}</Badge>
                        </Link>
                      ))}
                    </div>
                  ) : null}

                  {result.stopped_reason === 'max_steps' ? (
                    <InlineAlert tone="warn" title="达到最大步数上限">
                      可以提高最大步数后重新执行，或把目标拆得更小。
                    </InlineAlert>
                  ) : null}
                  {result.stopped_reason === 'loop_detected' ? (
                    <InlineAlert tone="warn" title="检测到循环调用">
                      Agent 重复调用同一工具，已提前终止。请调整目标描述。
                    </InlineAlert>
                  ) : null}
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="执行轨迹" description="点击每一步查看思考、参数与结果" />
                <CardBody>
                  <TrajectoryTimeline result={result} />
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="最终回答" />
                <CardBody>
                  {result.final_answer ? (
                    <Markdown>{result.final_answer}</Markdown>
                  ) : (
                    <p className="flex items-center gap-1.5 text-2xs text-faint">
                      <AlertTriangle className="size-3" />
                      本次执行没有产生最终回答。
                    </p>
                  )}
                </CardBody>
              </Card>
            </div>
          ) : (
            <EmptyState
              icon={<Bot className="size-4" />}
              title="还没有执行结果"
              description="填写目标并选择参考资料，Agent 会自主调用工具完成任务。"
            />
          )}
        </>
      )}
    </PageContainer>
  )
}
