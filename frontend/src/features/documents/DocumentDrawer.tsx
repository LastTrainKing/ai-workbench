import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Copy, FileText, Layers, RefreshCw, Sparkles, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatBytes, formatDateTime, formatNumber, relativeTime } from '@/lib/utils'
import { Drawer, confirm } from '@/components/ui/overlay'
import { Button, IconButton } from '@/components/ui/Button'
import { Badge, KeyValue, Tabs } from '@/components/ui/primitives'
import { EmptyState, ErrorState, InlineAlert, ProgressBar, Skeleton, SkeletonText } from '@/components/ui/feedback'
import { DocumentStatusBadge, TagChip } from '@/components/badges'
import { AnalyzePanel } from './AnalyzePanel'
import { useToastStore, toastApiError } from '@/store/toast'

type TabKey = 'overview' | 'chunks' | 'content' | 'analysis'

const CHUNK_PAGE_SIZE = 20

function OverviewTab({ documentId }: { documentId: string }) {
  const detail = useQuery({
    queryKey: ['documents', documentId],
    queryFn: ({ signal }) => api.documents.get(documentId, signal),
  })
  const embedding = useQuery({
    queryKey: ['documents', documentId, 'embedding-status'],
    queryFn: ({ signal }) => api.documents.embeddingStatus(documentId, signal),
  })

  if (detail.isLoading) return <SkeletonText lines={8} />
  if (detail.isError) return <ErrorState compact error={detail.error} onRetry={() => void detail.refetch()} />
  const doc = detail.data
  if (!doc) return null

  const busy = doc.status === 'PENDING' || doc.status === 'PROCESSING'

  return (
    <div className="space-y-4">
      {busy ? (
        <div className="rounded-md border border-subtle bg-surface px-3 py-2.5">
          <div className="mb-1.5 flex items-center justify-between text-2xs text-muted">
            <span>正在处理：{doc.job_stage || doc.status}</span>
            <span className="tabular-nums">{doc.progress}%</span>
          </div>
          <ProgressBar value={doc.progress} tone="info" />
        </div>
      ) : null}

      {doc.status === 'FAILED' ? (
        <InlineAlert tone="danger" title="解析失败">
          {doc.error_message || doc.job_error || '后端未提供失败原因'}
        </InlineAlert>
      ) : null}

      <div>
        <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">基本信息</p>
        <KeyValue
          columns={2}
          items={[
            { key: '标题', value: doc.title },
            { key: '原始文件名', value: doc.original_filename },
            { key: '类型', value: `${doc.mime_type || '—'} (${doc.file_ext || '—'})` },
            { key: '大小', value: formatBytes(doc.size_bytes) },
            { key: '页数', value: doc.page_count ? formatNumber(doc.page_count) : '—' },
            { key: '语言', value: doc.language || '—' },
            { key: '字符数', value: formatNumber(doc.char_count) },
            { key: '切片数', value: formatNumber(doc.chunk_count) },
            { key: '已向量化切片', value: formatNumber(doc.embedded_chunk_count) },
            { key: '创建时间', value: formatDateTime(doc.created_at) },
            { key: '更新时间', value: formatDateTime(doc.updated_at) },
            { key: '处理完成', value: doc.processed_at ? formatDateTime(doc.processed_at) : '—' },
            { key: '任务状态', value: doc.job_status || '—' },
            { key: '任务阶段', value: doc.job_stage || '—' },
          ]}
        />
      </div>

      {doc.tags.length > 0 ? (
        <div>
          <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">标签</p>
          <div className="flex flex-wrap gap-1">
            {doc.tags.map((tag) => (
              <TagChip key={tag.id} name={tag.name} color={tag.color} />
            ))}
          </div>
        </div>
      ) : null}

      {doc.summary ? (
        <div>
          <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">文档摘要</p>
          <p className="rounded-md border border-subtle bg-inset px-2.5 py-2 text-xs text-muted">
            {doc.summary}
          </p>
        </div>
      ) : null}

      <div>
        <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">向量化状态</p>
        {embedding.isLoading ? (
          <Skeleton className="h-10" />
        ) : embedding.data ? (
          <div className="space-y-2 rounded-md border border-subtle bg-surface px-2.5 py-2">
            <ProgressBar
              value={embedding.data.chunks_total === 0 ? 0 : embedding.data.chunks_embedded}
              max={Math.max(1, embedding.data.chunks_total)}
              tone={embedding.data.chunks_failed > 0 ? 'warn' : 'success'}
              showLabel
            />
            <div className="flex flex-wrap gap-1.5">
              <Badge tone="success">已嵌入 {embedding.data.chunks_embedded}</Badge>
              <Badge tone="neutral">总计 {embedding.data.chunks_total}</Badge>
              {embedding.data.chunks_failed > 0 ? (
                <Badge tone="danger">失败 {embedding.data.chunks_failed}</Badge>
              ) : null}
              {embedding.data.embedding_model ? (
                <Badge tone="info" mono>
                  {embedding.data.embedding_model}
                </Badge>
              ) : null}
            </div>
          </div>
        ) : (
          <p className="text-2xs text-faint">暂无数据</p>
        )}
      </div>

      {Object.keys(doc.doc_metadata ?? {}).length > 0 ? (
        <div>
          <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">元数据</p>
          <KeyValue
            columns={2}
            items={Object.entries(doc.doc_metadata).map(([key, value]) => ({
              key,
              value: typeof value === 'object' ? JSON.stringify(value) : String(value),
            }))}
          />
        </div>
      ) : null}
    </div>
  )
}

function ChunksTab({ documentId }: { documentId: string }) {
  const [page, setPage] = useState(1)
  const chunks = useQuery({
    queryKey: ['documents', documentId, 'chunks', page],
    queryFn: ({ signal }) =>
      api.documents.chunks(documentId, { page, page_size: CHUNK_PAGE_SIZE }, signal),
  })

  if (chunks.isLoading) return <SkeletonText lines={8} />
  if (chunks.isError) return <ErrorState compact error={chunks.error} onRetry={() => void chunks.refetch()} />

  const items = chunks.data?.items ?? []
  if (items.length === 0) {
    return (
      <EmptyState
        compact
        icon={<Layers className="size-4" />}
        title="还没有切片"
        description="文档完成解析与向量化后，切片会显示在这里。"
        className="border-0"
      />
    )
  }

  return (
    <div className="space-y-2">
      {items.map((chunk) => (
        <div key={chunk.id} className="rounded-md border border-subtle bg-surface">
          <div className="flex flex-wrap items-center gap-2 border-b border-subtle px-2.5 py-1.5">
            <span className="font-mono text-2xs text-faint">#{chunk.chunk_index}</span>
            {chunk.heading ? (
              <span className="min-w-0 flex-1 truncate text-2xs font-medium text-primary">
                {chunk.heading}
              </span>
            ) : (
              <span className="flex-1" />
            )}
            <Badge
              tone={
                chunk.embedding_status === 'EMBEDDED'
                  ? 'success'
                  : chunk.embedding_status === 'FAILED'
                    ? 'danger'
                    : 'warn'
              }
            >
              {chunk.embedding_status}
            </Badge>
            <span className="text-2xs text-faint tabular-nums">{chunk.char_count} 字符</span>
          </div>
          <p className="max-h-56 overflow-y-auto whitespace-pre-wrap px-2.5 py-2 text-xs leading-relaxed text-muted">
            {chunk.content}
          </p>
        </div>
      ))}

      <div className="flex items-center justify-between pt-1">
        <span className="text-2xs text-faint tabular-nums">
          共 {chunks.data?.meta.total ?? 0} 个切片
        </span>
        <div className="flex gap-1.5">
          <Button size="xs" variant="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            上一页
          </Button>
          <Button
            size="xs"
            variant="ghost"
            disabled={!chunks.data?.meta.has_next}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
    </div>
  )
}

function ContentTab({ documentId }: { documentId: string }) {
  const [copied, setCopied] = useState(false)
  const content = useQuery({
    queryKey: ['documents', documentId, 'content'],
    queryFn: ({ signal }) => api.documents.content(documentId, 40000, signal),
  })

  if (content.isLoading) return <SkeletonText lines={10} />
  if (content.isError) return <ErrorState compact error={content.error} onRetry={() => void content.refetch()} />

  const data = content.data
  if (!data) return null

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-2xs text-faint">
          {formatNumber(data.char_count)} 字符{data.truncated ? '（已截断）' : ''}
        </span>
        <Button
          size="xs"
          variant="ghost"
          icon={<Copy className="size-3" />}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(data.content)
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1500)
            } catch {
              /* clipboard blocked */
            }
          }}
        >
          {copied ? '已复制' : '复制全文'}
        </Button>
      </div>
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md border border-subtle bg-inset px-3 py-2.5 text-xs leading-relaxed text-muted">
        {data.content || '（没有提取到文本内容）'}
      </pre>
    </div>
  )
}

export interface DocumentDrawerProps {
  documentId: string | null
  open: boolean
  onClose: () => void
  onDeleted?: () => void
}

export function DocumentDrawer({ documentId, open, onClose, onDeleted }: DocumentDrawerProps) {
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)
  const [tab, setTab] = useState<TabKey>('overview')

  useEffect(() => {
    if (open) setTab('overview')
  }, [open, documentId])

  const detail = useQuery({
    queryKey: ['documents', documentId],
    queryFn: ({ signal }) => api.documents.get(documentId as string, signal),
    enabled: Boolean(documentId) && open,
    // Poll only while ingestion is still running.
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'PENDING' || status === 'PROCESSING' ? 1500 : false
    },
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
    void queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
    void queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] })
  }

  const reprocess = useMutation({
    mutationFn: () => api.documents.reprocess(documentId as string),
    onSuccess: (result) => {
      success('已重新排队', result.message ?? '正在重新解析文档')
      invalidate()
    },
    onError: (error) => toastApiError(error, '重新处理失败'),
  })

  const remove = useMutation({
    mutationFn: () => api.documents.remove(documentId as string),
    onSuccess: () => {
      success('文档已删除')
      invalidate()
      onDeleted?.()
      onClose()
    },
    onError: (error) => toastApiError(error, '删除文档失败'),
  })

  const doc = detail.data

  const handleDelete = async () => {
    const ok = await confirm({
      title: '删除文档',
      description: '文档、切片与对应向量都会被删除，且无法恢复。',
      detail: doc?.title,
      confirmLabel: '删除',
      tone: 'danger',
    })
    if (ok) remove.mutate()
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={doc?.title ?? '文档详情'}
      subtitle={
        doc ? (
          <span className="flex flex-wrap items-center gap-1.5">
            <DocumentStatusBadge status={doc.status} progress={doc.progress} />
            <span className="text-2xs text-faint">
              {formatNumber(doc.chunk_count)} 切片 · {formatBytes(doc.size_bytes)} ·{' '}
              {relativeTime(doc.updated_at)}
            </span>
          </span>
        ) : null
      }
      headerActions={
        <>
          <IconButton
            label="重新处理"
            size="sm"
            disabled={!doc || reprocess.isPending}
            onClick={() => reprocess.mutate()}
          >
            <RefreshCw className={cn('size-3.5', reprocess.isPending && 'animate-spin')} />
          </IconButton>
          <IconButton label="删除文档" size="sm" disabled={!doc} onClick={() => void handleDelete()}>
            <Trash2 className="size-3.5 text-danger" />
          </IconButton>
        </>
      }
      footer={
        doc ? (
          <>
            <span className="text-2xs text-faint">
              创建于 {formatDateTime(doc.created_at)}
            </span>
            <Button size="sm" variant="ghost" onClick={onClose}>
              关闭
            </Button>
          </>
        ) : null
      }
    >
      {!documentId ? null : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : (
        <>
          <Tabs
            className="mb-3"
            value={tab}
            onChange={(key) => setTab(key as TabKey)}
            items={[
              { key: 'overview', label: '概览', icon: <FileText className="size-3" /> },
              { key: 'chunks', label: '切片', icon: <Layers className="size-3" />, count: doc?.chunk_count },
              { key: 'content', label: '原文', icon: <AlertCircle className="size-3" /> },
              { key: 'analysis', label: 'AI 分析', icon: <Sparkles className="size-3" /> },
            ]}
          />

          {tab === 'overview' ? <OverviewTab documentId={documentId} /> : null}
          {tab === 'chunks' ? <ChunksTab documentId={documentId} /> : null}
          {tab === 'content' ? <ContentTab documentId={documentId} /> : null}
          {tab === 'analysis' ? (
            <AnalyzePanel documentId={documentId} projectId={doc?.project_id ?? null} />
          ) : null}
        </>
      )}
    </Drawer>
  )
}
