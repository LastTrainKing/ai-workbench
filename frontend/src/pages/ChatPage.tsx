import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  ArrowUpRight,
  BookMarked,
  Bot,
  Check,
  Layers,
  RotateCcw,
  Send,
  ShieldCheck,
  ShieldX,
  Sparkles,
  User,
} from 'lucide-react'
import { api, toApiError } from '@/lib/api'
import { cn, formatMs } from '@/lib/utils'
import { Button, IconButton } from '@/components/ui/Button'
import { Field, Select, Textarea } from '@/components/ui/form'
import { Modal } from '@/components/ui/overlay'
import { Badge, Card, CardBody, PageContainer, SectionHeader, SegmentedControl } from '@/components/ui/primitives'
import { EmptyState, InlineAlert } from '@/components/ui/feedback'
import { Markdown } from '@/components/Markdown'
import type { ChatMessageIn, Citation, RagAnswer, SearchMode } from '@/lib/types'

const MODES: { value: SearchMode; label: string }[] = [
  { value: 'hybrid', label: '混合' },
  { value: 'semantic', label: '语义' },
  { value: 'keyword', label: '关键词' },
]

interface Turn {
  id: string
  question: string
  answer?: RagAnswer
  error?: string
  pending: boolean
}

let turnCounter = 0
function nextTurnId(): string {
  turnCounter += 1
  return `turn-${Date.now().toString(36)}-${turnCounter}`
}

function CitationCard({
  citation,
  elementId,
  highlighted,
}: {
  citation: Citation
  elementId: string
  highlighted: boolean
}) {
  return (
    <div
      id={elementId}
      className={cn(
        'rounded-md border bg-surface px-2.5 py-2 transition-colors scroll-mt-24',
        highlighted ? 'border-accent bg-accent-soft' : 'border-subtle',
      )}
    >
      <div className="flex items-start gap-2">
        <span className="flex size-5 shrink-0 items-center justify-center rounded border border-accent/40 bg-accent-soft px-1 font-mono text-2xs font-semibold text-accent">
          {citation.index}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-2xs font-medium text-primary">{citation.document_title}</span>
            {citation.chunk_index !== null ? (
              <span className="font-mono text-2xs text-faint">#{citation.chunk_index}</span>
            ) : null}
            <Badge tone="neutral" mono>
              {citation.score.toFixed(4)}
            </Badge>
          </div>
          {citation.heading ? (
            <p className="mt-0.5 truncate text-2xs text-faint">{citation.heading}</p>
          ) : null}
          <p className="mt-1 max-h-24 overflow-y-auto whitespace-pre-wrap text-2xs leading-relaxed text-muted">
            {citation.snippet}
          </p>
        </div>
      </div>
    </div>
  )
}

function AnswerBlock({
  turn,
  onCitationClick,
  activeCitation,
}: {
  turn: Turn
  onCitationClick: (index: number) => void
  activeCitation: number | null
}) {
  const answer = turn.answer
  if (turn.pending) {
    return (
      <div className="flex items-center gap-2 py-2 text-xs text-muted">
        <span className="flex gap-0.5">
          <span className="size-1.5 animate-blink rounded-full bg-accent" />
          <span className="size-1.5 animate-blink rounded-full bg-accent [animation-delay:150ms]" />
          <span className="size-1.5 animate-blink rounded-full bg-accent [animation-delay:300ms]" />
        </span>
        正在检索知识库并生成回答…
      </div>
    )
  }

  if (turn.error) {
    return <InlineAlert tone="danger" title="回答失败">{turn.error}</InlineAlert>
  }

  if (!answer) return null

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={answer.grounded ? 'success' : 'warn'} icon={answer.grounded ? <ShieldCheck className="size-3" /> : <ShieldX className="size-3" />}>
          {answer.grounded ? '有据可依' : '未接地'}
        </Badge>
        <Badge tone="neutral">{answer.provider}</Badge>
        <Badge tone="neutral" mono>
          {answer.model}
        </Badge>
        <Badge tone="info">{formatMs(answer.latency_ms)}</Badge>
        {answer.retrieval ? (
          <>
            <Badge tone="neutral">稠密 {answer.retrieval.dense_hits}</Badge>
            <Badge tone="neutral">稀疏 {answer.retrieval.sparse_hits}</Badge>
            <Badge tone="neutral">采用 {answer.retrieval.used_chunks} 片段</Badge>
          </>
        ) : null}
        <Link
          to={`/ai-runs/${answer.run_id}`}
          className="ml-auto inline-flex items-center gap-1 text-2xs text-accent hover:underline"
        >
          运行轨迹
          <ArrowUpRight className="size-3" />
        </Link>
      </div>

      {answer.queries && answer.queries.length > 1 ? (
        <p className="text-2xs text-faint">检索查询：{answer.queries.join(' · ')}</p>
      ) : null}

      <Markdown citations onCitationClick={onCitationClick} activeCitation={activeCitation}>
        {answer.answer}
      </Markdown>

      {answer.citations.length > 0 ? (
        <div className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">
            <BookMarked className="size-3" />
            引用来源（{answer.citations.length}）
          </p>
          <div className="space-y-1.5">
            {answer.citations.map((citation) => (
              <CitationCard
                key={citation.index}
                citation={citation}
                elementId={`citation-${turn.id}-${citation.index}`}
                highlighted={activeCitation === citation.index}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<SearchMode>('hybrid')
  const [topK, setTopK] = useState(8)
  const [selectedKbs, setSelectedKbs] = useState<string[]>([])
  const [kbModalOpen, setKbModalOpen] = useState(false)
  const [activeCitation, setActiveCitation] = useState<{ turnId: string; index: number } | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const basesQuery = useQuery({
    queryKey: ['knowledge-bases', 'options'],
    queryFn: ({ signal }) => api.knowledgeBases.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })

  const bases = useMemo(() => basesQuery.data?.items ?? [], [basesQuery.data])

  useEffect(() => {
    // Default to every knowledge base once the list arrives.
    if (bases.length > 0 && selectedKbs.length === 0) {
      setSelectedKbs(bases.map((base) => base.id))
    }
  }, [bases, selectedKbs.length])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns])

  const mutation = useMutation({
    mutationFn: (payload: { question: string; history: ChatMessageIn[] }) =>
      api.chat.ask({
        question: payload.question,
        knowledge_base_ids: selectedKbs,
        mode,
        top_k: topK,
        history: payload.history,
        project_id: null,
      }),
  })

  const historyFor = (list: Turn[]): ChatMessageIn[] => {
    const messages: ChatMessageIn[] = []
    for (const turn of list) {
      if (!turn.answer) continue
      messages.push({ role: 'user', content: turn.question })
      messages.push({ role: 'assistant', content: turn.answer.answer })
    }
    return messages.slice(-6)
  }

  const ask = async () => {
    const trimmed = question.trim()
    if (!trimmed || mutation.isPending) return

    const turn: Turn = { id: nextTurnId(), question: trimmed, pending: true }
    const history = historyFor(turns)
    setTurns((prev) => [...prev, turn])
    setQuestion('')

    try {
      const result = await mutation.mutateAsync({ question: trimmed, history })
      setTurns((prev) =>
        prev.map((item) => (item.id === turn.id ? { ...item, answer: result.data, pending: false } : item)),
      )
    } catch (error) {
      const apiError = toApiError(error)
      setTurns((prev) =>
        prev.map((item) =>
          item.id === turn.id
            ? { ...item, pending: false, error: `${apiError.message}（${apiError.code}）` }
            : item,
        ),
      )
    }
  }

  const handleCitationClick = (turnId: string, index: number) => {
    setActiveCitation({ turnId, index })
    window.setTimeout(() => {
      document.getElementById(`citation-${turnId}-${index}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    }, 20)
  }

  const selectedLabel = useMemo(() => {
    if (selectedKbs.length === 0) return '未选择知识库'
    if (selectedKbs.length === bases.length && bases.length > 0) return `全部知识库（${bases.length}）`
    return `已选 ${selectedKbs.length} 个知识库`
  }, [selectedKbs, bases.length])

  return (
    <PageContainer className="flex min-h-[calc(100vh-8rem)] flex-col space-y-4">
      <SectionHeader
        title="RAG 问答"
        description="回答完全基于所选知识库的检索片段，[n] 标记可点击跳转到对应引用。"
        actions={
          <>
            <Button
              size="sm"
              variant="secondary"
              icon={<Layers className="size-3.5" />}
              onClick={() => setKbModalOpen(true)}
            >
              {selectedLabel}
            </Button>
            <IconButton
              label="清空会话"
              size="sm"
              disabled={turns.length === 0}
              onClick={() => {
                setTurns([])
                setActiveCitation(null)
              }}
            >
              <RotateCcw className="size-3.5" />
            </IconButton>
          </>
        }
      />

      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <div>
            <p className="aiw-label">检索模式</p>
            <SegmentedControl value={mode} onChange={setMode} options={MODES} />
          </div>
          <Field label="引用片段数" className="w-[110px]">
            <Select value={String(topK)} onChange={(event) => setTopK(Number(event.target.value))}>
              {[4, 6, 8, 10, 12].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </Field>
          <div className="ml-auto flex flex-wrap gap-1">
            {bases.slice(0, 4).map((base) => (
              <Badge
                key={base.id}
                tone={selectedKbs.includes(base.id) ? 'accent' : 'neutral'}
              >
                {base.name}
              </Badge>
            ))}
            {bases.length > 4 ? <Badge tone="neutral">+{bases.length - 4}</Badge> : null}
          </div>
        </CardBody>
      </Card>

      <div className="flex-1 space-y-4">
        {turns.length === 0 ? (
          <EmptyState
            icon={<Bot className="size-4" />}
            title="开始一次有依据的问答"
            description="选择知识库后提问，回答会附带可点击的引用片段与运行轨迹。"
            className="py-16"
          />
        ) : (
          turns.map((turn) => (
            <div key={turn.id} className="space-y-3">
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md border border-subtle bg-elevated text-muted">
                  <User className="size-3.5" />
                </span>
                <p className="min-w-0 flex-1 whitespace-pre-wrap text-sm text-primary">{turn.question}</p>
              </div>
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-accent text-accent-fg">
                  <Sparkles className="size-3.5" />
                </span>
                <div className="min-w-0 flex-1">
                  <AnswerBlock
                    turn={turn}
                    activeCitation={activeCitation?.turnId === turn.id ? activeCitation.index : null}
                    onCitationClick={(index) => handleCitationClick(turn.id, index)}
                  />
                </div>
              </div>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <div className="sticky bottom-0 -mx-4 border-t border-subtle bg-base/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
        <div className="flex items-end gap-2">
          <Textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void ask()
              }
            }}
            rows={2}
            placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
            aria-label="提问输入"
            className="min-h-[46px]"
          />
          <Button
            variant="primary"
            size="md"
            loading={mutation.isPending}
            disabled={!question.trim() || selectedKbs.length === 0}
            icon={<Send className="size-3.5" />}
            onClick={() => void ask()}
          >
            发送
          </Button>
        </div>
        {selectedKbs.length === 0 ? (
          <p className="mt-1.5 text-2xs text-warn">请至少选择一个知识库。</p>
        ) : null}
      </div>

      <Modal
        open={kbModalOpen}
        onClose={() => setKbModalOpen(false)}
        title="选择知识库"
        description="回答只会引用所选知识库中的片段。"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setSelectedKbs([])}>
              清空
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setSelectedKbs(bases.map((base) => base.id))}
            >
              全选
            </Button>
            <Button variant="primary" size="sm" onClick={() => setKbModalOpen(false)}>
              完成
            </Button>
          </>
        }
      >
        {bases.length === 0 ? (
          <p className="text-xs text-faint">还没有知识库，先去创建并上传文档。</p>
        ) : (
          <ul className="space-y-1">
            {bases.map((base) => {
              const checked = selectedKbs.includes(base.id)
              return (
                <li key={base.id}>
                  <label className="flex cursor-pointer items-center gap-2.5 rounded-md border border-subtle px-2.5 py-2 transition-colors hover:border-strong">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setSelectedKbs((prev) =>
                          checked ? prev.filter((id) => id !== base.id) : [...prev, base.id],
                        )
                      }
                      className="size-3.5 accent-[rgb(var(--c-accent))]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs text-primary">{base.name}</span>
                      <span className="block truncate text-2xs text-faint">
                        {base.description || '暂无描述'}
                      </span>
                    </span>
                    {checked ? <Check className="size-3.5 shrink-0 text-accent" /> : null}
                  </label>
                </li>
              )
            })}
          </ul>
        )}
      </Modal>
    </PageContainer>
  )
}
