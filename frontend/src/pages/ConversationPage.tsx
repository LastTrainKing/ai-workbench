/**
 * The conversation. This is the home screen, and the only screen most people need.
 *
 * The shape is a chat: what you said, what it did about it, what came out — on one
 * timeline, with the input pinned to the bottom. The previous home was four stacked
 * cards of panels and badges, and the user's verdict on it was that a person
 * opening it could not tell where to type. Everything that was on those cards is
 * still here; it is just arranged as a conversation rather than as a dashboard.
 *
 * **A turn is a build run.** There is no separate conversation table and there does
 * not need to be one: the user said something, the run is what happened next, and
 * the deliverables are what came out. Inventing a parallel store for "messages"
 * would mean two records of the same event that could disagree.
 *
 * What stays folded: 执行计划 / 实时执行 / 本次上下文 live behind 「详情」, closed by
 * default. They are for the person who wants to know how it thinks. Nothing was
 * removed to achieve that — every panel is one click away, still polling, still
 * showing the real event stream.
 *
 * What must NOT move: the acceptance loop. 接受 / 退回 are on the file, in the
 * conversation, exactly where the file is. A rejection requires a reason and the
 * reason becomes a memory — that is the mechanism the product exists for, and
 * putting it behind a click would be the one change that made this redesign a
 * regression.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUp,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  KeyRound,
  Loader2,
  Sparkles,
} from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, InlineAlert, LoadingBlock } from '@/components/ui/feedback'
import { Badge } from '@/components/ui/primitives'
import { DeliverableCard } from '@/features/delivery/DeliverableCard'
import { RunDetailsFold, type PlanStep } from '@/features/delivery/RunDetails'
import {
  deliveryApi,
  KIND_LABELS,
  runSentence,
  type BuildRun,
  type Deliverable,
  type DeliverableKind,
} from '@/lib/deliveryApi'
import { contextApi } from '@/lib/contextApi'
import { api, toApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

/* --------------------------------------------------------------- the gate -- */

/**
 * Shown before the input, once, in a size that cannot be missed.
 *
 * The rule for when it appears is `ai.llm.degraded` and nothing else. Embeddings
 * falling back to the offline engine is deliberate — DeepSeek has no embedding
 * endpoint — so treating that as "unconfigured" would block a machine that works.
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

/* ---------------------------------------------------------------- one turn -- */

function Turn({
  run,
  deliverables,
  blockedReason,
}: {
  run: BuildRun
  deliverables: Deliverable[]
  blockedReason: string | null
}) {
  const queryClient = useQueryClient()
  const sentence = runSentence(run)
  const active = ['PLANNING', 'READY', 'EXECUTING', 'VERIFYING'].includes(run.status)
  const [notice, setNotice] = useState<string | null>(null)

  // Only active runs need their plan, and there are rarely more than one or two.
  const detail = useQuery({
    queryKey: ['delivery', 'build-run', run.id],
    queryFn: () => deliveryApi.buildRun(run.id),
    enabled: active,
    refetchInterval: active ? 4000 : false,
  })

  const steps: PlanStep[] =
    ((detail.data?.plan ?? run.plan) as { steps?: PlanStep[] } | undefined)?.steps ?? []

  const start = useMutation({
    mutationFn: () => deliveryApi.executeBuildRun(run.id),
    onSuccess: () => {
      setNotice(null)
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const toneClass =
    sentence.tone === 'done'
      ? 'text-success'
      : sentence.tone === 'trouble'
        ? 'text-danger'
        : sentence.tone === 'doing'
          ? 'text-accent'
          : 'text-warn'

  return (
    <div className="space-y-2.5" data-testid="conversation-turn">
      {/* What the user said. */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md border border-accent/25 bg-accent-soft/50 px-3.5 py-2.5">
          <p className="whitespace-pre-wrap text-[13.5px] text-text">{run.goal}</p>
        </div>
      </div>

      {/* What it did about it. */}
      <div className="flex gap-2.5">
        <span className="mt-1 flex size-6 shrink-0 items-center justify-center rounded-full bg-elevated">
          {active ? (
            <Loader2 className="size-3.5 animate-spin text-accent" />
          ) : (
            <Sparkles className="size-3.5 text-muted" />
          )}
        </span>

        <div className="min-w-0 flex-1 space-y-2">
          <p className={cn('text-[13px]', toneClass)} data-testid="turn-status">
            {sentence.text}
            {active && run.steps_total > 0 && (
              <span className="ml-2 text-[11.5px] text-faint">
                {KIND_LABELS[run.deliverable_kind] ?? run.deliverable_kind}
              </span>
            )}
          </p>

          {/* While it is working, the steps are worth seeing without opening
              anything — that is the difference between "it is doing something" and
              "it is stuck". They are a compact list, not a panel. */}
          {run.status === 'EXECUTING' && steps.length > 0 && (
            <ol className="space-y-1">
              {steps.slice(0, 6).map((step, index) => (
                <li key={step.id ?? index} className="flex items-start gap-2 text-[12px]">
                  {index < run.steps_done ? (
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
                  ) : index === run.steps_done ? (
                    <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-accent" />
                  ) : (
                    <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-faint" />
                  )}
                  <span className={index < run.steps_done ? 'text-faint' : 'text-muted'}>
                    {step.description}
                  </span>
                </li>
              ))}
            </ol>
          )}

          {/* A run that was created but never started says so, and offers the one
              action that resolves it. Its absence is how a run sat in DRAFT for a
              whole session while the screen looked calm. */}
          {(run.status === 'DRAFT' || run.status === 'READY') && (
            <div
              className="rounded-xl border border-warn/40 bg-warn-soft/40 p-3"
              data-testid="not-started"
            >
              <p className="text-[12.5px] text-text">
                这件事记下来了，但还没有交给它做。
              </p>
              <Button
                size="sm"
                variant="primary"
                className="mt-2"
                data-testid="start-run"
                disabled={start.isPending || Boolean(blockedReason)}
                onClick={() => start.mutate()}
              >
                {start.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <ArrowUp className="size-3.5" />
                )}
                {blockedReason ? '先配置模型' : '开始'}
              </Button>
              {notice && (
                <p className="mt-2 text-[12px] text-danger" data-testid="start-notice">
                  {notice}
                </p>
              )}
            </div>
          )}

          {/* What came out, where it came out. */}
          {deliverables.map((item) => (
            <DeliverableCard key={item.id} item={item} compact />
          ))}

          {run.error && run.status !== 'FAILED' && (
            <InlineAlert tone="danger" title="运行出错">{run.error}</InlineAlert>
          )}

          <RunDetailsFold run={run} steps={steps} />
        </div>
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- composer -- */

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
 * A distinct failure on purpose. "It could not be created" and "it was created but
 * could not start" need different words and different recovery, and collapsing them
 * into one message is how the original DRAFT-forever bug stayed invisible.
 */
class StartFailedError extends Error {
  readonly buildRunId: string
  constructor(buildRunId: string, message: string) {
    super(message)
    this.name = 'StartFailedError'
    this.buildRunId = buildRunId
  }
}

function Composer({ blockedReason }: { blockedReason: string | null }) {
  const queryClient = useQueryClient()
  const [goal, setGoal] = useState('')
  const [kind, setKind] = useState<DeliverableKind>('pptx')
  const [notice, setNotice] = useState<string | null>(null)
  const [showOptions, setShowOptions] = useState(false)

  const knowledge = useQuery({
    queryKey: ['knowledge', 'bases'],
    queryFn: async () => {
      const result = await api.knowledgeBases.list()
      return result.items ?? []
    },
    retry: false,
  })

  const create = useMutation({
    mutationFn: async () => {
      const created = await deliveryApi.createBuildRun({
        goal: goal.trim(),
        deliverable_kind: kind,
        knowledge_base_ids: (knowledge.data ?? []).map((kb: { id: string }) => kb.id),
      })
      // Create and start are one action from the user's point of view. The two
      // steps stay visibly separate so a start failure is a start failure — it can
      // never be reported as "nothing happened".
      try {
        const started = await deliveryApi.executeBuildRun(created.data.id)
        return { run: created.data, started: started.data }
      } catch (exc) {
        throw new StartFailedError(created.data.id, toApiError(exc).message)
      }
    },
    onSuccess: () => {
      setGoal('')
      setNotice(null)
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
    },
    onError: (exc) => {
      if (exc instanceof StartFailedError) {
        setNotice(`任务已记下来了，但没能开始：${exc.message}。上面那条可以点「开始」重试。`)
        void queryClient.invalidateQueries({ queryKey: ['delivery'] })
        return
      }
      setNotice(toApiError(exc).message)
    },
  })

  const baseCount = knowledge.data?.length ?? 0
  const canSend = goal.trim().length >= 4 && !create.isPending && !blockedReason

  const send = () => {
    if (!canSend) return
    create.mutate()
  }

  return (
    <div className="sticky bottom-0 z-10 border-t border-subtle bg-base/95 px-4 pb-4 pt-3 backdrop-blur">
      <div className="mx-auto max-w-3xl space-y-2">
        {notice && <InlineAlert tone="danger" title="没能开始">{notice}</InlineAlert>}

        {showOptions && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-border bg-surface px-3 py-2">
            <div className="flex items-center gap-1.5">
              <span className="text-[11.5px] text-faint">产出格式</span>
              {FORMATS.map((format) => (
                <button
                  key={format.value}
                  type="button"
                  data-testid={`format-${format.value}`}
                  disabled={Boolean(blockedReason)}
                  onClick={() => setKind(format.value)}
                  className={cn(
                    'rounded-lg border px-2 py-0.5 text-[11.5px] transition-colors disabled:opacity-50',
                    kind === format.value
                      ? 'border-accent/60 bg-accent/10 text-text'
                      : 'border-border text-muted hover:text-text',
                  )}
                >
                  {format.label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[11.5px] text-faint">用哪些资料</span>
              <Badge tone={baseCount > 0 ? 'info' : 'warn'}>
                {baseCount > 0 ? `${baseCount} 个知识库` : '还没有资料'}
              </Badge>
              <Link
                to="/knowledge"
                className="text-[11.5px] text-muted underline-offset-2 hover:text-text hover:underline"
              >
                管理
              </Link>
            </div>
          </div>
        )}

        <div className="flex items-end gap-2">
          <textarea
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                send()
              }
            }}
            rows={1}
            data-testid="goal-input"
            disabled={Boolean(blockedReason)}
            placeholder={
              blockedReason ? '先配置模型，这里就能用了' : '告诉它你想做什么…（回车发送，Shift+回车换行）'
            }
            className="max-h-40 min-h-[46px] w-full resize-none rounded-2xl border border-border bg-inset px-4 py-3 text-[14px] text-text outline-none transition-colors placeholder:text-faint focus:border-accent/60 disabled:opacity-60"
          />
          <Button
            variant="primary"
            size="md"
            data-testid="start-build"
            disabled={!canSend}
            onClick={send}
            className="shrink-0"
          >
            {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
            {blockedReason ? '先配置模型' : '发送'}
          </Button>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setShowOptions((v) => !v)}
            className="text-[11.5px] text-faint underline-offset-2 hover:text-muted hover:underline"
            data-testid="toggle-options"
          >
            {showOptions ? '收起选项' : `选项 · ${KIND_LABELS[kind] ?? kind}`}
          </button>
          {!showOptions &&
            EXAMPLES.slice(0, 2).map((example) => (
              <button
                key={example}
                type="button"
                disabled={Boolean(blockedReason)}
                onClick={() => setGoal(example)}
                className="rounded-full border border-border px-2.5 py-0.5 text-[11px] text-muted transition-colors hover:border-accent/40 hover:text-text disabled:opacity-50"
              >
                {example}
              </button>
            ))}
        </div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------- page -- */

export default function ConversationPage() {
  const health = useQuery({
    queryKey: ['health', 'deps'],
    queryFn: ({ signal }) => api.health.deps(signal),
    retry: false,
  })
  const runs = useQuery({
    queryKey: ['delivery', 'build-runs', 'conversation'],
    queryFn: () => deliveryApi.buildRuns({ limit: 20 }),
    refetchInterval: 5000,
  })
  const deliverables = useQuery({
    queryKey: ['delivery', 'deliverables', 'conversation'],
    queryFn: () => deliveryApi.deliverables({ limit: 60 }),
    refetchInterval: 5000,
  })
  const stats = useQuery({ queryKey: ['context', 'stats'], queryFn: () => contextApi.contextStats() })

  const llm = health.data?.ai?.llm
  const blockedReason = llm?.degraded
    ? llm.reason || '还没有配置可用的模型，它现在做不了需要推理的活。'
    : null

  // Deliverables are grouped from one list rather than fetched per run: the list
  // endpoint already carries `build_run_id`, and a request per turn would be a
  // request per turn.
  const byRun = useMemo(() => {
    const grouped = new Map<string, Deliverable[]>()
    for (const item of deliverables.data?.deliverables ?? []) {
      if (!item.build_run_id) continue
      const bucket = grouped.get(item.build_run_id) ?? []
      bucket.push(item)
      grouped.set(item.build_run_id, bucket)
    }
    return grouped
  }, [deliverables.data])

  // Oldest first, newest at the bottom, like every conversation anyone has used.
  const turns = useMemo(
    () => [...(runs.data?.build_runs ?? [])].reverse(),
    [runs.data],
  )

  // Land at the bottom, where the newest turn is.
  const bottom = useRef<HTMLDivElement>(null)
  const landed = useRef(false)
  useEffect(() => {
    if (landed.current || turns.length === 0) return
    landed.current = true
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [turns.length])

  if (runs.isError) {
    return (
      <div className="p-4">
        <ErrorState title="无法加载" error={runs.error} onRetry={() => void runs.refetch()} />
      </div>
    )
  }

  const pending = stats.data?.candidates_pending ?? 0

  return (
    <div className="flex min-h-full flex-col">
      <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 px-4 py-5">
        {blockedReason && <ModelSetupNotice reason={blockedReason} />}

        {/* The learning loop, as a line rather than a card. It is real and it is
            the point of the product, but it is not what someone opens this screen
            to do — so it says it exists and gets out of the way. */}
        {pending > 0 && (
          <Link
            to="/me"
            data-testid="learned-strip"
            className="flex items-center gap-2 rounded-xl border border-accent/25 bg-accent-soft/40 px-3.5 py-2.5 text-[12.5px] text-muted transition-colors hover:text-text"
          >
            <Sparkles className="size-3.5 shrink-0 text-accent" />
            我从你上一次的验收里学到了 {pending} 条，等你确认一下
            <ChevronRight className="ml-auto size-3.5" />
          </Link>
        )}

        {runs.isLoading ? (
          <LoadingBlock label="读取对话…" />
        ) : turns.length === 0 ? (
          <div className="py-10">
            <EmptyState
              icon={<Sparkles className="size-5" />}
              title="跟它说一件你想完成的事"
              description="在下面输入框里写一句话就行。它会去找资料、动手做，做完把文件放在这条对话里，等你说行不行。"
            />
          </div>
        ) : (
          turns.map((run) => (
            <Turn
              key={run.id}
              run={run}
              deliverables={byRun.get(run.id) ?? []}
              blockedReason={blockedReason}
            />
          ))
        )}

        <div ref={bottom} />
      </div>

      <Composer blockedReason={blockedReason} />
    </div>
  )
}
