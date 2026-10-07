/**
 * The workbench. This is "it is doing the work", not a chat window.
 *
 * Three columns and a footer:
 *
 *   plan         the steps, and which have actually finished
 *   execution    what it is doing right now, from the event stream
 *   context      what it was told about you for this run
 *   deliverables the files it produced, with accept and reject
 *
 * The footer is the point. 「接受 / 退回」 is where the loop closes — a rejection
 * with a reason is the only place in the product where the user states what they
 * actually wanted, and it is what the memory evaluator listens to.
 *
 * Verification is shown as a list of checks rather than a tick, because "it says
 * the file is fine" is not something a person should have to take on faith.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Download,
  ExternalLink,
  Loader2,
  Package,
  Play,
  X,
  XCircle,
} from 'lucide-react'

import { Badge, Card, CardBody, CardHeader, PageContainer } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, InlineAlert, LoadingBlock } from '@/components/ui/feedback'
import { deliveryApi, KIND_LABELS, STATUS_LABELS, STATUS_TONES, formatSize, formatWhen } from '@/lib/deliveryApi'
import type { Deliverable } from '@/lib/deliveryApi'
import { toApiError } from '@/lib/api'
import { saveBlob } from '@/lib/download'
import { cn } from '@/lib/utils'
import { contextApi } from '@/lib/contextApi'

/* ---------------------------------------------------------- not started -- */

/**
 * Shown while a run is created but not started.
 *
 * This panel exists because its absence was a real defect. `DRAFT` used to look
 * identical to a run that was thinking — an empty plan, an empty execution panel,
 * an empty context panel — and the user waited for work that had never been
 * asked to begin. The status is now something the screen says out loud.
 */
function StartPanel({ runId, retryable }: { runId: string; retryable: boolean }) {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)

  const start = useMutation({
    mutationFn: () => deliveryApi.executeBuildRun(runId),
    onSuccess: () => {
      setNotice(null)
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
      void queryClient.invalidateQueries({ queryKey: ['health', 'deps'] })
    },
    // Not swallowed. If it will not start, that is the whole message.
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  return (
    <div
      data-testid="not-started"
      className="rounded-xl border border-warn/40 bg-warn-soft/50 p-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-text">
            它还没有开始{retryable ? ' —— 上次没能启动成功' : ''}
          </p>
          <p className="mt-1 text-[12.5px] text-muted">
            这次任务已经记下来了，但还没有交给它做。点「开始」它就会去规划并执行。
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          data-testid="start-run"
          disabled={start.isPending}
          onClick={() => start.mutate()}
        >
          {start.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
          {start.isPending ? '正在开始…' : '开始'}
        </Button>
      </div>

      {notice && (
        <div className="mt-3">
          <InlineAlert tone="danger" title="没能开始">
            {notice}
          </InlineAlert>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------- verification view -- */

function VerificationPanel({ item }: { item: Deliverable }) {
  const result = item.verification
  if (!result || !result.checks?.length) {
    return (
      <p className="text-[12px] text-warn">
        还没有校验记录 —— 生成文件不等于内容正确。
      </p>
    )
  }
  const runState = result.run_state
  return (
    <div className="space-y-2" data-testid="verification-checks">
      {/* Column one: the file itself. This is what decides 已机器校验. */}
      <div className="space-y-1">
        <p className="text-[11px] uppercase tracking-wide text-faint">文件本身</p>
        {result.checks.map((check) => (
          <div key={check.name} className="flex items-start gap-2 text-[12px]">
            {check.ok ? (
              <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
            ) : (
              <XCircle className="mt-0.5 size-3.5 shrink-0 text-danger" />
            )}
            <span className="font-mono text-faint">{check.name}</span>
            <span className="text-muted">{check.detail}</span>
          </div>
        ))}
      </div>

      {/* Column two: the run that produced it. A separate question, so a separate
          block — and it never turns the file's verdict red. */}
      {runState && (
        <div className="space-y-1 border-t border-border/60 pt-2" data-testid="run-state-note">
          <p className="text-[11px] uppercase tracking-wide text-faint">这次运行</p>
          <div className="flex items-start gap-2 text-[12px]">
            {runState.ok ? (
              <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
            ) : (
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warn" />
            )}
            <span className="text-muted">{runState.detail}</span>
          </div>
        </div>
      )}
    </div>
  )
}

/* ----------------------------------------------------------- deliverable -- */

function DeliverableCard({ item }: { item: Deliverable }) {
  const queryClient = useQueryClient()
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  // Carries a tone as well as text: "已验收" and "下载失败：文件已不在磁盘上" must not
  // look the same, and a download that silently does nothing is the defect this
  // whole path was rewritten to remove.
  const [notice, setNotice] = useState<{ text: string; tone: 'info' | 'danger' } | null>(null)

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['delivery'] })
    void queryClient.invalidateQueries({ queryKey: ['context'] })
  }

  const accept = useMutation({
    mutationFn: () => deliveryApi.accept(item.id),
    onSuccess: () => {
      setNotice({ text: '已验收。它会记住这个标准。', tone: 'info' })
      invalidate()
    },
    onError: (exc) => setNotice({ text: toApiError(exc).message, tone: 'danger' }),
  })
  const reject = useMutation({
    mutationFn: () => deliveryApi.reject(item.id, reason.trim()),
    onSuccess: () => {
      setNotice({ text: '已退回。这个原因会变成一条待确认的偏好。', tone: 'info' })
      setRejecting(false)
      setReason('')
      invalidate()
    },
    onError: (exc) => setNotice({ text: toApiError(exc).message, tone: 'danger' }),
  })

  // Fetch the bytes with the token, then hand them to the browser. A plain
  // `<a href>` cannot do this — a navigation sends no Authorization header, which
  // is why the button used to do nothing at all.
  const download = useMutation({
    mutationFn: () => deliveryApi.downloadDeliverable(item.id),
    onSuccess: ({ blob, filename }) => {
      setNotice(null)
      saveBlob(blob, filename)
    },
    onError: (exc) => setNotice({ text: `下载失败：${toApiError(exc).message}`, tone: 'danger' }),
  })

  // No `file://` — a WebView refuses it. The backend resolves the path from this
  // deliverable's own record, so no path crosses the wire from the page.
  const open = useMutation({
    mutationFn: () => deliveryApi.openDeliverable(item.id),
    onSuccess: () => setNotice(null),
    onError: (exc) => setNotice({ text: `打开失败：${toApiError(exc).message}`, tone: 'danger' }),
  })

  return (
    <div className="rounded-xl border border-border bg-surface p-3.5" data-testid="deliverable-card">
      <div className="flex flex-wrap items-center gap-2">
        <Package className="size-4 shrink-0 text-muted" />
        <span className="text-[13.5px] text-text">
          {item.title}
          {item.version > 1 && <span className="text-faint"> v{item.version}</span>}
        </span>
        <Badge tone="neutral">{KIND_LABELS[item.kind] ?? item.kind}</Badge>
        <Badge tone="neutral">{formatSize(item.size_bytes)}</Badge>
        {item.verified ? (
          <Badge tone="success">已机器校验</Badge>
        ) : (
          <Badge tone="warn">未校验</Badge>
        )}
        {item.accepted === true && <Badge tone="success">你已验收</Badge>}
        {item.accepted === false && <Badge tone="danger">你已退回</Badge>}
        <span className="ml-auto text-[11.5px] text-faint">{formatWhen(item.created_at)}</span>
      </div>

      {notice && (
        <div className="mt-2" data-testid="deliverable-notice">
          <InlineAlert tone={notice.tone} title={notice.tone === 'danger' ? '没能完成' : '已更新'}>
            {notice.text}
          </InlineAlert>
        </div>
      )}

      {item.accepted === false && item.feedback && (
        <p className="mt-2 rounded-lg border border-danger/25 bg-danger/5 px-3 py-1.5 text-[12px] text-danger/90">
          退回原因：「{item.feedback}」
        </p>
      )}

      <div className="mt-2.5 rounded-lg border border-border/60 bg-inset/40 p-2.5">
        <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">验证证据</p>
        <VerificationPanel item={item} />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          size="xs"
          variant="secondary"
          data-testid="download-deliverable"
          disabled={download.isPending}
          onClick={() => download.mutate()}
        >
          {download.isPending ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Download className="size-3" />
          )}
          {download.isPending ? '正在取回…' : '下载'}
        </Button>
        <Button
          size="xs"
          variant="ghost"
          data-testid="open-deliverable"
          disabled={open.isPending}
          onClick={() => open.mutate()}
        >
          {open.isPending ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <ExternalLink className="size-3" />
          )}
          打开
        </Button>

        {item.accepted === null && !rejecting && (
          <>
            <Button
              size="xs"
              variant="primary"
              className="ml-auto"
              data-testid="accept-deliverable"
              disabled={accept.isPending}
              onClick={() => accept.mutate()}
            >
              <Check className="size-3" /> 接受
            </Button>
            <Button
              size="xs"
              variant="secondary"
              data-testid="start-reject"
              onClick={() => setRejecting(true)}
            >
              <X className="size-3" /> 退回
            </Button>
          </>
        )}
      </div>

      {rejecting && (
        <div className="mt-3 rounded-lg border border-warn/30 bg-warn/5 p-3">
          <p className="mb-2 text-[12px] text-warn">
            哪里不满意？一句话就够 —— 这是它学会你标准的唯一途径。
          </p>
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={2}
            autoFocus
            data-testid="reject-reason"
            placeholder="例如：图表太少，页数也偏多"
            className="w-full resize-none rounded-lg border border-border bg-base px-3 py-2 text-[13px] text-text outline-none focus:border-warn/60"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button size="xs" variant="ghost" onClick={() => setRejecting(false)}>
              取消
            </Button>
            <Button
              size="xs"
              variant="primary"
              data-testid="confirm-reject"
              disabled={reason.trim().length < 2 || reject.isPending}
              onClick={() => reject.mutate()}
            >
              {reject.isPending ? <Loader2 className="size-3 animate-spin" /> : null}
              确认退回
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Which tier answered this question, and how long each took.
 *
 * Rendered from the real ladder, not from a summary the UI computed. If the call fails
 * the failure is shown — an absent line would read as "answered from memory" to anyone
 * skimming, which is the one thing this must not be mistaken for.
 */
const TIER_LABELS: Record<string, string> = {
  memory: '记忆',
  skill: 'Skill',
  knowledge: '知识库',
  general: '通用路径',
}

function TierLine({ question }: { question: string }) {
  const tiers = useQuery({
    queryKey: ['retrieval', 'tiers', question],
    queryFn: () => contextApi.tiers(question),
    enabled: question.trim().length > 0,
    staleTime: 60_000,
  })

  if (!question.trim()) return null

  if (tiers.isPending) {
    return <p className="mb-2 text-[11.5px] text-faint">正在判断这次由哪一层回答…</p>
  }

  if (tiers.isError) {
    return (
      <p className="mb-2 text-[11.5px] text-danger">
        没能判断这次由哪一层回答：{tiers.error instanceof Error ? tiers.error.message : String(tiers.error)}
      </p>
    )
  }

  const result = tiers.data
  const label = TIER_LABELS[result.tier] ?? result.tier

  return (
    <div className="mb-2.5 space-y-1.5">
      <p className="text-[12px] text-muted">
        这次由 <span className="font-medium text-text">「{label}」</span> 回答
        {result.tier !== 'general' && (
          <span className="text-faint"> —— 没有走通用推理，用的是你自己的东西</span>
        )}
      </p>
      <div className="flex flex-wrap gap-1.5">
        {result.attempts.map((attempt) => (
          <span
            key={attempt.tier}
            className={cn(
              'rounded border px-1.5 py-px text-2xs',
              attempt.hit ? 'border-accent/40 text-text' : 'border-border text-faint',
            )}
            title={attempt.detail}
          >
            {TIER_LABELS[attempt.tier] ?? attempt.tier} {attempt.hit ? '命中' : '未命中'} {attempt.elapsed_ms.toFixed(0)}ms
          </span>
        ))}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ page -- */

export default function WorkbenchPage() {
  const { runId } = useParams<{ runId: string }>()
  const queryClient = useQueryClient()

  const run = useQuery({
    queryKey: ['delivery', 'build-run', runId],
    queryFn: () => deliveryApi.buildRun(runId!),
    enabled: Boolean(runId),
    // Poll only while the run is active. A terminal run does not change, and
    // polling it forever is how a page quietly burns a battery.
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status) return false
      const terminal = ['COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'LIMIT_REACHED']
      return terminal.includes(status) ? false : 4000
    },
  })

  const context = useQuery({
    queryKey: ['delivery', 'context', runId],
    queryFn: () => deliveryApi.buildRunContext(runId!),
    enabled: Boolean(runId),
  })

  const recent = useQuery({
    queryKey: ['delivery', 'build-runs', 'recent'],
    queryFn: () => deliveryApi.buildRuns({ limit: 8 }),
  })

  if (!runId) {
    return (
      <PageContainer className="space-y-4">
        <Card>
          <CardHeader title="工作台" description="选择一次任务，看它是怎么做的" />
          <CardBody>
            {recent.isLoading ? (
              <LoadingBlock label="读取任务…" />
            ) : (recent.data?.build_runs.length ?? 0) === 0 ? (
              <EmptyState
                icon={<Package className="size-5" />}
                title="还没有任务"
                description="在首页说一句你想完成什么，然后回到这里看它怎么做。"
                compact
              />
            ) : (
              <div className="divide-y divide-border/50">
                {(recent.data?.build_runs ?? []).map((item) => (
                  <Link
                    key={item.id}
                    to={`/workbench/${item.id}`}
                    className="flex items-center gap-3 py-2.5 transition-colors hover:bg-inset/40"
                  >
                    <span className="min-w-0 flex-1 truncate text-[13px] text-text">{item.title}</span>
                    <Badge tone={STATUS_TONES[item.status] as never}>{STATUS_LABELS[item.status]}</Badge>
                    <span className="text-[11.5px] text-faint">{formatWhen(item.created_at)}</span>
                  </Link>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </PageContainer>
    )
  }

  if (run.isLoading) {
    return (
      <PageContainer>
        <LoadingBlock label="读取任务…" />
      </PageContainer>
    )
  }
  if (run.isError || !run.data) {
    return (
      <PageContainer>
        <ErrorState title="任务不存在" error={run.error} onRetry={() => void run.refetch()} />
      </PageContainer>
    )
  }

  const data = run.data
  const steps = ((data.plan as { steps?: { id?: number; description?: string; verification?: string }[] })?.steps ?? [])
  const deliverables = data.deliverables ?? []

  return (
    <PageContainer className="space-y-4">
      {/* header */}
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-muted transition-colors hover:text-text">
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[16px] font-medium text-text">{data.title}</h1>
          <p className="mt-0.5 truncate text-[12px] text-muted">「{data.goal}」</p>
        </div>
        <Badge tone={STATUS_TONES[data.status] as never}>{STATUS_LABELS[data.status]}</Badge>
        {data.started_at && (
          <span className="text-[11.5px] text-faint">开始于 {formatWhen(data.started_at)}</span>
        )}
        <Button size="xs" variant="ghost" onClick={() => void queryClient.invalidateQueries({ queryKey: ['delivery'] })}>
          刷新
        </Button>
      </div>

      {data.error && (
        <InlineAlert tone="danger" title="运行出错">{data.error}</InlineAlert>
      )}

      {/* A run in DRAFT has been created and nothing more. It must not read like
          work in progress — three empty panels saying "还没有…" is exactly how the
          original bug hid for a session. Say it, and offer the one action that
          resolves it. */}
      {(data.status === 'DRAFT' || data.status === 'READY') && (
        <StartPanel
          runId={data.id}
          retryable={data.status === 'READY'}
        />
      )}

      {/* What it made comes first. The question a person opens this page with is
          "did it make my thing?", not "which step is it on" — so the deliverables
          hold the top of the page and the machinery lives in 「详情」 below.
          Nothing was removed; the三个 panels are one click away. */}
      <Card>
        <CardHeader
          title="产出物"
          description="生成不等于完成 —— 每一份都要能读回，并且由你决定它是不是你要的"
          actions={deliverables.length > 0 ? <Badge tone="neutral">{deliverables.length}</Badge> : null}
        />
        <CardBody className="space-y-2.5">
          {deliverables.length === 0 ? (
            <EmptyState
              icon={<Package className="size-5" />}
              title="还没有产出"
              description={
                data.status === 'DRAFT' || data.status === 'READY'
                  ? '这次任务还没开始。开始之后，它做出来的文件会出现在这里。'
                  : '任务执行完成后，它做出来的文件会出现在这里。'
              }
              compact
            />
          ) : (
            deliverables.map((item) => <DeliverableCard key={item.id} item={item} />)
          )}
        </CardBody>
      </Card>

      {/* the machinery, folded away by default */}
      <details
        className="group rounded-xl border border-border bg-surface"
        open={data.status === 'EXECUTING'}
      >
        <summary
          data-testid="workbench-details"
          className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-[13px] text-muted transition-colors hover:text-text"
        >
          <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
          <span className="font-medium">详情</span>
          <span className="text-[11.5px] text-faint">
            它准备怎么做、现在做到哪、这次依据了什么
          </span>
          <span className="ml-auto text-[11.5px] text-faint">
            {data.steps_done}/{data.steps_total || '—'} 步
          </span>
        </summary>

        <div className="grid gap-3 border-t border-border p-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,0.9fr)]">
          {/* plan */}
          <Card>
            <CardHeader title="执行计划" description={`${data.steps_done}/${data.steps_total || '—'} 步`} />
            <CardBody>
              {steps.length === 0 ? (
                <p className="text-[12.5px] text-faint">还没有生成计划。</p>
              ) : (
                <ol className="space-y-1.5">
                  {steps.map((step, index) => {
                    const done = index < data.steps_done
                    const current = index === data.steps_done && data.status === 'EXECUTING'
                    return (
                      <li key={step.id ?? index} className="flex items-start gap-2">
                        {done ? (
                          <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
                        ) : current ? (
                          <Loader2 className="mt-0.5 size-3.5 shrink-0 animate-spin text-accent" />
                        ) : (
                          <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-faint" />
                        )}
                        <div className="min-w-0">
                          <p className={cn('text-[12.5px]', done ? 'text-muted' : 'text-text')}>
                            {step.description ?? `第 ${index + 1} 步`}
                          </p>
                          {step.verification && (
                            <p className="text-[11px] text-faint">验收：{step.verification}</p>
                          )}
                        </div>
                      </li>
                    )
                  })}
                </ol>
              )}
            </CardBody>
          </Card>

        {/* execution */}
        <Card>
          <CardHeader
            title="实时执行"
            description={data.status === 'EXECUTING' ? '正在运行' : STATUS_LABELS[data.status]}
            actions={data.status === 'EXECUTING' ? <Loader2 className="size-3.5 animate-spin text-accent" /> : null}
          />
          <CardBody>
            {data.result_summary ? (
              <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-muted">
                {data.result_summary}
              </p>
            ) : data.status === 'DRAFT' || data.status === 'PLANNING' ? (
              <p className="text-[12.5px] text-faint">
                还没有开始执行。计划生成后，这里会显示每一步的进展。
              </p>
            ) : (
              <p className="text-[12.5px] text-faint">暂无执行记录。</p>
            )}

            {data.ai_run_id && (
              <Link
                to={`/ai-runs/${data.ai_run_id}`}
                className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-muted underline-offset-2 hover:text-text hover:underline"
              >
                <ExternalLink className="size-3" /> 查看完整执行轨迹
              </Link>
            )}
          </CardBody>
        </Card>

        {/* context */}
        <Card>
          <CardHeader title="本次上下文" description="它这次被告诉了什么" />
          <CardBody>
            {/* Which tier answered. The product's claim is that it is faster than
                starting from scratch, and that claim is only meaningful where the work
                happens: a question answered from the user's own memory never reached the
                model at all, and this is where that becomes visible rather than asserted. */}
            <TierLine question={data.goal} />

            {context.isLoading ? (
              <LoadingBlock label="读取上下文…" />
            ) : context.data?.available ? (
              <>
                <p className="mb-2 text-[11.5px] text-faint">
                  约 {context.data.token_estimate ?? 0} tokens
                  {context.data.truncated && ' · 已按预算裁剪'}
                </p>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-inset p-2.5 font-sans text-[11.5px] leading-relaxed text-muted">
                  {context.data.rendered}
                </pre>
              </>
            ) : (
              <p className="text-[12.5px] text-faint">
                {context.data?.reason ?? '本次运行没有注入个人上下文。'}
              </p>
            )}
          </CardBody>
        </Card>
        </div>
      </details>

      {data.status === 'FAILED' && (
        <div className="flex items-center gap-2 text-[12px] text-danger">
          <AlertTriangle className="size-3.5" />
          这次没有完成。原因已记录在上方。
        </div>
      )}
    </PageContainer>
  )
}
