import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Eye,
  FileText,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from 'lucide-react'
import { api } from '@/lib/api'
import { formatBytes, formatNumber, relativeTime } from '@/lib/utils'
import { Button, IconButton } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/form'
import { Modal, confirm } from '@/components/ui/overlay'
import { Badge, Card, PageContainer, Pagination, SectionHeader } from '@/components/ui/primitives'
import { EmptyState, ErrorState, ProgressBar, SkeletonTable } from '@/components/ui/feedback'
import { DocumentStatusBadge } from '@/components/badges'
import { DocumentUploader } from './DocumentUploader'
import { DocumentDrawer } from './DocumentDrawer'
import { useToastStore, toastApiError } from '@/store/toast'
import type { DocumentSummary } from '@/lib/types'

const PAGE_SIZE = 20
const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'PENDING', label: '排队中' },
  { value: 'PROCESSING', label: '处理中' },
  { value: 'COMPLETED', label: '已完成' },
  { value: 'FAILED', label: '失败' },
]

export interface DocumentsPanelProps {
  /** Fixes the knowledge base (knowledge-base scoped page). */
  knowledgeBaseId?: string
  projectId?: string
  heading?: string
  description?: string
  showHeader?: boolean
  /** Open a specific document drawer on mount (deep link `?doc=`). */
  initialDocumentId?: string | null
  /** Open the upload modal on mount (deep link `?upload=1`). */
  initialUploadOpen?: boolean
}

export function DocumentsPanel({
  knowledgeBaseId,
  projectId,
  heading = '文档',
  description,
  showHeader = true,
  initialDocumentId = null,
  initialUploadOpen = false,
}: DocumentsPanelProps) {
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [projectFilter, setProjectFilter] = useState(projectId ?? '')
  const [uploadOpen, setUploadOpen] = useState(initialUploadOpen)
  const [activeDocument, setActiveDocument] = useState<string | null>(initialDocumentId)

  useEffect(() => {
    setActiveDocument(initialDocumentId)
  }, [initialDocumentId])

  useEffect(() => {
    if (initialUploadOpen) setUploadOpen(true)
  }, [initialUploadOpen])

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    enabled: !knowledgeBaseId && !projectId,
    staleTime: 60_000,
  })

  const listQuery = useQuery({
    queryKey: ['documents', { knowledgeBaseId, projectFilter, status, search, page }],
    queryFn: ({ signal }) =>
      api.documents.list(
        {
          page,
          page_size: PAGE_SIZE,
          knowledge_base_id: knowledgeBaseId,
          project_id: projectFilter || undefined,
          status: status || undefined,
          q: search || undefined,
        },
        signal,
      ),
    // Poll only while something in the current page is still ingesting.
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? []
      const busy = items.some((doc) => doc.status === 'PENDING' || doc.status === 'PROCESSING')
      return busy ? 2000 : false
    },
  })

  const items = useMemo(() => listQuery.data?.items ?? [], [listQuery.data])
  const busyCount = useMemo(
    () => items.filter((doc) => doc.status === 'PENDING' || doc.status === 'PROCESSING').length,
    [items],
  )

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['documents'] })
    void queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
    void queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] })
  }

  const reprocess = useMutation({
    mutationFn: (id: string) => api.documents.reprocess(id),
    onSuccess: (result) => {
      success('已重新排队', result.message ?? '正在重新解析文档')
      invalidate()
    },
    onError: (error) => toastApiError(error, '重新处理失败'),
  })

  const remove = useMutation({
    mutationFn: (id: string) => api.documents.remove(id),
    onSuccess: () => {
      success('文档已删除')
      invalidate()
    },
    onError: (error) => toastApiError(error, '删除文档失败'),
  })

  const handleDelete = async (doc: DocumentSummary) => {
    const ok = await confirm({
      title: '删除文档',
      description: '文档、切片与对应向量都会被删除，且无法恢复。',
      detail: doc.title,
      confirmLabel: '删除',
      tone: 'danger',
    })
    if (ok) remove.mutate(doc.id)
  }

  return (
    <PageContainer className="space-y-4">
      {showHeader ? (
        <SectionHeader
          title={heading}
          description={description ?? '上传后会自动解析、切片并向量化；处理中的文档会自动刷新状态。'}
          actions={
            <Button
              variant="primary"
              size="sm"
              icon={<Upload className="size-3.5" />}
              onClick={() => setUploadOpen(true)}
            >
              上传文档
            </Button>
          }
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <Input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value)
              setPage(1)
            }}
            placeholder="按标题搜索文档…"
            className="pl-8"
            aria-label="搜索文档"
          />
        </div>
        <Select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value)
            setPage(1)
          }}
          className="w-auto min-w-[130px]"
          aria-label="按状态筛选"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        {!knowledgeBaseId && !projectId ? (
          <Select
            value={projectFilter}
            onChange={(event) => {
              setProjectFilter(event.target.value)
              setPage(1)
            }}
            className="w-auto min-w-[150px]"
            aria-label="按项目筛选"
          >
            <option value="">全部项目</option>
            {(projectsQuery.data?.items ?? []).map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </Select>
        ) : null}
        {busyCount > 0 ? <Badge tone="info">正在处理 {busyCount} 个</Badge> : null}
      </div>

      {listQuery.isLoading ? (
        <SkeletonTable rows={8} cols={5} />
      ) : listQuery.isError ? (
        <ErrorState title="文档加载失败" error={listQuery.error} onRetry={() => void listQuery.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<FileText className="size-4" />}
          title={search || status ? '没有匹配的文档' : '还没有文档'}
          description={
            search || status
              ? '换个关键词或清除筛选条件。'
              : '支持 Markdown、纯文本与 PDF，上传后会自动完成解析与向量化。'
          }
          action={
            <Button variant="primary" size="sm" icon={<Upload className="size-3.5" />} onClick={() => setUploadOpen(true)}>
              上传文档
            </Button>
          }
        />
      ) : (
        <>
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-xs">
                <thead className="border-b border-subtle bg-elevated text-2xs uppercase tracking-wide text-faint">
                  <tr>
                    <th className="px-3 py-2 font-medium">标题</th>
                    <th className="w-[150px] px-3 py-2 font-medium">状态</th>
                    <th className="w-[90px] px-3 py-2 text-right font-medium">切片</th>
                    <th className="w-[90px] px-3 py-2 text-right font-medium">大小</th>
                    <th className="w-[120px] px-3 py-2 font-medium">更新</th>
                    <th className="w-[110px] px-3 py-2 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((doc) => {
                    const busy = doc.status === 'PENDING' || doc.status === 'PROCESSING'
                    return (
                      <tr key={doc.id} className="border-b border-subtle last:border-b-0 hover:bg-elevated">
                        <td className="px-3 py-2">
                          <button
                            type="button"
                            onClick={() => setActiveDocument(doc.id)}
                            className="block max-w-[420px] truncate text-left text-xs font-medium text-primary hover:text-accent hover:underline"
                          >
                            {doc.title}
                          </button>
                          <p className="truncate text-2xs text-faint">{doc.original_filename}</p>
                          {busy ? (
                            <div className="mt-1 w-40">
                              <ProgressBar value={doc.progress} tone="info" />
                            </div>
                          ) : null}
                        </td>
                        <td className="px-3 py-2">
                          <DocumentStatusBadge status={doc.status} progress={doc.progress} />
                          {doc.status === 'FAILED' && doc.error_message ? (
                            <p className="mt-1 max-w-[180px] truncate text-2xs text-danger" title={doc.error_message}>
                              {doc.error_message}
                            </p>
                          ) : null}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-muted">
                          {formatNumber(doc.chunk_count)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-muted">
                          {formatBytes(doc.size_bytes)}
                        </td>
                        <td className="px-3 py-2 text-faint">{relativeTime(doc.updated_at)}</td>
                        <td className="px-3 py-2">
                          <div className="flex items-center justify-end gap-1">
                            <IconButton label="查看详情" size="xs" onClick={() => setActiveDocument(doc.id)}>
                              <Eye className="size-3" />
                            </IconButton>
                            <IconButton
                              label="重新处理"
                              size="xs"
                              disabled={reprocess.isPending}
                              onClick={() => reprocess.mutate(doc.id)}
                            >
                              <RefreshCw className="size-3" />
                            </IconButton>
                            <IconButton label="删除" size="xs" onClick={() => void handleDelete(doc)}>
                              <Trash2 className="size-3 text-danger" />
                            </IconButton>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <Pagination
            page={listQuery.data?.meta.page ?? 1}
            pageSize={listQuery.data?.meta.page_size ?? PAGE_SIZE}
            total={listQuery.data?.meta.total ?? 0}
            onPage={setPage}
          />
        </>
      )}

      {!knowledgeBaseId && !projectId ? (
        <p className="text-2xs text-faint">
          提示：在
          <Link to="/knowledge" className="mx-1 text-accent hover:underline">
            知识库
          </Link>
          页面进入某个知识库，可以只看该库的文档。
        </p>
      ) : null}

      <Modal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        title="上传文档"
        description="支持 .md / .markdown / .txt / .pdf，可一次拖入多个文件。"
        size="lg"
        footer={
          <Button variant="ghost" size="sm" onClick={() => setUploadOpen(false)}>
            完成
          </Button>
        }
      >
        <DocumentUploader
          knowledgeBaseId={knowledgeBaseId}
          projectId={projectId}
          onUploaded={invalidate}
        />
      </Modal>

      <DocumentDrawer
        documentId={activeDocument}
        open={Boolean(activeDocument)}
        onClose={() => setActiveDocument(null)}
        onDeleted={invalidate}
      />
    </PageContainer>
  )
}

/** Route wrapper that syncs `?doc=` with the drawer. */
export function useDocumentDeepLink(): [string | null, (id: string | null) => void] {
  const [params, setParams] = useSearchParams()
  const docId = params.get('doc')
  const setDocId = (id: string | null) => {
    const next = new URLSearchParams(params)
    if (id) next.set('doc', id)
    else next.delete('doc')
    setParams(next, { replace: true })
  }
  return [docId, setDocId]
}
