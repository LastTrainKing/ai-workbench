import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Library } from 'lucide-react'
import { api } from '@/lib/api'
import { formatBytes, formatNumber, relativeTime } from '@/lib/utils'
import { Badge, Card, CardBody, PageContainer, SectionHeader } from '@/components/ui/primitives'
import { ErrorState, Skeleton } from '@/components/ui/feedback'
import { DocumentsPanel } from '@/features/documents/DocumentsPanel'

export default function KnowledgeDocumentsPage() {
  const { kbId } = useParams<{ kbId: string }>()

  const kbQuery = useQuery({
    queryKey: ['knowledge-bases', kbId],
    queryFn: ({ signal }) => api.knowledgeBases.get(kbId as string, signal),
    enabled: Boolean(kbId),
  })

  if (!kbId) return null

  return (
    <PageContainer className="space-y-4">
      <Link
        to="/knowledge"
        className="inline-flex items-center gap-1.5 text-2xs text-muted transition-colors hover:text-primary"
      >
        <ArrowLeft className="size-3" />
        返回知识库列表
      </Link>

      {kbQuery.isLoading ? (
        <Skeleton className="h-20" />
      ) : kbQuery.isError ? (
        <ErrorState title="知识库加载失败" error={kbQuery.error} onRetry={() => void kbQuery.refetch()} />
      ) : kbQuery.data ? (
        <>
          <SectionHeader
            title={
              <span className="flex items-center gap-2">
                <span
                  className="flex size-6 items-center justify-center rounded-md border border-subtle"
                  style={{ backgroundColor: `${kbQuery.data.color}22`, color: kbQuery.data.color }}
                >
                  <Library className="size-3.5" />
                </span>
                {kbQuery.data.name}
              </span>
            }
            description={kbQuery.data.description || '该知识库下的全部文档'}
          />

          <Card>
            <CardBody className="flex flex-wrap items-center gap-x-6 gap-y-2">
              {[
                { label: '文档', value: formatNumber(kbQuery.data.stats.document_count) },
                { label: '切片', value: formatNumber(kbQuery.data.stats.chunk_count) },
                { label: '已向量化', value: formatNumber(kbQuery.data.stats.embedded_chunk_count) },
                { label: '字符', value: formatNumber(kbQuery.data.stats.total_chars) },
                { label: '存储', value: formatBytes(kbQuery.data.stats.total_size_bytes) },
              ].map((item) => (
                <div key={item.label}>
                  <p className="text-2xs uppercase tracking-wide text-faint">{item.label}</p>
                  <p className="text-sm font-semibold text-primary tabular-nums">{item.value}</p>
                </div>
              ))}
              <div className="ml-auto flex flex-wrap items-center gap-1.5">
                {kbQuery.data.is_archived ? <Badge tone="neutral">已归档</Badge> : null}
                {kbQuery.data.stats.processing_documents > 0 ? (
                  <Badge tone="info">处理中 {kbQuery.data.stats.processing_documents}</Badge>
                ) : null}
                {kbQuery.data.stats.failed_documents > 0 ? (
                  <Badge tone="danger">失败 {kbQuery.data.stats.failed_documents}</Badge>
                ) : null}
                <Badge tone="neutral" mono>
                  {kbQuery.data.embedding_model} · {kbQuery.data.vector_size}d
                </Badge>
                {kbQuery.data.stats.last_updated ? (
                  <span className="text-2xs text-faint">
                    更新于 {relativeTime(kbQuery.data.stats.last_updated)}
                  </span>
                ) : null}
              </div>
            </CardBody>
          </Card>
        </>
      ) : null}

      <DocumentsPanel
        knowledgeBaseId={kbId}
        showHeader={false}
        heading="文档"
        description=""
      />
    </PageContainer>
  )
}
