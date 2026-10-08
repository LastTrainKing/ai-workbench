/**
 * 执行计划 / 实时执行 / 本次上下文, behind one summary element.
 *
 * Shared by the conversation page and the workbench. It is one decision — "what
 * goes behind 详情, and what does the fold say on its summary line" — and two copies
 * of it would drift, which is the failure mode this round was about.
 *
 * **The summary line carries the "why", because a "why" you have to click for is not
 * in the design.** The user's rule: 「默认显示的那一行应该说清"这次用了什么"，展开才是
 * 细节」. So the closed state says *由「知识库」回答，用了 1 段你的资料* and expanding shows
 * the plan and the material itself.
 *
 * **What is not here any more, and why.** The fold used to print
 * `memory=未命中(7ms) · skill=未命中(6ms) · knowledge=命中(44ms)` and `约 69 tokens`.
 * Those are the numbers *this project* is tuned with — they belong in the startup log
 * and in `scripts/sample_relevance.py`, which still read them. On screen they answer a
 * question the user did not ask, and the user's rule is blunt about it: 「用户看得懂的
 * 留，看不懂的删。别让用户读你的性能报告」. The backend still returns both strings;
 * `summary` is the one that is rendered.
 *
 * The 执行计划 panel is not rendered at all when there is no plan. An empty state that
 * says 「还没有生成计划」 is a row of screen taken from something that does exist, to
 * report the absence of something that does not.
 *
 * Default closed, with one exception: a run that is **executing** starts open. That
 * is the moment somebody is watching, and a fold that hid the live progress of a
 * running job would be hiding the thing the person came to see.
 *
 * The context is fetched only once the fold is opened. Fetching it for every turn
 * on the conversation page would be one request per run for something nobody has
 * asked to look at.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, ChevronRight, CircleDashed, Loader2 } from 'lucide-react'

import { LoadingBlock } from '@/components/ui/feedback'
import { deliveryApi, type BuildRun } from '@/lib/deliveryApi'
import { contextApi } from '@/lib/contextApi'
import { cn } from '@/lib/utils'

export interface PlanStep {
  id?: number
  description?: string
  verification?: string
}

/** What the retrieval ladder decided, as the API returns it. */
export interface RetrievalView {
  tier?: string
  /** The sentence for a person. */
  summary?: string
  /** The line with milliseconds in it. Kept for logs, never rendered. */
  explanation?: string
  total_ms?: number
}

/**
 * 「它凭什么这么说」, on one line.
 *
 * Shared so the same fact is worded the same way wherever it appears — under an answer,
 * on a run's fold, and in the workbench.
 */
export function RetrievalLine({
  retrieval,
  className,
}: {
  retrieval?: RetrievalView
  className?: string
}) {
  if (!retrieval?.summary) return null
  return (
    <p className={cn('text-[11.5px] text-faint', className)} data-testid="retrieval-line">
      {retrieval.summary}
    </p>
  )
}

export function RunDetailsFold({
  run,
  steps,
  retrieval,
}: {
  run: BuildRun
  steps: PlanStep[]
  /**
   * Which rung of the retrieval ladder answered, when the caller already knows.
   *
   * A `start` message carries it from the understanding layer's own retrieval, so the
   * fold shows the tier without asking again — one retrieval, one place it is recorded,
   * and no second request that could disagree with the first.
   */
  retrieval?: RetrievalView
}) {
  const [open, setOpen] = useState(false)

  const context = useQuery({
    queryKey: ['delivery', 'context', run.id],
    queryFn: () => deliveryApi.buildRunContext(run.id),
    enabled: open,
  })
  const tiers = useQuery({
    queryKey: ['retrieval', 'tiers', run.goal],
    queryFn: () => contextApi.tiers(run.goal),
    enabled: open && !retrieval?.summary && run.goal.trim().length > 0,
    staleTime: 60_000,
  })

  const line = retrieval?.summary ?? tiers.data?.summary

  const expanded = open || run.status === 'EXECUTING'

  // Only the panels that have something in them. See the note above about empty states.
  const showPlan = steps.length > 0
  const showExecution = Boolean(run.result_summary) || run.status === 'EXECUTING'
  const panels = (showPlan ? 1 : 0) + (showExecution ? 1 : 0) + 1

  return (
    <details
      className="group rounded-xl border border-border bg-surface"
      open={expanded}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary
        data-testid="run-details"
        className="flex cursor-pointer list-none items-center gap-2 px-3.5 py-2.5 text-[12.5px] text-muted transition-colors hover:text-text"
      >
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
        <span className="font-medium">详情</span>
        {/* The reason, on the closed line. Without this the answer to 「它凭什么这么说」
            was behind the click, which is the same as not being there. */}
        {line ? (
          <span className="min-w-0 truncate text-[11.5px] text-faint" data-testid="run-details-line">
            {line}
          </span>
        ) : (
          <span className="hidden text-[11.5px] text-faint sm:inline">
            它准备怎么做、现在做到哪
          </span>
        )}
        {run.steps_total > 0 && (
          <span className="ml-auto shrink-0 text-[11.5px] text-faint">
            {run.steps_done}/{run.steps_total} 步
          </span>
        )}
      </summary>

      <div
        className={cn(
          'grid gap-3 border-t border-border p-3',
          panels >= 3 ? 'lg:grid-cols-3' : panels === 2 ? 'lg:grid-cols-2' : '',
        )}
      >
        {showPlan && (
          <section data-testid="panel-plan">
            <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">执行计划</p>
            <ol className="space-y-1.5">
              {steps.map((step, index) => {
                const done = index < run.steps_done
                const current = index === run.steps_done && run.status === 'EXECUTING'
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
                      <p className={cn('text-[12px]', done ? 'text-muted' : 'text-text')}>
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
          </section>
        )}

        {showExecution && (
          <section data-testid="panel-execution">
            <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">实时执行</p>
            {run.result_summary ? (
              <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-muted">
                {run.result_summary}
              </p>
            ) : (
              <p className="text-[12px] text-faint">正在运行…</p>
            )}
            {run.ai_run_id && (
              <Link
                to={`/ai-runs/${run.ai_run_id}`}
                className="mt-2 inline-block text-[11.5px] text-muted underline-offset-2 hover:text-text hover:underline"
              >
                查看完整执行轨迹 →
              </Link>
            )}
          </section>
        )}

        <section data-testid="panel-context">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">这次依据了什么</p>
          {line ? (
            <p className="mb-1.5 text-[12px] text-text" data-testid="panel-context-line">
              {line}
            </p>
          ) : (
            <p className="mb-1.5 text-[12px] text-faint">还没查过这次用了什么。</p>
          )}
          {context.isLoading ? (
            <LoadingBlock label="读取上下文…" />
          ) : context.data?.available ? (
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-inset p-2 font-sans text-[11px] leading-relaxed text-muted">
              {context.data.rendered}
            </pre>
          ) : (
            <p className="text-[12px] text-faint">
              {context.data?.reason ?? '这次没有用到你的记忆或资料。'}
            </p>
          )}
        </section>
      </div>
    </details>
  )
}
