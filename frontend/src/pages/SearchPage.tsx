import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Database, FileText, Search, Sparkles, Zap } from 'lucide-react'
import { api } from '@/lib/api'
import { formatMs } from '@/lib/utils'
import { highlightTerms, queryTerms } from '@/lib/citations'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select } from '@/components/ui/form'
import {
  Badge,
  Card,
  CardBody,
  PageContainer,
  ScoreBar,
  SectionHeader,
  SegmentedControl,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, SkeletonText } from '@/components/ui/feedback'
import { DocumentDrawer } from '@/features/documents/DocumentDrawer'
import type { SearchMode } from '@/lib/types'

const MODES: { value: SearchMode; label: string; title: string }[] = [
  { value: 'keyword', label: '关键词', title: '仅使用稀疏关键词召回' },
  { value: 'semantic', label: '语义', title: '仅使用向量相似度召回' },
  { value: 'hybrid', label: '混合', title: '向量 + 关键词融合（推荐）' },
]

function HighlightedText({ text, terms }: { text: string; terms: string[] }) {
  const segments = useMemo(() => highlightTerms(text, terms), [text, terms])
  return (
    <>
      {segments.map((segment, index) =>
        segment.match ? (
          <mark key={index} className="rounded bg-accent-soft px-0.5 text-accent">
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </>
  )
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams()
  const initialQuery = params.get('q') ?? ''

  const [query, setQuery] = useState(initialQuery)
  const [submitted, setSubmitted] = useState(initialQuery)
  const [mode, setMode] = useState<SearchMode>('hybrid')
  const [kbId, setKbId] = useState('')
  const [limit, setLimit] = useState(20)
  const [activeDocument, setActiveDocument] = useState<string | null>(null)

  useEffect(() => {
    const next = params.get('q') ?? ''
    setQuery(next)
    setSubmitted(next)
  }, [params])

  const basesQuery = useQuery({
    queryKey: ['knowledge-bases', 'options'],
    queryFn: ({ signal }) => api.knowledgeBases.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })

  const searchQuery = useQuery({
    queryKey: ['search', { submitted, mode, kbId, limit }],
    queryFn: ({ signal }) =>
      api.search.run(
        {
          q: submitted,
          mode,
          knowledge_base_id: kbId || undefined,
          limit,
        },
        signal,
      ),
    enabled: submitted.trim().length > 0,
  })

  const terms = useMemo(() => queryTerms(submitted), [submitted])

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    const trimmed = query.trim()
    setSubmitted(trimmed)
    const next = new URLSearchParams(params)
    if (trimmed) next.set('q', trimmed)
    else next.delete('q')
    setParams(next, { replace: true })
  }

  const data = searchQuery.data
  const results = data?.results ?? []

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="检索"
        description="在知识库切片上执行关键词、语义或混合检索，并查看每一条命中分数的来源。"
      />

      <Card>
        <CardBody className="space-y-3">
          <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-2">
            <div className="relative min-w-[240px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="输入检索问题，例如：RAG 的分块策略是什么？"
                className="pl-8"
                aria-label="检索关键词"
              />
            </div>
            <Button type="submit" variant="primary" size="sm" icon={<Search className="size-3.5" />}>
              检索
            </Button>
          </form>

          <div className="flex flex-wrap items-end gap-3">
            <div>
              <p className="aiw-label">检索模式</p>
              <SegmentedControl value={mode} onChange={setMode} options={MODES} />
            </div>
            <Field label="知识库" className="min-w-[170px]">
              <Select value={kbId} onChange={(event) => setKbId(event.target.value)} aria-label="知识库范围">
                <option value="">全部知识库</option>
                {(basesQuery.data?.items ?? []).map((base) => (
                  <option key={base.id} value={base.id}>
                    {base.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="返回条数" className="w-[110px]">
              <Select
                value={String(limit)}
                onChange={(event) => setLimit(Number(event.target.value))}
                aria-label="返回条数"
              >
                {[10, 20, 30, 50].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </CardBody>
      </Card>

      {data ? (
        <div className="flex flex-wrap items-center gap-2 text-2xs text-faint">
          <Badge tone="accent" icon={<Zap className="size-3" />}>
            {formatMs(data.took_ms)}
          </Badge>
          <Badge tone="neutral">模式 {data.mode}</Badge>
          <Badge tone={data.cached ? 'info' : 'neutral'}>{data.cached ? '命中缓存' : '未缓存'}</Badge>
          {data.reranked ? <Badge tone="accent">已重排</Badge> : null}
          <Badge tone="neutral">稠密命中 {data.dense_hits}</Badge>
          <Badge tone="neutral">稀疏命中 {data.sparse_hits}</Badge>
          <span className="ml-auto">{results.length} 条结果</span>
        </div>
      ) : null}

      {searchQuery.isFetching ? (
        <div className="space-y-2.5">
          {Array.from({ length: 4 }).map((_, index) => (
            <Card key={index}>
              <CardBody>
                <SkeletonText lines={3} />
              </CardBody>
            </Card>
          ))}
        </div>
      ) : searchQuery.isError ? (
        <ErrorState title="检索失败" error={searchQuery.error} onRetry={() => void searchQuery.refetch()} />
      ) : !submitted.trim() ? (
        <EmptyState
          icon={<Search className="size-4" />}
          title="输入关键词开始检索"
          description="混合模式会融合向量相似度与关键词命中，并在下方展示两条腿各自的分数。"
        />
      ) : results.length === 0 ? (
        <EmptyState
          icon={<Database className="size-4" />}
          title="没有找到匹配的切片"
          description="尝试更换关键词、切换到混合模式，或确认文档已经完成向量化。"
        />
      ) : (
        <ul className="space-y-2.5">
          {results.map((hit) => (
            <li key={hit.chunk_id}>
              <Card className="transition-colors hover:border-strong">
                <CardBody className="space-y-2.5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <button
                        type="button"
                        onClick={() => setActiveDocument(hit.document_id)}
                        className="flex items-center gap-1.5 text-xs font-medium text-primary hover:text-accent hover:underline"
                      >
                        <FileText className="size-3.5 shrink-0 text-faint" />
                        <span className="truncate">{hit.document_title}</span>
                      </button>
                      <p className="mt-0.5 flex flex-wrap items-center gap-1.5 font-mono text-2xs text-faint">
                        <span>chunk #{hit.chunk_index}</span>
                        {hit.heading ? <span>· {hit.heading}</span> : null}
                      </p>
                    </div>
                    <Badge tone="accent" mono>
                      {hit.score.toFixed(4)}
                    </Badge>
                  </div>

                  <p className="max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-muted">
                    <HighlightedText text={hit.content} terms={terms} />
                  </p>

                  <div className="space-y-1 rounded-md border border-subtle bg-inset px-2.5 py-2">
                    <ScoreBar label="向量" value={hit.vector_score} tone="accent" />
                    <ScoreBar label="关键词" value={hit.keyword_score} tone="info" />
                  </div>
                </CardBody>
              </Card>
            </li>
          ))}
        </ul>
      )}

      <DocumentDrawer
        documentId={activeDocument}
        open={Boolean(activeDocument)}
        onClose={() => setActiveDocument(null)}
      />

      {data && results.length > 0 ? (
        <p className="flex items-center gap-1.5 text-2xs text-faint">
          <Sparkles className="size-3" />
          点击文档标题可打开该文档的切片与原文。
        </p>
      ) : null}
    </PageContainer>
  )
}
