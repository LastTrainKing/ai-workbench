/**
 * The agent workbench — the page the whole product is built around.
 *
 * This is not a chat window. A chat window shows what the model said; this shows
 * what it *did*: the plan it committed to, each step as it moves, every tool call
 * with its arguments and result, the verification that decided whether a step
 * passed, the retries when it did not, and the diff left behind.
 *
 * Everything on this page comes from the live run. There is no placeholder data:
 * if the backend is not running, the page says so rather than showing a
 * convincing-looking fiction.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleDashed,
  FileDiff,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  Square,
  Undo2,
  X,
  XCircle,
} from 'lucide-react'

import { Badge, Card, CardBody, CardHeader, PageContainer, SectionHeader } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { EmptyState, InlineAlert, ProgressBar, Spinner } from '@/components/ui/feedback'
import { Markdown } from '@/components/Markdown'
import { useAgentRun } from '@/features/agent/useAgentRun'
import { describeState, isTerminal } from '@/lib/agentApi'
import type { DiffResult, PlanStep, RunEvent, RunState, StepStatus } from '@/lib/agentApi'
import { cn } from '@/lib/utils'

/* ------------------------------------------------------------------ atoms -- */

function StepIcon({ status }: { status: StepStatus }) {
  if (status === 'DONE') return <Check className="size-3.5" />
  if (status === 'FAILED') return <X className="size-3.5" />
  if (status === 'RUNNING') return <Loader2 className="size-3.5 animate-spin" />
  if (status === 'SKIPPED') return <ChevronRight className="size-3.5" />
  return <CircleDashed className="size-3.5" />
}

const STEP_TONE: Record<StepStatus, string> = {
  DONE: 'border-success/40 bg-success/10 text-success',
  FAILED: 'border-danger/40 bg-danger/10 text-danger',
  RUNNING: 'border-accent/50 bg-accent/15 text-accent',
  SKIPPED: 'border-border text-faint',
  PENDING: 'border-border text-faint',
}

function PlanTimeline({ steps, currentId }: { steps: PlanStep[]; currentId: string }) {
  return (
    <ol className="space-y-1">
      {steps.map((step) => {
        const active = step.id === currentId || step.status === 'RUNNING'
        return (
          <li
            key={step.id}
            className={cn('flex gap-3 rounded-lg px-2 py-2', active && 'bg-accent/5')}
          >
            <span
              className={cn(
                'mt-0.5 grid size-7 shrink-0 place-items-center rounded-full border',
                STEP_TONE[step.status],
              )}
            >
              <StepIcon status={step.status} />
            </span>
            <div className="min-w-0 flex-1">
              <p
                className={cn(
                  'text-[13px] leading-snug',
                  step.status === 'PENDING' ? 'text-faint' : 'text-text',
                  active && 'font-medium',
                )}
              >
                {step.description}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-faint">
                {step.tool_calls > 0 && <span>{step.tool_calls} 次工具</span>}
                {step.retry_count > 0 && <span className="text-warn">重试 {step.retry_count}</span>}
                {step.status === 'DONE' && step.completed_at && <span>{step.completed_at}</span>}
              </div>
              {step.error && (
                <p className="mt-1 line-clamp-2 text-[11px] text-danger/90">{step.error}</p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

const EVENT_ICON: Record<string, typeof Check> = {
  plan: Sparkles,
  step: Check,
  tool: ChevronRight,
  verification: ShieldAlert,
  error: XCircle,
  result: Check,
  state: ArrowRight,
}

function EventRow({ event }: { event: RunEvent }) {
  const Icon = EVENT_ICON[event.kind] ?? ArrowRight
  const payload = (event.data ?? {}) as { ok?: boolean; exit_code?: number | null }
  const failed = payload.ok === false

  const tone =
    event.kind === 'error' || failed
      ? 'text-danger'
      : event.kind === 'verification'
        ? 'text-info'
        : event.kind === 'step'
          ? 'text-success'
          : event.kind === 'tool'
            ? 'text-accent'
            : 'text-muted'

  return (
    <div className="flex gap-2.5 border-b border-border/40 px-3 py-2 last:border-0">
      <Icon className={cn('mt-0.5 size-3.5 shrink-0', tone)} />
      <div className="min-w-0 flex-1">
        <p className="break-words text-[12.5px] text-text">{event.message}</p>
        {(payload.exit_code !== undefined && payload.exit_code !== null) || failed ? (
          <p className="mt-0.5 text-[11px] text-faint">
            {payload.exit_code !== undefined && payload.exit_code !== null && (
              <span>退出码 {payload.exit_code} </span>
            )}
            {failed && <span className="text-danger">执行失败</span>}
          </p>
        ) : null}
      </div>
      <span className="shrink-0 font-mono text-[10.5px] text-faint">{event.at.slice(11, 19)}</span>
    </div>
  )
}

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-lg border border-border bg-inset/50 px-3 py-2">
      <p className="text-[10.5px] uppercase tracking-wide text-faint">{label}</p>
      <p className={cn('mt-0.5 font-mono text-[15px] tabular-nums', tone ?? 'text-text')}>
        {value}
      </p>
    </div>
  )
}

function DiffList({ diff }: { diff: DiffResult | null }) {
  if (!diff) return <p className="px-3 py-4 text-[12px] text-faint">暂无变更数据。</p>
  if (!diff.available) return <p className="px-3 py-4 text-[12px] text-faint">{diff.message}</p>
  if (!diff.files.length) {
    return <p className="px-3 py-4 text-[12px] text-faint">本次运行没有改动工作区文件。</p>
  }
  const tone = { added: 'text-success', removed: 'text-danger', modified: 'text-info' } as const
  const label = { added: '新增', removed: '删除', modified: '修改' } as const
  return (
    <div className="divide-y divide-border/40">
      {diff.files.slice(0, 80).map((file) => (
        <div key={file.path} className="flex items-center gap-3 px-3 py-1.5">
          <span className={cn('w-10 shrink-0 text-[11px] font-medium', tone[file.change])}>
            {label[file.change]}
          </span>
          <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-text">
            {file.path}
          </span>
          <span className="shrink-0 font-mono text-[11px] text-faint">
            {file.lines_added > 0 && <span className="text-success">+{file.lines_added}</span>}
            {file.lines_removed > 0 && (
              <span className="ml-1 text-danger">-{file.lines_removed}</span>
            )}
          </span>
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------- page -- */

const EXAMPLES = [
  '分析这个项目，运行测试，找到失败原因并修复，直到测试通过',
  '在 demo 目录创建 Python 项目，含实现和 pytest 测试，跑通后 git 提交',
  '检查项目结构并生成一份 README 草稿',
]

export default function AgentWorkbenchPage() {
  const run = useAgentRun()
  const [goal, setGoal] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const streamRef = useRef<HTMLDivElement>(null)

  const state = run.status?.state ?? (run.starting ? ('PLANNING' as RunState) : null)
  const meta = state ? describeState(state) : null
  const running = state !== null && !isTerminal(state)
  const plan = run.status?.plan ?? null

  useEffect(() => {
    const node = streamRef.current
    if (!node) return
    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 140
    if (nearBottom) node.scrollTop = node.scrollHeight
  }, [run.events.length])

  const verification = useMemo(() => {
    const rows = run.events.filter((e) => e.kind === 'verification')
    return {
      total: rows.length,
      passed: rows.filter((e) => (e.data as { passed?: boolean }).passed === true).length,
    }
  }, [run.events])

  const toneClass =
    {
      success: 'text-success',
      danger: 'text-danger',
      warn: 'text-warn',
      info: 'text-info',
      accent: 'text-accent',
      muted: 'text-muted',
    }[meta?.tone ?? 'muted'] ?? 'text-muted'

  const finalAnswer = useMemo(() => {
    const last = [...run.events].reverse().find((e) => e.kind === 'result')
    return (last?.data as { answer?: string } | undefined)?.answer ?? ''
  }, [run.events])

  // The backend reaches a terminal state a second or two before it emits the
  // final summary, so "no answer yet" is a normal intermediate state rather than
  // a failure. Saying "未生成文字总结" during that window reads like the run lost
  // its result.
  const awaitingAnswer = !finalAnswer && run.status !== null && !run.error

  async function rollback() {
    if (!run.runId) return
    const { agentApi } = await import('@/lib/agentApi')
    const first = await agentApi.rollback(run.runId, false)
    if (first.data.restored) {
      setNotice(first.data.message)
      return
    }
    const conflicts = first.data.conflicts ?? []
    setNotice(
      `有 ${conflicts.length} 个文件在 Agent 结束后被修改，回滚会丢弃这些改动：\n` +
        conflicts
          .slice(0, 5)
          .map((c) => `· ${c.path} — ${c.reason}`)
          .join('\n'),
    )
  }

  async function forceRollback() {
    if (!run.runId) return
    const { agentApi } = await import('@/lib/agentApi')
    const forced = await agentApi.rollback(run.runId, true)
    setNotice(forced.data.message || '回滚完成')
  }

  return (
    <PageContainer className="flex h-full min-h-0 flex-col gap-4">
      <SectionHeader
        title="Agent"
        description="把目标交给它。它会规划、执行、验证，失败时自己修复。"
        actions={
          running ? (
            <div className="flex items-center gap-2">
              {run.status?.paused ? (
                <Button size="sm" variant="secondary" onClick={() => void run.resume()}>
                  <Play className="size-3.5" /> 继续
                </Button>
              ) : (
                <Button size="sm" variant="secondary" onClick={() => void run.pause()}>
                  <Pause className="size-3.5" /> 暂停
                </Button>
              )}
              <Button size="sm" variant="danger" onClick={() => void run.cancel()}>
                <Square className="size-3.5" /> 停止
              </Button>
            </div>
          ) : run.runId ? (
            <Button size="sm" variant="secondary" onClick={run.reset}>
              <RotateCcw className="size-3.5" /> 新任务
            </Button>
          ) : null
        }
      />

      {run.error && (
        <InlineAlert tone="danger" title="出错了">
          {run.error}
        </InlineAlert>
      )}

      {notice && (
        <InlineAlert tone="warn" title="回滚">
          <pre className="whitespace-pre-wrap font-sans text-[12.5px]">{notice}</pre>
          <div className="mt-2 flex gap-2">
            <Button size="xs" variant="danger" onClick={() => void forceRollback()}>
              强制回滚
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setNotice(null)}>
              取消
            </Button>
          </div>
        </InlineAlert>
      )}

      {/* ------------------------------------------------------ goal input -- */}
      {!run.runId && (
        <Card>
          <CardBody className="space-y-3">
            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void run.start(goal)
              }}
              rows={3}
              placeholder="告诉 Agent 你想做什么…  例如：修复这个项目的测试"
              className="w-full resize-none rounded-lg border border-border bg-inset px-3 py-2.5 text-[13.5px] text-text outline-none transition-colors placeholder:text-faint focus:border-accent/60"
            />
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-1.5">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => setGoal(example)}
                    className="rounded-full border border-border px-2.5 py-1 text-[11.5px] text-muted transition-colors hover:border-accent/40 hover:text-text"
                  >
                    {example.slice(0, 22)}…
                  </button>
                ))}
              </div>
              <Button
                variant="primary"
                size="md"
                disabled={!goal.trim() || run.starting}
                onClick={() => void run.start(goal)}
              >
                {run.starting ? (
                  <Spinner className="size-3.5" />
                ) : (
                  <Sparkles className="size-3.5" />
                )}
                开始执行
              </Button>
            </div>
          </CardBody>
        </Card>
      )}

      {/* ------------------------------------------------------- workbench -- */}
      {run.runId && (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)_minmax(0,0.8fr)]">
          <Card className="flex min-h-0 flex-col">
            <CardHeader
              title="执行计划"
              description={plan ? `${plan.done_steps}/${plan.total_steps} 步完成` : '生成中…'}
              actions={
                meta ? <span className={cn('text-[12px] font-medium', toneClass)}>{meta.label}</span> : null
              }
            />
            {plan && <ProgressBar value={plan.progress * 100} className="mx-4 mt-1" />}
            <CardBody className="min-h-0 flex-1 overflow-y-auto">
              {plan ? (
                <PlanTimeline steps={plan.steps} currentId={run.status?.current_step_id ?? ''} />
              ) : (
                <div className="flex items-center gap-2 text-[13px] text-faint">
                  <Loader2 className="size-3.5 animate-spin" /> 正在制定计划…
                </div>
              )}
              {plan?.success_criteria?.length ? (
                <div className="mt-4 border-t border-border pt-3">
                  <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">成功标准</p>
                  <ul className="space-y-1">
                    {plan.success_criteria.map((criterion) => (
                      <li key={criterion} className="flex gap-2 text-[12px] text-muted">
                        <Check className="mt-0.5 size-3 shrink-0 text-success/70" />
                        <span>{criterion}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </CardBody>
          </Card>

          <Card className="flex min-h-0 flex-col">
            <CardHeader
              title="实时执行"
              description={`${run.events.length} 个事件`}
              actions={running ? <Loader2 className="size-3.5 animate-spin text-accent" /> : null}
            />
            <div ref={streamRef} className="min-h-0 flex-1 overflow-y-auto">
              {run.events.length === 0 ? (
                <div className="flex h-full items-center justify-center text-[13px] text-faint">
                  <Loader2 className="mr-2 size-3.5 animate-spin" /> 等待 Agent…
                </div>
              ) : (
                run.events.map((event) => <EventRow key={event.seq} event={event} />)
              )}
            </div>
          </Card>

          <div className="flex min-h-0 flex-col gap-4">
            <Card>
              <CardHeader title="运行状态" />
              <CardBody className="grid grid-cols-2 gap-2">
                <Metric label="工具调用" value={run.status?.tool_calls ?? 0} />
                <Metric
                  label="重试"
                  value={run.status?.retries ?? 0}
                  tone={(run.status?.retries ?? 0) > 0 ? 'text-warn' : undefined}
                />
                <Metric label="验证" value={`${verification.passed}/${verification.total}`} />
                <Metric label="耗时" value={`${run.status?.elapsed_seconds ?? 0}s`} />
                <Metric label="改动文件" value={run.status?.files_modified ?? 0} />
                <Metric label="事件" value={run.status?.event_count ?? 0} />
              </CardBody>
            </Card>

            <Card className="flex min-h-0 flex-1 flex-col">
              <CardHeader
                title="文件变更"
                actions={
                  run.diff?.available ? <Badge tone="neutral">{run.diff.changed ?? 0}</Badge> : null
                }
              />
              <div className="min-h-0 flex-1 overflow-y-auto">
                {run.diff ? (
                  <DiffList diff={run.diff} />
                ) : (
                  <p className="px-3 py-4 text-[12px] text-faint">
                    {running ? '运行结束后生成变更摘要。' : '暂无变更数据。'}
                  </p>
                )}
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* ------------------------------------------------------ confirmation -- */}
      {run.confirmations.map((confirmation) => (
        <Card key={confirmation.id} className="border-warn/40">
          <CardHeader
            title="需要你确认"
            description={`风险等级：${confirmation.risk}`}
            actions={<Badge tone="warn">等待中</Badge>}
          />
          <CardBody className="space-y-3">
            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <p className="text-[11px] uppercase tracking-wide text-faint">操作</p>
                <p className="font-mono text-[13px] text-text">{confirmation.operation}</p>
              </div>
              <div className="min-w-0">
                <p className="text-[11px] uppercase tracking-wide text-faint">目标</p>
                <p className="truncate font-mono text-[12.5px] text-text">{confirmation.target}</p>
              </div>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-faint">原因</p>
              <p className="text-[13px] text-muted">{confirmation.reason}</p>
            </div>
            {confirmation.impact && (
              <InlineAlert tone="warn" title="影响">
                {confirmation.impact}
              </InlineAlert>
            )}
            <div className="flex gap-2">
              <Button
                variant="primary"
                size="sm"
                onClick={() => void run.decide(confirmation.id, true)}
              >
                <Check className="size-3.5" /> 允许一次
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void run.decide(confirmation.id, false)}
              >
                <X className="size-3.5" /> 拒绝
              </Button>
            </div>
          </CardBody>
        </Card>
      ))}

      {/* ------------------------------------------------------------ result -- */}
      {run.status && isTerminal(run.status.state) && (
        <Card
          className={cn(
            run.status.state === 'COMPLETED' ? 'border-success/40' : 'border-danger/40',
          )}
        >
          <CardHeader
            title="执行结果"
            actions={
              <Badge tone={run.status.state === 'COMPLETED' ? 'success' : 'danger'}>
                {describeState(run.status.state).label}
              </Badge>
            }
          />
          <CardBody>
            {run.diff?.available && (run.diff.changed ?? 0) > 0 && (
              <div className="mb-3 flex items-center gap-2 text-[12px] text-muted">
                <FileDiff className="size-3.5" />
                本次运行改动了 {run.diff.changed} 个文件
              </div>
            )}
            <div className="text-[13px] leading-relaxed text-text">
              {awaitingAnswer ? (
                <span className="flex items-center gap-2 text-faint">
                  <Loader2 className="size-3.5 animate-spin" /> 正在生成执行总结…
                </span>
              ) : (
                <Markdown>{finalAnswer || '本次运行没有产生文字总结，请查看上方事件流。'}</Markdown>
              )}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => void run.refreshDiff()}>
                <FileDiff className="size-3.5" /> 刷新变更
              </Button>
              <Button size="sm" variant="outline" onClick={() => void rollback()}>
                <Undo2 className="size-3.5" /> 回滚本次改动
              </Button>
            </div>
          </CardBody>
        </Card>
      )}

      {!run.runId && !run.starting && (
        <EmptyState
          icon={<Sparkles className="size-5" />}
          title="Agent 还没有任务"
          description="给它一个目标。它会制定计划、调用工具、验证结果，失败时自己修复。"
        />
      )}
    </PageContainer>
  )
}
