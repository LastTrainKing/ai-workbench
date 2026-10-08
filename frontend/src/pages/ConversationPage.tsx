/**
 * The conversation. This is the home screen, and the only screen most people need.
 *
 *     "我点开这个软件，就是要和我的数字员工对话，就跟一个老板加了员工微信一样，
 *      他能对话，能做好文件发我。"
 *
 * The shape is a chat: what you said, what it said back, what it did about it, what came
 * out — on one timeline, with the input pinned to the bottom.
 *
 * **Three outcomes, not two.** Saying something comes back as one of:
 *
 *   - it answered          — a bubble, and nothing was built because nothing needed to be
 *   - it asked             — one to three questions, each with what it will do if you say
 *                            nothing, plus a button for "don't ask, just do it"
 *   - it started work      — the run, its steps, and its files, on the same timeline
 *
 * The middle one is the reason this round exists. Before it, the only thing a message
 * could do was start work, so an underspecified request produced a confidently wrong
 * artifact instead of one question.
 *
 * What stays folded: 执行计划 / 实时执行 / 本次上下文 / 命中层级 live behind 「详情」,
 * closed by default. They are for the person who wants to know how it thinks. Nothing was
 * removed to achieve that — every panel is one click away.
 *
 * What must NOT move: the acceptance loop. 接受 / 退回 are on the file, in the
 * conversation, exactly where the file is. A rejection requires a reason and the reason
 * becomes a memory — that is the mechanism the product exists for, and putting it behind a
 * click would be the one change that made this a regression.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUp,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  KeyRound,
  Loader2,
  MessageSquare,
  Sparkles,
} from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, InlineAlert, LoadingBlock } from '@/components/ui/feedback'
import { Badge } from '@/components/ui/primitives'
import { DeliverableCard } from '@/features/delivery/DeliverableCard'
import { RunDetailsFold, type PlanStep } from '@/features/delivery/RunDetails'
import { conversationApi, type ConversationMessage } from '@/lib/conversationApi'
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

/* ------------------------------------------------------------------ bubbles -- */

function UserBubble({ message }: { message: ConversationMessage }) {
  return (
    <div className="flex justify-end" data-testid="message-user">
      <div className="max-w-[85%] rounded-2xl rounded-br-md border border-accent/25 bg-accent-soft/50 px-3.5 py-2.5">
        <p className="whitespace-pre-wrap text-[13.5px] text-text">{message.content}</p>
      </div>
    </div>
  )
}

function AssistantBubble({ message }: { message: ConversationMessage }) {
  if (!message.content) return null
  return (
    <p
      className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-text"
      data-testid="message-assistant"
    >
      {message.content}
    </p>
  )
}

/**
 * It asked. Answer in a sentence, or tell it to stop asking.
 *
 * The default under each question is the whole point of this card. The user's rule:
 * 「问要带默认值，让用户否定比让他从零选择快」. A question whose default is empty says
 * so rather than inventing one — it has no recommendation for that one, and pretending
 * otherwise would be worse than admitting it.
 */
function ClarifyCard({
  message,
  onConfirm,
  pending,
}: {
  message: ConversationMessage
  onConfirm: () => void
  pending: boolean
}) {
  return (
    <div
      className="rounded-xl border border-accent/30 bg-accent-soft/30 p-3.5"
      data-testid="clarify-questions"
    >
      <ol className="space-y-2.5">
        {message.questions.map((question, index) => (
          <li key={`${question.question}-${index}`} data-testid="clarify-question">
            <p className="text-[13px] text-text">
              <span className="mr-1.5 text-faint">{index + 1}.</span>
              {question.question}
            </p>
            <p className="mt-0.5 pl-4 text-[12px] text-muted">
              {question.default ? (
                <>
                  你不确定的话，我按「{question.default}」来做
                </>
              ) : (
                <span className="text-warn">这一条我没主意，你定</span>
              )}
            </p>
          </li>
        ))}
      </ol>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          data-testid="confirm-start"
          disabled={pending}
          onClick={onConfirm}
        >
          {pending ? <Loader2 className="size-3.5 animate-spin" /> : null}
          就按你说的做
        </Button>
        <span className="text-[11.5px] text-faint">或者直接在下面回我一句</span>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- one turn -- */

/**
 * What it did about it, and what came out.
 *
 * Kept as its own component because a `start` message is the only one that needs a run,
 * and a run is polled while it is active — a message that answered a question should not
 * start a timer.
 */
function RunTurn({
  run,
  deliverables,
  blockedReason,
  decidedForYou,
  retrieval,
}: {
  run: BuildRun
  deliverables: Deliverable[]
  blockedReason: string | null
  decidedForYou: string[]
  retrieval: ConversationMessage['retrieval']
}) {
  const queryClient = useQueryClient()
  const sentence = runSentence(run)
  const active = ['PLANNING', 'READY', 'EXECUTING', 'VERIFYING'].includes(run.status)
  const [notice, setNotice] = useState<string | null>(null)

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
    <div className="space-y-2" data-testid="conversation-turn">
      <p className={cn('text-[13px]', toneClass)} data-testid="turn-status">
        {sentence.text}
        {active && run.steps_total > 0 && (
          <span className="ml-2 text-[11.5px] text-faint">
            {KIND_LABELS[run.deliverable_kind] ?? run.deliverable_kind}
          </span>
        )}
      </p>

      {/* What it decided without being told. An assumption the user cannot see is one
          they cannot correct. */}
      {decidedForYou.length > 0 && (
        <p className="text-[12px] text-faint" data-testid="decided-for-you">
          这几处我自己定了：{decidedForYou.join('；')}
        </p>
      )}

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

      {/* A run that was created but never started says so, and offers the one action that
          resolves it. Its absence is how a run sat in DRAFT for a whole session while the
          screen looked calm. */}
      {(run.status === 'DRAFT' || run.status === 'READY') && (
        <div
          className="rounded-xl border border-warn/40 bg-warn-soft/40 p-3"
          data-testid="not-started"
        >
          <p className="text-[12.5px] text-text">这件事记下来了，但还没有交给它做。</p>
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
        <InlineAlert tone="danger" title="运行出错">
          {run.error}
        </InlineAlert>
      )}

      <RunDetailsFold run={run} steps={steps} retrieval={retrieval} />
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

function Composer({
  blockedReason,
  onSend,
  pending,
  notice,
}: {
  blockedReason: string | null
  onSend: (text: string) => void
  pending: boolean
  notice: string | null
}) {
  const [text, setText] = useState('')
  const [kind, setKind] = useState<DeliverableKind>('pptx')
  const [showOptions, setShowOptions] = useState(false)

  const knowledge = useQuery({
    queryKey: ['knowledge', 'bases'],
    queryFn: async () => {
      const result = await api.knowledgeBases.list()
      return result.items ?? []
    },
    retry: false,
  })

  const baseCount = knowledge.data?.length ?? 0
  const canSend = text.trim().length >= 1 && !pending && !blockedReason

  const send = () => {
    if (!canSend) return
    onSend(text.trim())
    setText('')
  }

  return (
    <div className="sticky bottom-0 z-10 border-t border-subtle bg-base/95 px-4 pb-4 pt-3 backdrop-blur">
      <div className="mx-auto max-w-3xl space-y-2">
        {notice && (
          <InlineAlert tone="danger" title="没能开始">
            {notice}
          </InlineAlert>
        )}

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
            value={text}
            onChange={(event) => setText(event.target.value)}
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
              blockedReason ? '先配置模型，这里就能用了' : '说点什么…（回车发送，Shift+回车换行）'
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
            {pending ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
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
                onClick={() => setText(example)}
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
  const { id } = useParams<{ id?: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)

  const health = useQuery({
    queryKey: ['health', 'deps'],
    queryFn: ({ signal }) => api.health.deps(signal),
    retry: false,
  })

  const conversation = useQuery({
    queryKey: ['conversation', id],
    queryFn: () => conversationApi.get(id!),
    enabled: Boolean(id),
    // Only while something is outstanding: an idle conversation has nothing to poll for.
    refetchInterval: (query) => {
      const messages = query.state.data?.messages ?? []
      const working = messages.some((m) => m.kind === 'start' && m.waiting === false)
      return working ? 5000 : false
    },
  })

  // Runs are read from the list rather than one request per turn, and only to render the
  // sentence and the steps for a `start` message.
  const runs = useQuery({
    queryKey: ['delivery', 'build-runs', 'conversation'],
    queryFn: () => deliveryApi.buildRuns({ limit: 50 }),
    refetchInterval: 5000,
  })

  const stats = useQuery({ queryKey: ['context', 'stats'], queryFn: () => contextApi.contextStats() })

  const llm = health.data?.ai?.llm
  const blockedReason = llm?.degraded
    ? llm.reason || '还没有配置可用的模型，它现在做不了需要推理的活。'
    : null

  const byRun = useMemo(() => {
    const grouped = new Map<string, BuildRun>()
    for (const run of runs.data?.build_runs ?? []) grouped.set(run.id, run)
    return grouped
  }, [runs.data])

  const deliverablesByRun = useMemo(() => {
    const grouped = new Map<string, Deliverable[]>()
    for (const item of conversation.data?.deliverables ?? []) {
      if (!item.build_run_id) continue
      grouped.set(item.build_run_id, [...(grouped.get(item.build_run_id) ?? []), item])
    }
    return grouped
  }, [conversation.data])

  const send = useMutation({
    mutationFn: async ({ conversationId, text }: { conversationId: string; text: string }) => {
      const result = await conversationApi.send(conversationId, text)
      return { conversationId, turn: result.data }
    },
    onSuccess: ({ conversationId, turn }) => {
      setNotice(null)
      void queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
      if (turn.outcome === 'start') setNotice(null)
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const confirm = useMutation({
    mutationFn: (conversationId: string) => conversationApi.confirm(conversationId),
    onSuccess: () => {
      setNotice(null)
      void queryClient.invalidateQueries({ queryKey: ['conversation', id] })
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  /** Sending from `/` starts the conversation first — one user action, not two. */
  const handleSend = async (text: string) => {
    if (id) {
      send.mutate({ conversationId: id, text })
      return
    }
    try {
      const created = await conversationApi.create()
      void queryClient.invalidateQueries({ queryKey: ['conversations'] })
      navigate(`/c/${created.data.id}`, { replace: true })
      send.mutate({ conversationId: created.data.id, text })
    } catch (exc) {
      setNotice(toApiError(exc).message)
    }
  }

  const messages = conversation.data?.messages ?? []
  const pending = stats.data?.candidates_pending ?? 0

  // Land at the bottom, where the newest thing is.
  const bottom = useRef<HTMLDivElement>(null)
  const landed = useRef(0)
  useEffect(() => {
    if (messages.length === landed.current) return
    landed.current = messages.length
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [messages.length])

  if (id && conversation.isError) {
    return (
      <div className="p-4">
        <ErrorState
          title="无法加载"
          error={conversation.error}
          onRetry={() => void conversation.refetch()}
        />
      </div>
    )
  }

  return (
    <div className="flex min-h-full flex-col">
      <div className="mx-auto w-full max-w-3xl flex-1 space-y-6 px-4 py-5" data-testid="message-stream">
        {blockedReason && <ModelSetupNotice reason={blockedReason} />}

        {/* The learning loop, as a line rather than a card. It is real and it is the point
            of the product, but it is not what someone opens this screen to do. */}
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

        {id && conversation.isLoading ? (
          <LoadingBlock label="读取对话…" />
        ) : messages.length === 0 ? (
          <div className="py-10">
            <EmptyState
              icon={<MessageSquare className="size-5" />}
              title="跟它说一件你想完成的事"
              description="一句话就行。不清楚的地方它会先问你，问明白了再动手；做完把文件放在这条对话里，等你说行不行。"
            />
          </div>
        ) : (
          messages.map((message) => (
            <div key={message.id} className="space-y-2">
              {message.role === 'user' ? (
                <UserBubble message={message} />
              ) : (
                <div className="flex gap-2.5">
                  <span className="mt-1 flex size-6 shrink-0 items-center justify-center rounded-full bg-elevated">
                    <Sparkles className="size-3.5 text-muted" />
                  </span>
                  <div className="min-w-0 flex-1 space-y-2">
                    <AssistantBubble message={message} />

                    {message.kind === 'clarify' && (
                      <ClarifyCard
                        message={message}
                        pending={confirm.isPending}
                        onConfirm={() => confirm.mutate(message.conversation_id)}
                      />
                    )}

                    {message.kind === 'start' &&
                      (message.build_run_id && byRun.get(message.build_run_id) ? (
                        <RunTurn
                          run={byRun.get(message.build_run_id)!}
                          deliverables={deliverablesByRun.get(message.build_run_id) ?? []}
                          blockedReason={blockedReason}
                          decidedForYou={message.decided_for_you}
                          retrieval={message.retrieval}
                        />
                      ) : (
                        <p className="text-[12.5px] text-faint">正在开始…</p>
                      ))}
                  </div>
                </div>
              )}
            </div>
          ))
        )}

        <div ref={bottom} />
      </div>

      <Composer
        blockedReason={blockedReason}
        onSend={(text) => void handleSend(text)}
        pending={send.isPending}
        notice={notice}
      />
    </div>
  )
}
