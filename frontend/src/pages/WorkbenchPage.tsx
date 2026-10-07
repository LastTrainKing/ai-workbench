/**
 * The workbench. One run, in detail.
 *
 * The conversation page is where work happens; this is where you go when you want to
 * look at one piece of it properly. Both import `DeliverableCard` and
 * `RunDetailsFold` rather than each having their own — two implementations of "the
 * card with 下载 / 打开 / 接受 / 退回 on it" would drift, and the drift would only
 * show up as a missing button.
 *
 * Order is unchanged and deliberate: **产出物 first**. The question a person opens
 * this page with is "did it make my thing", not "which step is it on". 执行计划 /
 * 实时执行 / 本次上下文 are one click below, still live, still polling.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Loader2, Package, Play } from 'lucide-react'

import { Badge, Card, CardBody, CardHeader, PageContainer } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, InlineAlert, LoadingBlock } from '@/components/ui/feedback'
import { DeliverableCard } from '@/features/delivery/DeliverableCard'
import { RunDetailsFold, type PlanStep } from '@/features/delivery/RunDetails'
import { deliveryApi, STATUS_LABELS, STATUS_TONES, formatWhen } from '@/lib/deliveryApi'
import { toApiError } from '@/lib/api'

/* ------------------------------------------------------------ not started -- */

/**
 * Shown while a run is created but not started.
 *
 * This panel exists because its absence was a real defect. `DRAFT` used to look
 * identical to a run that was thinking — an empty plan, an empty execution panel, an
 * empty context panel — and the user waited for work that had never been asked to
 * begin. The status is now something the screen says out loud, and it offers the one
 * action that resolves it.
 */
function StartPanel({ runId }: { runId: string }) {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)

  const start = useMutation({
    mutationFn: () => deliveryApi.executeBuildRun(runId),
    onSuccess: () => {
      setNotice(null)
      void queryClient.invalidateQueries({ queryKey: ['delivery'] })
    },
    // Not swallowed. If it will not start, that is the whole message.
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  return (
    <div className="rounded-xl border border-warn/40 bg-warn-soft/50 p-4" data-testid="not-started">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-[14px] font-medium text-text">它还没有开始</p>
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
          <InlineAlert tone="danger" title="没能开始">{notice}</InlineAlert>
        </div>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------- page -- */

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
                description="在对话页说一句你想完成什么，它就会出现在这里。"
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
  const deliverables = data.deliverables ?? []
  const steps: PlanStep[] = ((data.plan as { steps?: PlanStep[] } | undefined)?.steps ?? [])

  return (
    <PageContainer className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-muted transition-colors hover:text-text" aria-label="回到对话">
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
        <Button
          size="xs"
          variant="ghost"
          onClick={() => void queryClient.invalidateQueries({ queryKey: ['delivery'] })}
        >
          刷新
        </Button>
      </div>

      {data.error && <InlineAlert tone="danger" title="运行出错">{data.error}</InlineAlert>}

      {(data.status === 'DRAFT' || data.status === 'READY') && <StartPanel runId={data.id} />}

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

      {/* The machinery, folded. Still polling, still showing the real event stream —
          the fold is a presentation choice, not a removal. */}
      <RunDetailsFold run={data} steps={steps} />

      {data.status === 'FAILED' && (
        <div className="flex items-center gap-2 text-[12px] text-danger">
          <AlertTriangle className="size-3.5" />
          这次没有完成。原因已记录在上方。
        </div>
      )}
    </PageContainer>
  )
}
