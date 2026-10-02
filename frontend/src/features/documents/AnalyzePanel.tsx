import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  BookOpen,
  BrainCircuit,
  ChevronDown,
  FileText,
  GraduationCap,
  ListChecks,
  MessageCircleQuestion,
  NotebookPen,
  Sparkles,
  Table2,
  Wand2,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatMs } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Badge, Card, CardBody, CardHeader } from '@/components/ui/primitives'
import { EmptyState, InlineAlert, SkeletonText } from '@/components/ui/feedback'
import { Markdown } from '@/components/Markdown'
import { toastApiError } from '@/store/toast'
import type { AnalysisAction, DocumentAnalysis } from '@/lib/types'

const ACTIONS: { key: AnalysisAction; label: string; icon: typeof Sparkles }[] = [
  { key: 'summary', label: '摘要', icon: FileText },
  { key: 'concepts', label: '概念', icon: BrainCircuit },
  { key: 'todos', label: '待办', icon: ListChecks },
  { key: 'terms', label: '术语表', icon: Table2 },
  { key: 'study_plan', label: '学习计划', icon: GraduationCap },
  { key: 'interview', label: '面试题', icon: MessageCircleQuestion },
  { key: 'note', label: '生成笔记', icon: NotebookPen },
  { key: 'cards', label: '记忆卡', icon: BookOpen },
]

/* ------------------------------------------------------------- result parts */

function SummaryBlock({ data }: { data: DocumentAnalysis }) {
  return (
    <div className="space-y-3">
      {data.summary ? (
        <div className="rounded-md border border-subtle bg-inset px-3 py-2.5">
          <Markdown>{data.summary}</Markdown>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-1.5">
        {data.difficulty ? <Badge tone="warn">难度 {data.difficulty}</Badge> : null}
        {data.audience ? <Badge tone="info">受众 {data.audience}</Badge> : null}
      </div>

      {data.key_points && data.key_points.length > 0 ? (
        <div>
          <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">要点</p>
          <ul className="space-y-1">
            {data.key_points.map((point, index) => (
              <li key={index} className="flex gap-2 text-xs text-primary">
                <span className="mt-1.5 size-1 shrink-0 rounded-full bg-accent" />
                <span>{point}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.keywords && data.keywords.length > 0 ? (
        <div>
          <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">关键词</p>
          <div className="flex flex-wrap gap-1">
            {data.keywords.map((keyword) => (
              <Badge key={keyword} tone="accent">
                {keyword}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function ConceptChips({ concepts }: { concepts: string[] }) {
  if (concepts.length === 0) return <p className="text-2xs text-faint">未抽取到概念。</p>
  return (
    <div className="flex flex-wrap gap-1.5">
      {concepts.map((concept) => (
        <span
          key={concept}
          className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent-soft px-2.5 py-1 text-2xs font-medium text-accent"
        >
          <BrainCircuit className="size-3" />
          {concept}
        </span>
      ))}
    </div>
  )
}

function TodoChecklist({ todos }: { todos: DocumentAnalysis['todos'] }) {
  const [checked, setChecked] = useState<Record<number, boolean>>({})
  if (!todos || todos.length === 0) return <p className="text-2xs text-faint">未生成待办项。</p>
  return (
    <ul className="space-y-1.5">
      {todos.map((todo, index) => (
        <li key={index}>
          <label className="flex cursor-pointer items-start gap-2.5 rounded-md border border-subtle bg-surface px-2.5 py-2 transition-colors hover:border-strong">
            <input
              type="checkbox"
              checked={Boolean(checked[index])}
              onChange={() => setChecked((prev) => ({ ...prev, [index]: !prev[index] }))}
              className="mt-0.5 size-3.5 shrink-0 accent-[rgb(var(--c-accent))]"
            />
            <span className="min-w-0">
              <span
                className={cn(
                  'block text-xs',
                  checked[index] ? 'text-faint line-through' : 'text-primary',
                )}
              >
                {todo.title}
              </span>
              {todo.source ? <span className="block text-2xs text-faint">来源：{todo.source}</span> : null}
            </span>
          </label>
        </li>
      ))}
    </ul>
  )
}

function TermTable({ terms }: { terms: DocumentAnalysis['terms'] }) {
  if (!terms || terms.length === 0) return <p className="text-2xs text-faint">未抽取到术语。</p>
  return (
    <div className="overflow-hidden rounded-md border border-subtle">
      <table className="w-full text-left text-xs">
        <thead className="bg-elevated text-2xs uppercase tracking-wide text-faint">
          <tr>
            <th className="w-[30%] px-2.5 py-1.5 font-medium">术语</th>
            <th className="px-2.5 py-1.5 font-medium">释义</th>
          </tr>
        </thead>
        <tbody>
          {terms.map((term, index) => (
            <tr key={`${term.term}-${index}`} className="border-t border-subtle align-top">
              <td className="px-2.5 py-2 font-medium text-primary">{term.term}</td>
              <td className="px-2.5 py-2 text-muted">{term.definition}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InterviewAccordion({ items }: { items: DocumentAnalysis['interview'] }) {
  const [open, setOpen] = useState<number | null>(0)
  if (!items || items.length === 0) return <p className="text-2xs text-faint">未生成面试题。</p>
  return (
    <ul className="space-y-1.5">
      {items.map((item, index) => {
        const expanded = open === index
        return (
          <li key={index} className="overflow-hidden rounded-md border border-subtle bg-surface">
            <button
              type="button"
              onClick={() => setOpen(expanded ? null : index)}
              aria-expanded={expanded}
              className="flex w-full items-start gap-2.5 px-2.5 py-2 text-left transition-colors hover:bg-elevated"
            >
              <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded bg-accent-soft font-mono text-2xs text-accent">
                {index + 1}
              </span>
              <span className="min-w-0 flex-1 text-xs font-medium text-primary">{item.question}</span>
              <span className="flex shrink-0 items-center gap-1.5">
                {item.difficulty ? <Badge tone="warn">{item.difficulty}</Badge> : null}
                {item.topic ? <Badge tone="info">{item.topic}</Badge> : null}
                <ChevronDown className={cn('size-3.5 text-faint transition-transform', expanded && 'rotate-180')} />
              </span>
            </button>
            {expanded ? (
              <div className="border-t border-subtle bg-inset px-2.5 py-2.5">
                <Markdown>{item.answer}</Markdown>
                {item.follow_up ? (
                  <p className="mt-2 rounded border border-subtle bg-surface px-2 py-1.5 text-2xs text-muted">
                    追问：{item.follow_up}
                  </p>
                ) : null}
              </div>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

function StudyPlanList({ days }: { days: DocumentAnalysis['study_plan'] }) {
  if (!days || days.length === 0) return <p className="text-2xs text-faint">未生成学习计划。</p>
  return (
    <ol className="space-y-1.5">
      {days.map((day, index) => (
        <li key={index} className="flex gap-2.5 rounded-md border border-subtle bg-surface px-2.5 py-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded bg-accent-soft font-mono text-2xs font-semibold text-accent">
            D{day.day}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-primary">{day.focus}</p>
            {day.goal ? <p className="mt-0.5 text-2xs text-muted">{day.goal}</p> : null}
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-2xs text-faint">
              {day.estimated_minutes ? <span>{day.estimated_minutes} 分钟</span> : null}
              {day.deliverable ? <Badge tone="neutral">产出：{day.deliverable}</Badge> : null}
            </div>
          </div>
        </li>
      ))}
    </ol>
  )
}

function FlashcardGrid({ cards }: { cards: DocumentAnalysis['cards'] }) {
  const [flipped, setFlipped] = useState<Record<number, boolean>>({})
  if (!cards || cards.length === 0) return <p className="text-2xs text-faint">未生成记忆卡。</p>
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {cards.map((card, index) => {
        const isFlipped = Boolean(flipped[index])
        return (
          <button
            key={index}
            type="button"
            onClick={() => setFlipped((prev) => ({ ...prev, [index]: !prev[index] }))}
            className="min-h-[92px] rounded-md border border-subtle bg-surface px-2.5 py-2.5 text-left transition-colors hover:border-strong"
          >
            <div className="flex items-center justify-between">
              <Badge tone={isFlipped ? 'success' : 'accent'}>{isFlipped ? '答案' : '问题'}</Badge>
              <span className="font-mono text-2xs text-faint">#{index + 1}</span>
            </div>
            <p className="mt-1.5 text-xs text-primary">{isFlipped ? card.back : card.front}</p>
            {card.source ? <p className="mt-1 text-2xs text-faint">来源：{card.source}</p> : null}
          </button>
        )
      })}
    </div>
  )
}

/* ---------------------------------------------------------------- panel */

export interface AnalyzePanelProps {
  documentId: string
  projectId?: string | null
}

export function AnalyzePanel({ documentId, projectId }: AnalyzePanelProps) {
  const [selected, setSelected] = useState<AnalysisAction[]>(['summary'])
  const [saveNote, setSaveNote] = useState(true)

  const mutation = useMutation({
    mutationFn: () =>
      api.documents.analyze(documentId, {
        actions: selected,
        save_note: saveNote,
        project_id: projectId ?? null,
      }),
    onError: (error) => toastApiError(error, 'AI 分析失败'),
  })

  const result = mutation.data?.data

  const toggle = (action: AnalysisAction) => {
    setSelected((prev) =>
      prev.includes(action) ? prev.filter((item) => item !== action) : [...prev, action],
    )
  }

  const has = (action: AnalysisAction) => selected.includes(action)

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader title="选择分析动作" description="可多选，一次请求并行执行。" />
        <CardBody className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {ACTIONS.map((action) => {
              const Icon = action.icon
              const active = selected.includes(action.key)
              return (
                <button
                  key={action.key}
                  type="button"
                  aria-pressed={active}
                  onClick={() => toggle(action.key)}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded border px-2 py-1 text-2xs font-medium transition-colors',
                    active
                      ? 'border-accent/50 bg-accent-soft text-accent'
                      : 'border-subtle bg-elevated text-muted hover:border-strong hover:text-primary',
                  )}
                >
                  <Icon className="size-3" />
                  {action.label}
                </button>
              )
            })}
          </div>

          <label className="flex items-center gap-2 text-2xs text-muted">
            <input
              type="checkbox"
              checked={saveNote}
              onChange={(event) => setSaveNote(event.target.checked)}
              className="size-3.5 accent-[rgb(var(--c-accent))]"
            />
            将结果保存为笔记
          </label>

          <Button
            variant="primary"
            size="sm"
            block
            loading={mutation.isPending}
            disabled={selected.length === 0}
            icon={<Wand2 className="size-3.5" />}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? '正在分析…' : `运行分析（${selected.length}）`}
          </Button>
        </CardBody>
      </Card>

      {mutation.isPending ? (
        <Card>
          <CardBody>
            <SkeletonText lines={5} />
          </CardBody>
        </Card>
      ) : mutation.isError ? (
        <InlineAlert tone="danger" title="分析失败">
          后端返回了错误，请稍后重试。详细错误已通过通知提示。
        </InlineAlert>
      ) : result ? (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5 text-2xs text-faint">
            {result.provider ? <Badge tone="neutral">{result.provider}</Badge> : null}
            {result.model ? <Badge tone="neutral">{result.model}</Badge> : null}
            {typeof result.latency_ms === 'number' ? <Badge tone="info">{formatMs(result.latency_ms)}</Badge> : null}
            {result.run_id ? (
              <a href={`/ai-runs/${result.run_id}`} className="text-accent hover:underline">
                查看运行轨迹
              </a>
            ) : null}
          </div>

          {result.note_ids && result.note_ids.length > 0 ? (
            <InlineAlert tone="success" title={`已保存 ${result.note_ids.length} 条笔记`}>
              在「笔记」中可查看 AI 生成的内容。
            </InlineAlert>
          ) : null}

          {has('summary') && (result.summary || result.key_points || result.keywords) ? (
            <Card>
              <CardHeader title="摘要" />
              <CardBody>
                <SummaryBlock data={result} />
              </CardBody>
            </Card>
          ) : null}

          {has('concepts') && result.concepts ? (
            <Card>
              <CardHeader title="核心概念" />
              <CardBody>
                <ConceptChips concepts={result.concepts} />
              </CardBody>
            </Card>
          ) : null}

          {has('terms') && result.terms ? (
            <Card>
              <CardHeader title="术语表" />
              <CardBody>
                <TermTable terms={result.terms} />
              </CardBody>
            </Card>
          ) : null}

          {has('todos') && result.todos ? (
            <Card>
              <CardHeader title="待办清单" />
              <CardBody>
                <TodoChecklist todos={result.todos} />
              </CardBody>
            </Card>
          ) : null}

          {has('study_plan') && result.study_plan ? (
            <Card>
              <CardHeader title="学习计划" />
              <CardBody>
                <StudyPlanList days={result.study_plan} />
              </CardBody>
            </Card>
          ) : null}

          {has('interview') && result.interview ? (
            <Card>
              <CardHeader title="面试题" />
              <CardBody>
                <InterviewAccordion items={result.interview} />
              </CardBody>
            </Card>
          ) : null}

          {has('cards') && result.cards ? (
            <Card>
              <CardHeader title="记忆卡" description="点击卡片翻面" />
              <CardBody>
                <FlashcardGrid cards={result.cards} />
              </CardBody>
            </Card>
          ) : null}

          {result.outline && result.outline.length > 0 ? (
            <Card>
              <CardHeader title="大纲" />
              <CardBody>
                <ol className="space-y-1">
                  {result.outline.map((item, index) => (
                    <li key={index} className="text-xs text-muted">
                      <span className="mr-1.5 font-mono text-2xs text-faint">{index + 1}.</span>
                      {item}
                    </li>
                  ))}
                </ol>
              </CardBody>
            </Card>
          ) : null}
        </div>
      ) : (
        <EmptyState
          compact
          icon={<Sparkles className="size-4" />}
          title="还没有分析结果"
          description="选择上方的动作并运行，结果会显示在这里。"
          className="border-0"
        />
      )}
    </div>
  )
}
