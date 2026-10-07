/**
 * The home screen. Four areas, top to bottom:
 *
 *   ① 交给 AI 一件事   the entry point — goal, scope, format
 *   ② 正在处理         what it is working on right now, with real step progress
 *   ③ 最近产出         what it produced, and whether you accepted it
 *   ④ 它学到了什么      what it learned from you
 *
 * The fourth area is the one that makes this product rather than an agent
 * workbench. It is the visible half of a loop whose invisible half is the memory
 * evaluator: every accepted or rejected delivery is a statement about a standard,
 * and this is where the user sees that statement become a belief the system holds.
 *
 * Every number on this page comes from a row. There is no placeholder, no sample
 * data and no animation that advances while nothing happens — a progress bar that
 * moves on its own is a lie the user catches within a minute.
 */

import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowRight,
  Check,
  FileText,
  KeyRound,
  Loader2,
  Package,
  Sparkles,
  TrendingUp,
  X,
} from 'lucide-react'

import { Badge, Card, CardBody, CardHeader, PageContainer } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, InlineAlert, LoadingBlock } from '@/components/ui/feedback'
import { deliveryApi, KIND_LABELS, STATUS_LABELS, STATUS_TONES, acceptanceLabel, formatSize, formatWhen } from '@/lib/deliveryApi'
import type { BuildRun, DeliverableKind } from '@/lib/deliveryApi'
import { contextApi } from '@/lib/contextApi'
import { api, toApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

const FORMATS: { value: DeliverableKind; label: string }[] = [
  { value: 'pptx', label: 'PPT' },
  { value: 'docx', label: 'Word' },
  { value: 'xlsx', label: 'Excel' },
  { value: 'pdf', label: 'PDF' },
  { value: 'report', label: '报告' },
  { value: 'code', label: '代码' },
]

const EXAMPLES = [
  '用我的资料做一份 15 分钟的答辩 PPT',
  '把这批文档整理成一份课程报告',
  '分析这些数据并做一张汇总表',
]

/**
 * Thrown when the run was created but would not start.
 *
 * This is a distinct failure on purpose. "It could not be created" and "it was
 * created but could not start" need different words and different recovery, and
 * collapsing them into one message is how the original DRAFT-forever bug stayed
 * invisible: the create succeeded, nothing arrived, and the screen looked calm.
 */
class StartFailedError extends Error {
  readonly buildRunId: string
  constructor(buildRunId: string, message: string) {
    super(message)
    this.name = 'StartFailedError'
    this.buildRunId = buildRunId
  }
}

/* ------------------------------------------------------- ⓿ needs a model -- */

/**
 * The first thing a fresh install needs is a model, and it needs to be told that
 * once, in a size it cannot miss. A pill in the corner is not a first-class
 * surface — the user's own words for this were "应该让 api 配置很显眼吧".
 *
 * Stated as a step to take, not an error to clear.
 */
function ModelSetupNotice({ reason }: { reason: string }) {
  return (
    <div
      data-testid="model-setup-notice"
      className="rounded-xl border border-warn/40 bg-warn-soft/60 p-3.5"
    >
      <div className="flex items-start gap-2.5">
        <KeyRound className="mt-0.5 size-4 shrink-0 text-warn" />
        <div className="min-w-0 flex-1">
          <p className="text-[13.5px] font-medium text-text">先给它一个模型，它才能干活</p>
          <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
            {reason}
            填一个 API Key 就好，一分钟的事；配好之后这里会自动消失。
          </p>
        </div>
        <Link to="/settings" className="shrink-0">
          <Button size="sm" variant="primary" data-testid="goto-model-settings">
            去配置
          </Button>
        </Link>
      </div>
    </div>
  )
}

/* ------------------------------------------------------- ① hand it a goal -- */

function GoalComposer() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [goal, setGoal] = useState('')
  const [kind, setKind] = useState<DeliverableKind>('pptx')
  const [notice, setNotice] = useState<string | null>(null)

  const knowledge = useQuery({
    queryKey: ['knowledge', 'bases'],
    queryFn: async () => {
      const { api } = await import('@/lib/api')
      const result = await api.knowledgeBases.list()
      return result.items ?? []
    },
    retry: false,
  })

  // Can it actually work? Only the llm matters. Embeddings falling back to the
  // offline engine is deliberate — DeepSeek has no embedding endpoint — so
  // treating that as "unconfigured" would block a perfectly working setup.
  const health = useQuery({
    queryKey: ['health', 'deps'],
    queryFn: ({ signal }) => api.health.deps(signal),
    retry: false,
  })

  const create = useMutation({
    mutationFn: async () => {
      const created = await deliveryApi.createBuildRun({
        goal: goal.trim(),
        deliverable_kind: kind,
        // The scope is explicit: "which of my documents may it read" is a question
        // the user is entitled to answer, so all bases are selected by default and
        // the selection is visible rather than implied.
        knowledge_base_ids: (knowledge.data ?? []).map((kb: { id: string }) => kb.id),
      })

      // A run that was created but never started is the exact bug this replaced.
      // The two steps are kept visibly separate so a start failure is a start
      // failure — it can never be reported as "nothing happened".
      try {
        const started = await deliveryApi.executeBuildRun(created.data.id)
        return { run: created.data, started: started.data }
      } catch (exc) {
        const error = toApiError(exc)
        throw new StartFailedError(created.data.id, error.message)
      }
    },
    onSuccess: ({ run }) => {
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
      void queryClient.invalidateQueries({ queryKey: ['health', 'deps'] })
      navigate(`/workbench/${run.id}`)
    },
    onError: (exc) => {
      // The run exists and the user can start it from the workbench — send them
      // there and say why, rather than leaving a silent DRAFT behind.
      if (exc instanceof StartFailedError) {
        setNotice(`任务已创建，但没能开始：${exc.message}。到工作台点「开始」可以重试。`)
        navigate(`/workbench/${exc.buildRunId}`)
        return
      }
      setNotice(toApiError(exc).message)
    },
  })

  const baseCount = knowledge.data?.length ?? 0
  const llm = health.data?.ai?.llm
  const blockedReason = llm?.degraded
    ? llm.reason || '还没有配置可用的模型，它现在做不了需要推理的活。'
    : null

  return (
    <Card className="border-accent/25">
      <CardBody className="space-y-3">
        <div className="flex items-center gap-2">
          <Sparkles className="size-4 text-accent" />
          <h2 className="text-[15px] font-medium text-text">你想让我完成什么？</h2>
        </div>

        {/* The gate is stated before the input, not after the click, because
            discovering it by being refused is how the user learns the product
            does not work — which is the wrong lesson. */}
        {blockedReason && <ModelSetupNotice reason={blockedReason} />}

        {notice && <InlineAlert tone="danger" title="没能开始">{notice}</InlineAlert>}

        <textarea
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey) && goal.trim().length >= 4) {
              if (blockedReason) return
              create.mutate()
            }
          }}
          rows={2}
          data-testid="goal-input"
          disabled={Boolean(blockedReason)}
          placeholder={
            blockedReason
              ? '先配置模型，这里就能用了'
              : '例如：用我的毕业设计资料做一份 15 分钟的答辩 PPT'
          }
          className="w-full resize-none rounded-xl border border-border bg-inset px-3.5 py-3 text-[14px] text-text outline-none transition-colors placeholder:text-faint focus:border-accent/60 disabled:opacity-60"
        />

        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              disabled={Boolean(blockedReason)}
              onClick={() => setGoal(example)}
              className="rounded-full border border-border px-2.5 py-1 text-[11.5px] text-muted transition-colors hover:border-accent/40 hover:text-text disabled:opacity-50"
            >
              {example}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 pt-1">
          <div className="flex items-center gap-2">
            <span className="text-[12px] text-faint">用哪些资料</span>
            <Badge tone={baseCount > 0 ? 'info' : 'warn'}>
              {baseCount > 0 ? `${baseCount} 个知识库` : '还没有资料'}
            </Badge>
            <Link to="/knowledge" className="text-[11.5px] text-muted underline-offset-2 hover:text-text hover:underline">
              管理
            </Link>
          </div>

          <div className="flex items-center gap-1.5">
            <span className="text-[12px] text-faint">产出格式</span>
            {FORMATS.map((format) => (
              <button
                key={format.value}
                type="button"
                data-testid={`format-${format.value}`}
                disabled={Boolean(blockedReason)}
                onClick={() => setKind(format.value)}
                className={cn(
                  'rounded-lg border px-2.5 py-1 text-[11.5px] transition-colors disabled:opacity-50',
                  kind === format.value
                    ? 'border-accent/60 bg-accent/10 text-text'
                    : 'border-border text-muted hover:text-text',
                )}
              >
                {format.label}
              </button>
            ))}
          </div>

          <Button
            variant="primary"
            size="sm"
            className="ml-auto"
            data-testid="start-build"
            disabled={goal.trim().length < 4 || create.isPending || Boolean(blockedReason)}
            onClick={() => create.mutate()}
          >
            {create.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <ArrowRight className="size-3.5" />}
            {blockedReason ? '先配置模型' : '开始'}
          </Button>
        </div>
      </CardBody>
    </Card>
  )
}

/* -------------------------------------------------------------- ② active -- */

function ActiveBuilds({ runs }: { runs: BuildRun[] }) {
  if (runs.length === 0) {
    return (
      <Card>
        <CardHeader title="正在处理" />
        <CardBody>
          <EmptyState
            icon={<Sparkles className="size-5" />}
            title="现在没有在跑的任务"
            description="在上面说一句你想完成什么，它就会开始规划。"
            compact
          />
        </CardBody>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader title="正在处理" actions={<Badge tone="accent">{runs.length}</Badge>} />
      <CardBody className="divide-y divide-border/50 p-0">
        {runs.map((run) => {
          const percent = Math.round(run.progress * 100)
          // A DRAFT run is not being processed, it is waiting. Saying so here means
          // the user finds out on the home screen rather than after opening the run
          // and staring at three empty panels.
          const waiting = run.status === 'DRAFT' || run.status === 'READY'
          return (
            <Link
              key={run.id}
              to={`/workbench/${run.id}`}
              className="block px-4 py-3 transition-colors hover:bg-inset/50"
            >
              <div className="flex items-center gap-3">
                <span className="min-w-0 flex-1 truncate text-[13.5px] text-text">{run.title}</span>
                <Badge tone={STATUS_TONES[run.status] as never}>
                  {STATUS_LABELS[run.status]}
                  {run.status === 'EXECUTING' && run.steps_total > 0 && ` ${percent}%`}
                </Badge>
              </div>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-inset">
                <div
                  className={cn('h-full rounded-full transition-all', waiting ? 'bg-warn/60' : 'bg-accent')}
                  style={{ width: `${percent}%` }}
                />
              </div>
              <p className="mt-1.5 text-[11.5px] text-faint">
                {waiting
                  ? '还没开始 —— 打开它点一下「开始」'
                  : `${run.steps_done}/${run.steps_total || '—'} 步`}{' '}
                · {KIND_LABELS[run.deliverable_kind] ?? run.deliverable_kind}
                {run.started_at && ` · 开始于 ${formatWhen(run.started_at)}`}
              </p>
            </Link>
          )
        })}
      </CardBody>
    </Card>
  )
}

/* --------------------------------------------------------- ③ deliverables -- */

function RecentDeliverables({ items }: { items: Awaited<ReturnType<typeof deliveryApi.deliverables>>['deliverables'] }) {
  if (items.length === 0) {
    return (
      <Card>
        <CardHeader title="最近产出" />
        <CardBody>
          <EmptyState
            icon={<Package className="size-5" />}
            title="还没有产出"
            description="完成一次任务后，它做出来的文件会出现在这里，并且等你验收。"
            compact
          />
        </CardBody>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader title="最近产出" actions={<Badge tone="neutral">{items.length}</Badge>} />
      <CardBody className="grid gap-2 p-3 sm:grid-cols-2 xl:grid-cols-3">
        {items.slice(0, 6).map((item) => {
          const state = acceptanceLabel(item)
          return (
            <Link
              key={item.id}
              to={item.build_run_id ? `/workbench/${item.build_run_id}` : '/workbench'}
              className="rounded-xl border border-border bg-inset/40 p-3 transition-all hover:border-border-strong"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 truncate text-[13px] text-text" title={item.title}>
                  {item.title}
                  {item.version > 1 && <span className="text-faint"> v{item.version}</span>}
                </span>
                <Badge tone={state.tone}>{state.text}</Badge>
              </div>
              <p className="mt-1.5 text-[11.5px] text-faint">
                {KIND_LABELS[item.kind] ?? item.kind} · {formatSize(item.size_bytes)}
                {item.verified && ' · 已机器校验'}
              </p>
              {item.feedback && (
                <p className="mt-1.5 line-clamp-2 text-[11.5px] text-danger/80">「{item.feedback}」</p>
              )}
              <p className="mt-1 text-[11px] text-faint/70">{formatWhen(item.created_at)}</p>
            </Link>
          )
        })}
      </CardBody>
    </Card>
  )
}

/* -------------------------------------------------------------- ④ learned -- */

function Learned({ pending, active }: { pending: number; active: { content: string }[] }) {
  const queryClient = useQueryClient()
  const candidates = useQuery({
    queryKey: ['context', 'candidates'],
    queryFn: () => contextApi.candidates('pending'),
  })
  const [notice, setNotice] = useState<string | null>(null)

  const approve = useMutation({
    mutationFn: (id: string) => contextApi.approveCandidate(id),
    onSuccess: () => {
      setNotice('已记住。下一次产出会不一样。')
      void queryClient.invalidateQueries({ queryKey: ['context'] })
    },
  })
  const reject = useMutation({
    mutationFn: (id: string) => contextApi.rejectCandidate(id),
    onSuccess: () => {
      setNotice('已忽略。')
      void queryClient.invalidateQueries({ queryKey: ['context'] })
    },
  })

  const rows = candidates.data?.candidates ?? []

  return (
    <Card className="border-accent/25">
      <CardHeader
        title="它学到了什么"
        description="每次你验收或退回，它都会重新理解你的标准"
        actions={
          <Link to="/me" className="text-[11.5px] text-muted underline-offset-2 hover:text-text hover:underline">
            管理记忆 →
          </Link>
        }
      />
      <CardBody className="space-y-3">
        {notice && <InlineAlert tone="success" title="已更新">{notice}</InlineAlert>}

        {rows.length > 0 ? (
          <div>
            <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">
              待你确认（{pending || rows.length}）
            </p>
            <div className="space-y-1.5">
              {rows.slice(0, 4).map((candidate) => (
                <div
                  key={candidate.id}
                  data-testid="learned-candidate"
                  className="flex items-center gap-2 rounded-lg border border-border bg-inset/40 px-3 py-2"
                >
                  <span className="min-w-0 flex-1 text-[12.5px] text-text">{candidate.content}</span>
                  <Button size="xs" variant="primary" onClick={() => approve.mutate(candidate.id)}>
                    <Check className="size-3" /> 确认
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => reject.mutate(candidate.id)}>
                    <X className="size-3" />
                  </Button>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <p className="text-[12.5px] text-faint">
            还没有待确认的内容。退回一次产出并说明原因，它就会从那里学。
          </p>
        )}

        {active.length > 0 && (
          <div>
            <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">
              已生效（{active.length}）
            </p>
            <div className="flex flex-wrap gap-1.5">
              {active.slice(0, 8).map((memory) => (
                <span
                  key={memory.content}
                  className="max-w-full truncate rounded-full border border-border px-2.5 py-0.5 text-[11.5px] text-muted"
                  title={memory.content}
                >
                  {memory.content}
                </span>
              ))}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

/* ------------------------------------------------------------------ page -- */

export default function HomePage() {
  const runs = useQuery({
    queryKey: ['delivery', 'build-runs', 'active'],
    queryFn: () => deliveryApi.buildRuns({ active_only: true, limit: 10 }),
  })
  const deliverables = useQuery({
    queryKey: ['delivery', 'deliverables', 'recent'],
    queryFn: () => deliveryApi.deliverables({ limit: 12 }),
  })
  const memories = useQuery({
    queryKey: ['context', 'memories', 'active'],
    queryFn: () => contextApi.memories({ status: 'active' }),
  })
  const stats = useQuery({ queryKey: ['context', 'stats'], queryFn: () => contextApi.contextStats() })

  const activeMemories = useMemo(
    () => (memories.data?.memories ?? []).map((memory) => ({ content: memory.content })),
    [memories.data],
  )

  if (runs.isError) {
    return (
      <PageContainer>
        <ErrorState title="无法加载首页" error={runs.error} onRetry={() => void runs.refetch()} />
      </PageContainer>
    )
  }

  return (
    <PageContainer className="space-y-4">
      <GoalComposer />

      {runs.isLoading ? (
        <LoadingBlock label="读取进行中的任务…" />
      ) : (
        <ActiveBuilds runs={runs.data?.build_runs ?? []} />
      )}

      {deliverables.isLoading ? (
        <LoadingBlock label="读取最近产出…" />
      ) : (
        <RecentDeliverables items={deliverables.data?.deliverables ?? []} />
      )}

      <Learned pending={stats.data?.candidates_pending ?? 0} active={activeMemories} />

      <p className="flex items-center justify-center gap-1.5 pb-2 text-[11.5px] text-faint/70">
        <TrendingUp className="size-3" />
        交付 {stats.data ? stats.data.context_snapshots : 0} 次上下文注入 ·{' '}
        <FileText className="size-3" />
        {stats.data?.memories_active ?? 0} 条已生效的记忆
      </p>
    </PageContainer>
  )
}
