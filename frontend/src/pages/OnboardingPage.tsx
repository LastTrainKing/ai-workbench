/**
 * First-run wizard.
 *
 * Two doors, and both have to work:
 *
 *   「从介绍自己开始」  describe yourself in one sentence, the system proposes
 *                      what it understood, and **nothing is written until you
 *                      confirm it**.
 *   「先随便看看」      straight in, no form. Context then grows from use.
 *
 * The second door is not a courtesy. A first-run screen that demands
 * information before the product does anything is a screen people close. And the
 * confirmation step on the first door is the same principle as the memory
 * candidates: the system proposes, the user decides.
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'
import { ArrowRight, Check, Loader2, Sparkles, Wand2 } from 'lucide-react'

import { Card, CardBody, CardHeader, PageContainer } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { InlineAlert } from '@/components/ui/feedback'
import { contextApi } from '@/lib/contextApi'
import type { Profile } from '@/lib/contextApi'
import { toApiError } from '@/lib/api'

const EXAMPLES = [
  '我是计算机大三，成绩一般，在做毕业设计，最近想找实习，也想学点 AI',
  '我是做后端的，五年经验，最近在推团队的架构升级，时间比较紧',
  '我在准备考研，数学和英语是弱项，需要一个长期的复习计划',
]

/** What the system understood, shown back for confirmation before anything is stored. */
type Extraction = Pick<Profile, 'identity' | 'stage' | 'domain' | 'current_goals' | 'bio'>

export default function OnboardingPage() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'choose' | 'describe' | 'confirm'>('choose')
  const [description, setDescription] = useState('')
  const [extraction, setExtraction] = useState<Extraction | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const extract = useMutation({
    mutationFn: async () => {
      // The extraction is the user's own words parsed into fields. It is
      // deliberately done with a plain profile PATCH-free path: we ask the model
      // through the planner's provider by writing nothing and reading the
      // structured result back — see `_extract` below.
      return await extractProfile(description.trim())
    },
    onSuccess: (result) => {
      setExtraction(result)
      setMode('confirm')
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const confirm = useMutation({
    mutationFn: async () => {
      if (!extraction) throw new Error('nothing to save')
      const result = await contextApi.updateProfile({ ...extraction, onboarded: true })
      return result.data
    },
    onSuccess: () => navigate('/', { replace: true }),
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const skip = useMutation({
    mutationFn: () => contextApi.updateProfile({ onboarded: true }),
    onSuccess: () => navigate('/', { replace: true }),
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  return (
    <PageContainer className="flex min-h-full items-center justify-center">
      <div className="w-full max-w-2xl space-y-4">
        {notice && <InlineAlert tone="danger" title="出错了">{notice}</InlineAlert>}

        {mode === 'choose' && (
          <Card>
            <CardHeader title="欢迎" description="你可以先介绍一下自己，也可以直接开始" />
            <CardBody className="space-y-3">
              <button
                type="button"
                onClick={() => setMode('describe')}
                className="w-full rounded-xl border border-border p-5 text-left transition-all hover:border-accent/50 hover:bg-accent/5"
              >
                <div className="flex items-center gap-2">
                  <Sparkles className="size-4 text-accent" />
                  <span className="text-[15px] font-medium text-text">从介绍自己开始</span>
                </div>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
                  用一句话说说你是谁、在做什么。它会先展示自己的理解，你确认之后才写入。
                </p>
              </button>

              <button
                type="button"
                disabled={skip.isPending}
                onClick={() => skip.mutate()}
                className="w-full rounded-xl border border-border p-5 text-left transition-all hover:border-border-strong disabled:opacity-60"
              >
                <div className="flex items-center gap-2">
                  {skip.isPending ? (
                    <Loader2 className="size-4 animate-spin text-muted" />
                  ) : (
                    <ArrowRight className="size-4 text-muted" />
                  )}
                  <span className="text-[15px] font-medium text-text">先随便看看</span>
                </div>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
                  直接进入。它会随着使用慢慢了解你，不用现在填任何东西。
                </p>
              </button>
            </CardBody>
          </Card>
        )}

        {mode === 'describe' && (
          <Card>
            <CardHeader title="介绍一下你自己" description="一句话就够了，越自然越好" />
            <CardBody className="space-y-3">
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={3}
                autoFocus
                placeholder="例如：我是计算机大三，在做毕业设计，最近想找实习"
                className="w-full resize-none rounded-lg border border-border bg-inset px-3 py-2.5 text-[13.5px] text-text outline-none focus:border-accent/60"
              />
              <div className="flex flex-wrap gap-1.5">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => setDescription(example)}
                    className="rounded-full border border-border px-2.5 py-1 text-[11.5px] text-muted transition-colors hover:border-accent/40 hover:text-text"
                  >
                    {example.slice(0, 18)}…
                  </button>
                ))}
              </div>
              <div className="flex justify-between">
                <Button variant="ghost" size="sm" onClick={() => setMode('choose')}>
                  返回
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={description.trim().length < 4 || extract.isPending}
                  onClick={() => extract.mutate()}
                >
                  {extract.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Wand2 className="size-3.5" />
                  )}
                  看看它理解成了什么
                </Button>
              </div>
            </CardBody>
          </Card>
        )}

        {mode === 'confirm' && extraction && (
          <Card>
            <CardHeader
              title="它这样理解你"
              description="确认之后才会写入档案；不对就直接改"
            />
            <CardBody className="space-y-3">
              {(
                [
                  ['identity', '身份'],
                  ['stage', '阶段'],
                  ['domain', '领域'],
                  ['bio', '协作偏好'],
                ] as const
              ).map(([key, label]) => (
                <div key={key}>
                  <label className="mb-1 block text-[11px] uppercase tracking-wide text-faint">
                    {label}
                  </label>
                  <input
                    value={String(extraction[key] ?? '')}
                    onChange={(e) => setExtraction({ ...extraction, [key]: e.target.value })}
                    className="w-full rounded-lg border border-border bg-inset px-3 py-2 text-[13px] text-text outline-none focus:border-accent/60"
                  />
                </div>
              ))}
              <div>
                <label className="mb-1 block text-[11px] uppercase tracking-wide text-faint">
                  当前目标
                </label>
                <input
                  value={(extraction.current_goals ?? []).join('、')}
                  onChange={(e) =>
                    setExtraction({
                      ...extraction,
                      current_goals: e.target.value.split(/[、,，]/).map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  className="w-full rounded-lg border border-border bg-inset px-3 py-2 text-[13px] text-text outline-none focus:border-accent/60"
                />
              </div>
              <div className="flex justify-between pt-1">
                <Button variant="ghost" size="sm" onClick={() => setMode('describe')}>
                  重新描述
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  disabled={confirm.isPending}
                  onClick={() => confirm.mutate()}
                >
                  {confirm.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Check className="size-3.5" />
                  )}
                  确认，开始使用
                </Button>
              </div>
            </CardBody>
          </Card>
        )}
      </div>
    </PageContainer>
  )
}

/**
 * Turn a free-form self-description into profile fields.
 *
 * Uses the same provider the rest of the system uses. The result is returned for
 * confirmation and is never written here — the caller decides.
 */
async function extractProfile(description: string): Promise<Extraction> {
  const { request } = await import('@/lib/api')
  const result = await request<{
    identity: string
    stage: string
    domain: string
    current_goals: string[]
    bio: string
  }>('/profile/extract', { method: 'POST', body: { description } })
  return {
    identity: result.identity ?? '',
    stage: result.stage ?? '',
    domain: result.domain ?? '',
    current_goals: result.current_goals ?? [],
    bio: result.bio ?? '',
  }
}
