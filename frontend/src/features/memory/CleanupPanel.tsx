/**
 * The one-time cleanup, and the rule it exists to honour: 先给用户看、再删.
 *
 * Fixing the mechanism that produced the duplicates does not remove the ones already
 * in the library. This is the removal, and it is two steps with a person in between:
 *
 *   检查一下   computes the whole plan and shows it. Writes nothing.
 *   执行       runs exactly the plan that was displayed.
 *
 * There is no "clean up automatically" button, and that is not a missing feature.
 * Memory is 「它对这个人的理解」, and a deletion the user did not see is not a cleanup —
 * it is the system editing their idea of themselves. The plan names every row, what
 * happens to it, and why, so the thing being approved is legible.
 *
 * The grouping is decided by the model, not by a threshold. `similarity.py` measures
 * why: 「用户喜欢用 Python 写脚本」 and 「用户喜欢用 Java 写脚本」 score higher than every
 * genuine rephrasing in the sample, so a threshold either merges them or misses the
 * real duplicates. Both are wrong, and one of them loses a fact about the user.
 */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, Trash2, Wand2 } from 'lucide-react'

import { Badge, Card, CardBody, CardHeader } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { InlineAlert } from '@/components/ui/feedback'
import { contextApi, type CleanupPlan } from '@/lib/contextApi'
import { toApiError } from '@/lib/api'

export function CleanupPanel() {
  const queryClient = useQueryClient()
  const [plan, setPlan] = useState<CleanupPlan | null>(null)
  const [notice, setNotice] = useState<{ text: string; tone: 'info' | 'danger' } | null>(null)

  const inspect = useMutation({
    mutationFn: () => contextApi.cleanupPlan(),
    onSuccess: (result) => {
      setNotice(null)
      setPlan(result)
    },
    onError: (exc) => setNotice({ text: toApiError(exc).message, tone: 'danger' }),
  })

  const apply = useMutation({
    mutationFn: () =>
      contextApi.cleanupApply({
        duplicate_groups: (plan?.duplicate_groups ?? []).map((group) => ({
          keep_id: group.keep_id,
          merge: group.merged.map((row) => row.id),
          why: group.why,
        })),
        candidate_groups: (plan?.candidate_groups ?? []).map((group) => ({
          keep_id: group.keep_id,
          merge: group.merged.map((row) => row.id),
          why: group.why,
        })),
        litter: (plan?.litter ?? []).map((row) => ({ id: row.id, why: row.why })),
        total_before: plan?.total_before ?? 0,
      }),
    onSuccess: (result) => {
      const { merged, deleted, candidates_folded: folded } = result.data
      setNotice({
        text: `已合并 ${merged} 条记忆、${folded} 条待确认，删除 ${deleted} 条。`,
        tone: 'info',
      })
      setPlan(null)
      void queryClient.invalidateQueries({ queryKey: ['context'] })
    },
    onError: (exc) => setNotice({ text: toApiError(exc).message, tone: 'danger' }),
  })

  const nothingToDo =
    plan !== null &&
    plan.rows_removed_from_retrieval === 0 &&
    plan.candidate_groups.length === 0

  return (
    <Card>
      <CardHeader
        title="记忆库体检"
        description="它自己找出重复的说法和测试留下的垃圾，列给你看，你点头才动"
        actions={
          plan ? (
            <Badge tone={nothingToDo ? 'success' : 'accent'}>
              {nothingToDo ? '不用清理' : `清理后剩 ${plan.total_after} 条`}
            </Badge>
          ) : null
        }
      />
      <CardBody className="space-y-3">
        {notice && (
          <InlineAlert tone={notice.tone} title={notice.tone === 'danger' ? '没能完成' : '已完成'}>
            {notice.text}
          </InlineAlert>
        )}

        {!plan && (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="secondary"
              size="sm"
              data-testid="memory-cleanup-inspect"
              disabled={inspect.isPending}
              onClick={() => inspect.mutate()}
            >
              {inspect.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
              {inspect.isPending ? '正在检查…' : '检查一下记忆库'}
            </Button>
            <span className="text-[12px] text-faint">
              只会列出「要动哪几条、为什么」，不会直接删。
            </span>
          </div>
        )}

        {plan && (
          <>
            <p className="text-[12.5px] text-muted" data-testid="memory-cleanup-note">
              {plan.note}
            </p>

            {plan.duplicate_groups.length > 0 && (
              <div className="space-y-2" data-testid="memory-cleanup-duplicates">
                <p className="text-[11px] uppercase tracking-wide text-faint">
                  说的是同一件事 —— 合并，不删除
                </p>
                {plan.duplicate_groups.map((group) => (
                  <div key={group.keep_id} className="rounded-lg border border-border bg-inset/40 p-2.5">
                    <p className="text-[12.5px] text-text">
                      <span className="text-success">保留</span> {group.keep_content}
                    </p>
                    <p className="mt-0.5 text-[11.5px] text-faint">
                      用过 {group.keep_use_count} 次 → 合并后 <span className="text-text">{group.merged_use_count}</span> 次
                      （重复的用量累加过来，不是从头开始）
                    </p>
                    {group.merged.map((row) => (
                      <p key={row.id} className="mt-1 text-[11.5px] text-muted">
                        <span className="text-warn">并入</span> {row.content}
                        <span className="text-faint">（{row.use_count} 次）</span>
                      </p>
                    ))}
                    <p className="mt-1 text-[11px] text-faint">依据：{group.why}</p>
                  </div>
                ))}
              </div>
            )}

            {plan.candidate_groups.length > 0 && (
              <div className="space-y-2" data-testid="memory-cleanup-candidates">
                <p className="text-[11px] uppercase tracking-wide text-faint">
                  待你确认里说的是同一件事 —— 合并成一条，你只需要答一次
                </p>
                {plan.candidate_groups.map((group) => (
                  <div key={group.keep_id} className="rounded-lg border border-border bg-inset/40 p-2.5">
                    <p className="text-[12.5px] text-text">
                      <span className="text-success">保留</span> {group.keep_content}
                    </p>
                    {group.merged.map((row) => (
                      <p key={row.id} className="mt-1 text-[11.5px] text-muted">
                        <span className="text-warn">并入</span> {row.content}
                      </p>
                    ))}
                    <p className="mt-1 text-[11px] text-faint">依据：{group.why}</p>
                  </div>
                ))}
              </div>
            )}

            {plan.litter.length > 0 && (
              <div className="space-y-2" data-testid="memory-cleanup-litter">
                <p className="text-[11px] uppercase tracking-wide text-faint">
                  不是关于你的东西 —— 会删除
                </p>
                {plan.litter.map((row) => (
                  <div
                    key={row.id}
                    className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/5 p-2.5"
                  >
                    <Trash2 className="mt-0.5 size-3.5 shrink-0 text-danger" />
                    <div className="min-w-0">
                      <p className="text-[12.5px] text-text">{row.content}</p>
                      <p className="text-[11px] text-faint">{row.why}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {nothingToDo ? (
              <p className="text-[12.5px] text-faint">
                没有需要清理的东西 —— 重复的和垃圾的都没有。
              </p>
            ) : (
              <div className="flex flex-wrap items-center gap-3 border-t border-border/60 pt-3">
                <Button
                  variant="primary"
                  size="sm"
                  data-testid="memory-cleanup-apply"
                  disabled={apply.isPending}
                  onClick={() => apply.mutate()}
                >
                  {apply.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}
                  {apply.isPending ? '正在执行…' : '就按这个来'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setPlan(null)}>
                  先不动
                </Button>
                <span className="flex items-center gap-1.5 text-[11.5px] text-warn">
                  <AlertTriangle className="size-3" />
                  合并只标记、不删除；上面列出的「不是关于你的东西」会真的删掉
                </span>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  )
}
