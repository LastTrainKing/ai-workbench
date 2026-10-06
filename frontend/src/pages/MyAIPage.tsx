/**
 * "My AI" — profile, memory and context, in one place.
 *
 * This is the page that makes the personal layer trustworthy rather than
 * merely present. Every fact the system holds is listed, editable and
 * deletable; every proposed fact waits for a decision; and the context preview
 * shows the exact text that would be sent to the model, block by block, with
 * the token cost of each.
 *
 * The framing matters. A user should not have to take "it knows you" on faith —
 * they should be able to read it and correct it.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Archive,
  Brain,
  Check,
  FileText,
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  User,
  X,
} from 'lucide-react'

import { Badge, Card, CardBody, CardHeader, PageContainer, SectionHeader } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { EmptyState, ErrorState, InlineAlert, LoadingBlock } from '@/components/ui/feedback'
import {
  contextApi,
  KIND_LABELS,
  KIND_TONES,
  scopeLabel,
  type MemoryStatus,
} from '@/lib/contextApi'
import type { Memory, MemoryKind, Profile } from '@/lib/contextApi'
import { toApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

const KINDS: MemoryKind[] = ['identity', 'goal', 'preference', 'skill', 'constraint', 'fact', 'decision']

/* ---------------------------------------------------------------- memory -- */

function MemoryRow({
  memory,
  onDelete,
  onArchive,
  onSave,
}: {
  memory: Memory
  onDelete: () => void
  onArchive: () => void
  onSave: (content: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(memory.content)
  const origin = memory.structured?.origin

  return (
    <div className="flex items-start gap-3 border-b border-border/50 px-4 py-3 last:border-0">
      <span className="mt-1 size-1.5 shrink-0 rounded-full bg-accent/70" />
      <div className="min-w-0 flex-1">
        {editing ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            className="w-full resize-none rounded-lg border border-border bg-inset px-2.5 py-1.5 text-[13px] text-text outline-none focus:border-accent/60"
          />
        ) : (
          <p className="text-[13px] leading-relaxed text-text">{memory.content}</p>
        )}
        {origin && (
          <div className="mt-2 rounded-lg border border-border/60 bg-inset/60 px-2.5 py-2 text-[11px] leading-relaxed text-muted">
            <div>
              来源：
              {origin.decided_at ? `${origin.decided_at.slice(0, 10)} 的 ` : ''}
              「{origin.deliverable_title || '（未命名交付）'}」
              {origin.version ? ` 第 ${origin.version} 版` : ''}
            </div>
            {origin.decision === 'rejected' && origin.feedback && (
              <div className="mt-0.5">你退回了它，说「{origin.feedback}」</div>
            )}
            {origin.decision === 'accepted' && (
              <div className="mt-0.5">你接受了它，没有提出修改意见</div>
            )}
            {origin.build_run_id && (
              <div className="mt-1">
                <Link
                  to={`/workbench/${origin.build_run_id}`}
                  className="text-accent underline-offset-2 hover:underline"
                >
                  查看那次交付
                </Link>
              </div>
            )}
          </div>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-faint">
          <span className={cn('text-' + (KIND_TONES[memory.kind] ?? 'neutral'))}>
            {KIND_LABELS[memory.kind] ?? memory.kind}
          </span>
          <span>{scopeLabel(memory.scope)}</span>
          <span>置信度 {memory.confidence.toFixed(2)}</span>
          <span>使用 {memory.use_count} 次</span>
          {memory.confirmed_by_user && <span className="text-success">你确认过</span>}
          <span className="text-faint/70">{memory.source}</span>
        </div>
      </div>
      <div className="flex shrink-0 gap-1">
        {editing ? (
          <>
            <Button
              size="xs"
              variant="primary"
              onClick={() => {
                onSave(draft)
                setEditing(false)
              }}
            >
              <Check className="size-3" />
            </Button>
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                setDraft(memory.content)
                setEditing(false)
              }}
            >
              <X className="size-3" />
            </Button>
          </>
        ) : (
          <>
            <Button size="xs" variant="ghost" onClick={() => setEditing(true)}>
              <Pencil className="size-3" />
            </Button>
            <Button size="xs" variant="ghost" title="归档（不再参与检索，但保留记录）" onClick={onArchive}>
              <Archive className="size-3" />
            </Button>
            <Button size="xs" variant="ghost" onClick={onDelete}>
              <Trash2 className="size-3" />
            </Button>
          </>
        )}
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- profile -- */

function ProfileForm({ profile }: { profile: Profile }) {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Profile>(profile)
  const [notice, setNotice] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () =>
      contextApi.updateProfile({
        display_name: draft.display_name,
        identity: draft.identity,
        stage: draft.stage,
        domain: draft.domain,
        current_goals: draft.current_goals,
        bio: draft.bio,
        onboarded: true,
      }),
    onSuccess: () => {
      setNotice('档案已保存，下一次对话立即生效。')
      void queryClient.invalidateQueries({ queryKey: ['context'] })
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const fields: { key: keyof Profile; label: string; hint: string; multiline?: boolean }[] = [
    { key: 'display_name', label: '称呼', hint: '希望它怎么称呼你' },
    { key: 'identity', label: '身份', hint: '你是谁，例如「计算机专业学生」' },
    { key: 'stage', label: '阶段', hint: '例如「大三，准备毕业设计」' },
    { key: 'domain', label: '领域', hint: '你关注的领域' },
    { key: 'bio', label: '补充说明', hint: '希望它怎么和你协作', multiline: true },
  ]

  return (
    <div className="space-y-4">
      {notice && <InlineAlert tone="success" title="已保存">{notice}</InlineAlert>}

      <Card>
        <CardHeader title="档案" description="它每次都会带上这些信息，所以尽量简短准确" />
        <CardBody className="space-y-3">
          {fields.map((field) => (
            <div key={String(field.key)}>
              <label className="mb-1 block text-[11px] uppercase tracking-wide text-faint">
                {field.label}
              </label>
              {field.multiline ? (
                <textarea
                  value={String(draft[field.key] ?? '')}
                  onChange={(e) => setDraft({ ...draft, [field.key]: e.target.value })}
                  rows={3}
                  placeholder={field.hint}
                  className="w-full resize-none rounded-lg border border-border bg-inset px-3 py-2 text-[13px] text-text outline-none focus:border-accent/60"
                />
              ) : (
                <input
                  value={String(draft[field.key] ?? '')}
                  onChange={(e) => setDraft({ ...draft, [field.key]: e.target.value })}
                  placeholder={field.hint}
                  className="w-full rounded-lg border border-border bg-inset px-3 py-2 text-[13px] text-text outline-none focus:border-accent/60"
                />
              )}
            </div>
          ))}

          <div>
            <label className="mb-1 block text-[11px] uppercase tracking-wide text-faint">
              当前目标
            </label>
            <input
              value={(draft.current_goals ?? []).join('、')}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  current_goals: e.target.value.split(/[、,，]/).map((s) => s.trim()).filter(Boolean),
                })
              }
              placeholder="用「、」分隔，例如：完成毕业设计、找实习"
              className="w-full rounded-lg border border-border bg-inset px-3 py-2 text-[13px] text-text outline-none focus:border-accent/60"
            />
          </div>

          <div className="flex items-center justify-between pt-1">
            <p className="text-[11px] text-faint">
              实际注入模型的摘要：<span className="text-muted">{draft.digest || '（空）'}</span>
            </p>
            <Button variant="primary" size="sm" disabled={save.isPending} onClick={() => save.mutate()}>
              {save.isPending ? <RefreshCw className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
              保存
            </Button>
          </div>
        </CardBody>
      </Card>
    </div>
  )
}

/* --------------------------------------------------------------- context -- */

function ContextPanel() {
  const query = useQuery({
    queryKey: ['context', 'preview'],
    queryFn: () => contextApi.preview(),
  })

  if (query.isLoading) return <LoadingBlock label="正在组装上下文…" />
  if (query.isError) {
    return <ErrorState title="无法读取上下文" error={query.error} onRetry={() => void query.refetch()} />
  }

  const preview = query.data!
  const budget = preview.budget_tokens ?? 1800
  const usage = Math.min(100, Math.round((preview.token_estimate / budget) * 100))

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="本次会注入什么"
          description={`${preview.token_estimate} / ${budget} tokens${preview.truncated ? '（已裁剪）' : ''}`}
          actions={<Layers className="size-4 text-muted" />}
        />
        <CardBody className="space-y-3">
          <div className="h-1.5 overflow-hidden rounded-full bg-inset">
            <div
              className={cn('h-full rounded-full', usage > 90 ? 'bg-warn' : 'bg-accent')}
              style={{ width: `${usage}%` }}
            />
          </div>

          {preview.empty ? (
            <p className="text-[13px] text-faint">
              目前没有任何个人上下文。填好档案后，这里会显示实际注入的内容。
            </p>
          ) : (
            <div className="space-y-2">
              {preview.blocks.map((block) => (
                <div key={block.kind} className="rounded-lg border border-border bg-inset/50 p-3">
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="text-[11px] uppercase tracking-wide text-faint">
                      {block.kind}
                    </span>
                    <Badge tone="neutral">{block.tokens} tokens</Badge>
                    {block.truncated && <Badge tone="warn">已截断</Badge>}
                  </div>
                  <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-relaxed text-text">
                    {block.text}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="实际发送的完整文本" description="逐字渲染，不做任何概括" />
        <CardBody>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-inset p-3 font-mono text-[12px] leading-relaxed text-muted">
            {preview.rendered || '（空）'}
          </pre>
        </CardBody>
      </Card>
    </div>
  )
}

/* ------------------------------------------------------------------ page -- */

type Tab = 'memory' | 'profile' | 'context'

export default function MyAIPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<Tab>('memory')
  const [filter, setFilter] = useState<string>('')
  const [newMemory, setNewMemory] = useState('')
  const [notice, setNotice] = useState<string | null>(null)

  const memories = useQuery({
    queryKey: ['context', 'memories', filter],
    queryFn: () => contextApi.memories(filter ? { kind: filter } : {}),
  })
  const candidates = useQuery({
    queryKey: ['context', 'candidates'],
    queryFn: () => contextApi.candidates('pending'),
  })
  const profile = useQuery({ queryKey: ['context', 'profile'], queryFn: () => contextApi.profile() })
  const stats = useQuery({ queryKey: ['context', 'stats'], queryFn: () => contextApi.contextStats() })

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ['context'] })

  const create = useMutation({
    mutationFn: () => contextApi.createMemory({ content: newMemory.trim(), kind: 'fact' }),
    onSuccess: () => {
      setNewMemory('')
      setNotice('已添加。')
      invalidate()
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })
  const update = useMutation({
    mutationFn: ({
      id,
      content,
      status,
    }: {
      id: string
      content?: string
      status?: MemoryStatus
    }) => contextApi.updateMemory(id, { content, status }),
    onSuccess: invalidate,
    onError: (exc) => setNotice(toApiError(exc).message),
  })
  const remove = useMutation({
    mutationFn: (id: string) => contextApi.deleteMemory(id),
    onSuccess: () => {
      setNotice('已删除，之后不会再被检索到。')
      invalidate()
    },
  })
  const approve = useMutation({
    mutationFn: (id: string) => contextApi.approveCandidate(id),
    onSuccess: () => {
      setNotice('已记为长期记忆。')
      invalidate()
    },
  })
  const reject = useMutation({
    mutationFn: (id: string) => contextApi.rejectCandidate(id),
    onSuccess: () => {
      setNotice('已拒绝，不会再被记录。')
      invalidate()
    },
  })

  const pending = candidates.data?.candidates ?? []
  const rows = memories.data?.memories ?? []

  const summary = useMemo(
    () => [
      { label: '长期记忆', value: stats.data?.memories_active ?? 0 },
      { label: '待确认', value: stats.data?.candidates_pending ?? 0 },
      { label: '偏好', value: stats.data?.preferences ?? 0 },
      { label: '上下文快照', value: stats.data?.context_snapshots ?? 0 },
    ],
    [stats.data],
  )

  return (
    <PageContainer className="space-y-5">
      <SectionHeader
        title="我的 AI"
        description="它对你的了解，全部在这里，随时可改可删。"
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {summary.map((item) => (
          <div key={item.label} className="rounded-xl border border-border bg-surface px-4 py-3">
            <p className="text-[11px] uppercase tracking-wide text-faint">{item.label}</p>
            <p className="mt-1 font-mono text-[20px] tabular-nums text-text">{item.value}</p>
          </div>
        ))}
      </div>

      {notice && <InlineAlert tone="info" title="已更新">{notice}</InlineAlert>}

      {/* ------------------------------------------------------- candidates -- */}
      {pending.length > 0 && (
        <Card className="border-accent/40">
          <CardHeader
            title="它想记住这些"
            description="需要你确认后才会记为长期记忆"
            actions={<Badge tone="accent">{pending.length}</Badge>}
          />
          <CardBody className="divide-y divide-border/50 p-0">
            {pending.map((candidate) => (
              <div key={candidate.id} className="flex items-start gap-3 px-4 py-3">
                <Sparkles className="mt-0.5 size-4 shrink-0 text-accent" />
                <div className="min-w-0 flex-1">
                  <p className="text-[13px] text-text">{candidate.content}</p>
                  <p className="mt-1 text-[11.5px] text-muted">{candidate.reason}</p>
                  <div className="mt-1 flex gap-2 text-[11px] text-faint">
                    <span>{KIND_LABELS[candidate.proposed_kind] ?? candidate.proposed_kind}</span>
                    <span>置信度 {candidate.confidence.toFixed(2)}</span>
                    {candidate.conflicts_with && (
                      <span className="text-warn">与已有记忆冲突</span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button size="xs" variant="primary" onClick={() => approve.mutate(candidate.id)}>
                    <Check className="size-3" /> 记住
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => reject.mutate(candidate.id)}>
                    <X className="size-3" /> 不用
                  </Button>
                </div>
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      {/* ------------------------------------------------------------ tabs -- */}
      <div className="flex gap-1 border-b border-border">
        {(
          [
            ['memory', '记忆', Brain],
            ['profile', '档案', User],
            ['context', '上下文预览', FileText],
          ] as const
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={cn(
              '-mb-px flex items-center gap-1.5 border-b-2 px-3.5 py-2 text-[13px] transition-colors',
              tab === key ? 'border-accent text-text' : 'border-transparent text-muted hover:text-text',
            )}
          >
            <Icon className="size-3.5" />
            {label}
          </button>
        ))}
      </div>

      {tab === 'memory' && (
        <div className="space-y-4">
          <Card>
            <CardBody className="flex gap-2">
              <input
                value={newMemory}
                onChange={(e) => setNewMemory(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newMemory.trim()) create.mutate()
                }}
                placeholder="手动添加一条记忆，例如：我偏好简洁的代码风格"
                className="flex-1 rounded-lg border border-border bg-inset px-3 py-2 text-[13px] text-text outline-none focus:border-accent/60"
              />
              <Button
                variant="primary"
                size="sm"
                disabled={!newMemory.trim() || create.isPending}
                onClick={() => create.mutate()}
              >
                <Plus className="size-3.5" /> 添加
              </Button>
            </CardBody>
          </Card>

          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => setFilter('')}
              className={cn(
                'rounded-full border px-2.5 py-1 text-[11.5px] transition-colors',
                !filter ? 'border-accent/50 text-text' : 'border-border text-muted hover:text-text',
              )}
            >
              全部
            </button>
            {KINDS.map((kind) => (
              <button
                key={kind}
                type="button"
                onClick={() => setFilter(kind)}
                className={cn(
                  'rounded-full border px-2.5 py-1 text-[11.5px] transition-colors',
                  filter === kind ? 'border-accent/50 text-text' : 'border-border text-muted hover:text-text',
                )}
              >
                {KIND_LABELS[kind]}
              </button>
            ))}
          </div>

          <Card>
            <CardHeader
              title="长期记忆"
              description={`${rows.length} 条${filter ? `（已筛选：${KIND_LABELS[filter as MemoryKind] ?? filter}）` : ''}`}
            />
            {memories.isLoading ? (
              <CardBody><LoadingBlock label="读取记忆…" /></CardBody>
            ) : rows.length === 0 ? (
              <CardBody>
                <EmptyState
                  icon={<Brain className="size-5" />}
                  title="还没有记忆"
                  description="和它聊几句，或者手动添加一条。它只会在确实值得的时候才记住。"
                  compact
                />
              </CardBody>
            ) : (
              <CardBody className="p-0">
                {rows.map((memory) => (
                  <MemoryRow
                    key={memory.id}
                    memory={memory}
                    onDelete={() => remove.mutate(memory.id)}
                    onArchive={() => update.mutate({ id: memory.id, status: 'archived' })}
                    onSave={(content) => update.mutate({ id: memory.id, content })}
                  />
                ))}
              </CardBody>
            )}
          </Card>
        </div>
      )}

      {tab === 'profile' &&
        (profile.isLoading ? (
          <LoadingBlock label="读取档案…" />
        ) : profile.data ? (
          <ProfileForm profile={profile.data} />
        ) : null)}

      {tab === 'context' && <ContextPanel />}
    </PageContainer>
  )
}
