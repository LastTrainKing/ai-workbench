/**
 * 执行计划 / 实时执行 / 本次上下文, behind one summary element.
 *
 * Shared by the conversation page and the workbench. It is one decision — "what
 * goes behind 详情, and what does the fold say on its summary line" — and two copies
 * of it would drift, which is the failure mode this round was about.
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

const TIER_LABELS: Record<string, string> = {
  memory: '记忆',
  skill: 'Skill',
  knowledge: '知识库',
  general: '通用路径',
}

export function RunDetailsFold({ run, steps }: { run: BuildRun; steps: PlanStep[] }) {
  const [open, setOpen] = useState(false)

  const context = useQuery({
    queryKey: ['delivery', 'context', run.id],
    queryFn: () => deliveryApi.buildRunContext(run.id),
    enabled: open,
  })
  const tiers = useQuery({
    queryKey: ['retrieval', 'tiers', run.goal],
    queryFn: () => contextApi.tiers(run.goal),
    enabled: open && run.goal.trim().length > 0,
    staleTime: 60_000,
  })

  const expanded = open || run.status === 'EXECUTING'

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
        <span className="hidden text-[11.5px] text-faint sm:inline">
          它准备怎么做、现在做到哪、这次依据了什么
        </span>
        <span className="ml-auto text-[11.5px] text-faint">
          {run.steps_done}/{run.steps_total || '—'} 步
        </span>
      </summary>

      <div className="grid gap-3 border-t border-border p-3 lg:grid-cols-3">
        <section data-testid="panel-plan">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">执行计划</p>
          {steps.length === 0 ? (
            <p className="text-[12px] text-faint">还没有生成计划。</p>
          ) : (
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
          )}
        </section>

        <section data-testid="panel-execution">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">实时执行</p>
          {run.result_summary ? (
            <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-muted">
              {run.result_summary}
            </p>
          ) : (
            <p className="text-[12px] text-faint">
              {run.status === 'EXECUTING' ? '正在运行…' : '暂无执行记录。'}
            </p>
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

        <section data-testid="panel-context">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-faint">本次上下文</p>
          {tiers.data && (
            <p className="mb-1.5 text-[11.5px] text-muted">
              这次由「{TIER_LABELS[tiers.data.tier] ?? tiers.data.tier}」回答
              {tiers.data.tier !== 'general' && (
                <span className="text-faint"> —— 没有走通用推理，用的是你自己的东西</span>
              )}
            </p>
          )}
          {context.isLoading ? (
            <LoadingBlock label="读取上下文…" />
          ) : context.data?.available ? (
            <>
              <p className="mb-1.5 text-[11px] text-faint">
                约 {context.data.token_estimate ?? 0} tokens
                {context.data.truncated && ' · 已按预算裁剪'}
              </p>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-inset p-2 font-sans text-[11px] leading-relaxed text-muted">
                {context.data.rendered}
              </pre>
            </>
          ) : (
            <p className="text-[12px] text-faint">
              {context.data?.reason ?? '本次运行没有注入个人上下文。'}
            </p>
          )}
        </section>
      </div>
    </details>
  )
}
