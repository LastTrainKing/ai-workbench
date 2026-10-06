/**
 * The Skill page — where the third asset lives.
 *
 * Three sections, in the order a person actually moves through them:
 *
 *   1. **已安装** — what it has adopted, where each came from, and how it has performed.
 *      The source and the verification summary are on the card rather than behind a
 *      click: a skill is executable content from an unknown author, and "we checked it"
 *      is only meaningful if what was checked is visible.
 *   2. **去找** — a search that produces candidates *with reasons*, so a person can
 *      choose. The rejected list is shown too, because a filter nobody can see is a
 *      filter nobody can trust.
 *   3. **待绑定** — a skill whose `{{params}}` are unfilled. The placeholders are left in
 *      place rather than blanked, so the gap is visible and can be filled instead of
 *      silently producing a sentence with a hole in it.
 *
 * Nothing here installs on its own. The adopt button is the only path, and it is a
 * separate click from the search that produced the candidate.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BadgeCheck,
  Download,
  ExternalLink,
  Loader2,
  Search,
  Star,
  ThumbsDown,
  Trash2,
  Wrench,
} from 'lucide-react'

import { Button } from '@/components/ui/Button'
import { Badge, Card, CardBody, CardHeader, StatTile } from '@/components/ui/primitives'
import { EmptyState, InlineAlert } from '@/components/ui/feedback'
import { toastApiError, useToastStore } from '@/store/toast'
import { skillsApi, type InstalledSkill, type SkillCandidate } from '@/lib/skillsApi'

function VerificationLine({ skill }: { skill: InstalledSkill }) {
  const findings = skill.verification?.findings ?? []
  const blocking = findings.filter((f) => f.severity === 'block')
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-2xs text-faint">
      <span className="inline-flex items-center gap-1 text-success">
        <BadgeCheck className="size-3" />
        已验证 {skill.verified_at ? skill.verified_at.slice(0, 10) : ''}
      </span>
      {blocking.length > 0 && <Badge tone="danger">{blocking.length} 处风险</Badge>}
      {skill.verification?.summary && <span>· {skill.verification.summary}</span>}
    </div>
  )
}

function InstalledCard({ skill, onChanged }: { skill: InstalledSkill; onChanged: () => void }) {
  const success = useToastStore((state) => state.success)
  const [note, setNote] = useState('')

  const review = useMutation({
    mutationFn: (payload: { rating: number; retire?: boolean }) =>
      skillsApi.review(skill.id, { ...payload, note }),
    onSuccess: () => {
      success('已记录复盘')
      onChanged()
    },
    onError: (error) => toastApiError(error, '复盘失败'),
  })

  const disable = useMutation({
    mutationFn: () => skillsApi.disable(skill.id),
    onSuccess: () => {
      success('已停用', '记录保留，作为这个来源的证据')
      onChanged()
    },
    onError: (error) => toastApiError(error, '停用失败'),
  })

  const unfilled = skill.verification?.unfilled_params ?? []

  return (
    <Card>
      <CardHeader
        title={skill.name}
        description={skill.description}
        actions={
          <div className="flex items-center gap-1.5">
            {skill.skill_type === 'script' && <Badge tone="warn">含脚本</Badge>}
            <Badge tone={skill.enabled ? 'success' : 'neutral'}>
              {skill.enabled ? '启用中' : '已停用'}
            </Badge>
          </div>
        }
      />
      <CardBody className="space-y-3">
        <p className="text-[12.5px] leading-relaxed text-muted">{skill.when_to_use || '未说明使用场景'}</p>

        <VerificationLine skill={skill} />

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-faint">
          <span className="inline-flex items-center gap-1">
            <Star className="size-3" />
            用过 {skill.use_count} 次{skill.use_count > 0 ? `，成功 ${skill.success_count} 次` : ''}
          </span>
          {skill.rating !== null && <span>评分 {skill.rating.toFixed(1)}</span>}
          {skill.license && <span>许可 {skill.license}</span>}
          <span>v{skill.version}</span>
          {skill.source && (
            <a
              href={skill.source}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 hover:text-text"
            >
              <ExternalLink className="size-3" />
              {skill.source.replace(/^https?:\/\/(www\.)?/, '').slice(0, 42)}
            </a>
          )}
        </div>

        {unfilled.length > 0 && (
          <InlineAlert tone="warn" title="还有没填上的个性化位置">
            正文里的 {unfilled.map((p) => `{{${p}}}`).join('、')} 还没有值。它们保持原样而不是留空 ——
            留空会让它读起来像知道这件事。
          </InlineAlert>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="复盘备注（可选）"
            className="min-w-40 flex-1 rounded-lg border border-border bg-inset px-2.5 py-1.5 text-[12px] text-text outline-none focus:border-accent/60"
          />
          <Button variant="secondary" size="sm" disabled={review.isPending} onClick={() => review.mutate({ rating: 1 })}>
            <Wrench className="size-3.5" />
            好用
          </Button>
          <Button variant="ghost" size="sm" disabled={review.isPending} onClick={() => review.mutate({ rating: 0.2, retire: true })}>
            <ThumbsDown className="size-3.5" />
            不合适
          </Button>
          {skill.enabled && (
            <Button variant="ghost" size="sm" disabled={disable.isPending} onClick={() => disable.mutate()}>
              <Trash2 className="size-3.5" />
              停用
            </Button>
          )}
        </div>
      </CardBody>
    </Card>
  )
}

function CandidateCard({ candidate, onAdopted }: { candidate: SkillCandidate; onAdopted: () => void }) {
  const success = useToastStore((state) => state.success)

  const adopt = useMutation({
    mutationFn: () =>
      skillsApi.adopt({
        name: candidate.name,
        description: candidate.why || candidate.name,
        when_to_use: candidate.name,
        // A candidate found by search has no body yet — fetching it is the next step in
        // the pipeline, and until it happens this records the source rather than
        // pretending to have the contents.
        body: `## 做法\n（来源：${candidate.repo}）\n待补：从仓库取回 SKILL.md 正文。`,
        source: candidate.url,
        license: candidate.license,
      }),
    onSuccess: () => {
      success('已导入', candidate.name)
      onAdopted()
    },
    onError: (error) => toastApiError(error, '导入失败'),
  })

  return (
    <div className="rounded-xl border border-border p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[13.5px] font-medium text-text">{candidate.repo}</p>
          <p className="mt-0.5 text-[12px] leading-relaxed text-muted">{candidate.why}</p>
        </div>
        <Button variant="primary" size="sm" disabled={adopt.isPending} onClick={() => adopt.mutate()}>
          {adopt.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
          导入
        </Button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-faint">
        <span>★{candidate.stars}</span>
        {candidate.license && <span>{candidate.license}</span>}
        <a href={candidate.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-text">
          <ExternalLink className="size-3" />
          看仓库
        </a>
      </div>
    </div>
  )
}

export default function SkillPanel() {
  const queryClient = useQueryClient()
  const [need, setNeed] = useState('')

  const installed = useQuery({ queryKey: ['skills', 'installed'], queryFn: () => skillsApi.list() })
  const stats = useQuery({ queryKey: ['skills', 'stats'], queryFn: () => skillsApi.stats() })

  const discover = useMutation({
    mutationFn: () => skillsApi.discover(need),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['skills'] })
  }

  const s = stats.data

  return (
    <div className="space-y-4">
      {s && (
        <div className="grid gap-3 sm:grid-cols-4">
          <StatTile label="已安装" value={s.installed} />
          <StatTile label="用过" value={s.ever_used} sub={`共 ${s.total_uses} 次`} />
          <StatTile label="从没用过" value={s.never_used} />
          <StatTile
            label="平均评分"
            value={s.average_rating === null ? '—' : s.average_rating}
            sub={`${s.rated} 个已复盘`}
          />
        </div>
      )}

      {/* ------------------------------------------------------------ 去找 -- */}
      <Card>
        <CardHeader title="去找" description="去公开渠道找适合你的做法，你确认之后才会导入" />
        <CardBody className="space-y-3">
          <div className="flex gap-2">
            <input
              value={need}
              onChange={(e) => setNeed(e.target.value)}
              placeholder="你想把哪类活干完？例如「写毕业论文的文献综述」"
              className="flex-1 rounded-lg border border-border bg-inset px-3 py-2 text-[13px] text-text outline-none focus:border-accent/60"
            />
            <Button variant="primary" size="sm" disabled={discover.isPending} onClick={() => discover.mutate()}>
              {discover.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
              找找看
            </Button>
          </div>

          {discover.isError && (
            <InlineAlert tone="danger" title="搜索没成功">
              {discover.error instanceof Error ? discover.error.message : String(discover.error)}
            </InlineAlert>
          )}

          {discover.data && (
            <>
              <p className="text-2xs text-faint">
                搜索词：{discover.data.queries.join(' · ')}
              </p>
              {discover.data.error && <InlineAlert tone="warn" title="部分搜索词失败">{discover.data.error}</InlineAlert>}

              {discover.data.candidates.length === 0 ? (
                <EmptyState title="这次没找到合适的" description={discover.data.summary} />
              ) : (
                <div className="space-y-2.5">
                  {discover.data.candidates.map((c) => (
                    <CandidateCard key={c.repo} candidate={c} onAdopted={refresh} />
                  ))}
                </div>
              )}

              {discover.data.rejected.length > 0 && (
                <details className="rounded-lg border border-border p-3">
                  <summary className="cursor-pointer text-[12.5px] text-muted">
                    被筛掉的 {discover.data.rejected.length} 个（点开看理由）
                  </summary>
                  <ul className="mt-2 space-y-1">
                    {discover.data.rejected.map((r) => (
                      <li key={r.repo} className="text-2xs text-faint">
                        <span className="text-muted">{r.repo}</span> — {r.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </CardBody>
      </Card>

      {/* -------------------------------------------------------- 已安装 -- */}
      <div className="space-y-3">
        <p className="text-[13px] font-medium text-text">已安装</p>
        {installed.isPending ? (
          <p className="text-xs text-muted">加载中…</p>
        ) : (installed.data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="还没有导入任何 skill"
            description="在上面搜一次，它会带着理由把候选给你，你选中的才会进来。"
          />
        ) : (
          installed.data?.items.map((skill) => (
            <InstalledCard key={skill.id} skill={skill} onChanged={refresh} />
          ))
        )}
      </div>
    </div>
  )
}
