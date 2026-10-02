import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueries, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  ArchiveRestore,
  FileText,
  Layers,
  Library,
  Pencil,
  Plus,
  Search,
  Trash2,
} from 'lucide-react'
import { api } from '@/lib/api'
import { formatBytes, formatNumber, relativeTime } from '@/lib/utils'
import { confirm, Modal } from '@/components/ui/overlay'
import { Button, IconButton } from '@/components/ui/Button'
import { ColorPicker, Field, Input, Select, Textarea } from '@/components/ui/form'
import {
  Badge,
  Card,
  PageContainer,
  Pagination,
  SectionHeader,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, SkeletonCard } from '@/components/ui/feedback'
import { useToastStore, toastApiError } from '@/store/toast'
import type { KnowledgeBase, KnowledgeBaseDetail, KnowledgeBasePayload } from '@/lib/types'

const PAGE_SIZE = 12

interface EditorState {
  open: boolean
  editing: KnowledgeBase | null
  name: string
  description: string
  icon: string
  color: string
  project_id: string
}

const EMPTY_EDITOR: EditorState = {
  open: false,
  editing: null,
  name: '',
  description: '',
  icon: 'book',
  color: '#8b5cf6',
  project_id: '',
}

function KnowledgeBaseCard({
  base,
  detail,
  onEdit,
  onDelete,
  onToggleArchive,
}: {
  base: KnowledgeBase
  detail: KnowledgeBaseDetail | undefined
  onEdit: () => void
  onDelete: () => void
  onToggleArchive: () => void
}) {
  const stats = detail?.stats
  const embeddedRatio =
    stats && stats.chunk_count > 0 ? stats.embedded_chunk_count / stats.chunk_count : 0

  return (
    <Card className="flex flex-col overflow-hidden transition-colors hover:border-strong">
      <div className="h-0.5 w-full" style={{ backgroundColor: base.color }} />
      <div className="flex flex-1 flex-col gap-3 p-3.5">
        <div className="flex items-start gap-2.5">
          <span
            className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border border-subtle"
            style={{ backgroundColor: `${base.color}22`, color: base.color }}
          >
            <Library className="size-3.5" />
          </span>
          <div className="min-w-0 flex-1">
            <Link
              to={`/knowledge/${base.id}/documents`}
              className="block truncate text-sm font-medium text-primary hover:underline"
            >
              {base.name}
            </Link>
            <p className="mt-0.5 line-clamp-2 text-2xs text-muted">
              {base.description || '暂无描述'}
            </p>
          </div>
          {base.is_archived ? <Badge tone="neutral">已归档</Badge> : null}
        </div>

        <div className="grid grid-cols-2 gap-1.5">
          <div className="rounded border border-subtle bg-elevated px-2 py-1.5">
            <p className="text-2xs text-faint">文档</p>
            <p className="text-xs font-semibold text-primary tabular-nums">
              {stats ? formatNumber(stats.document_count) : '—'}
            </p>
          </div>
          <div className="rounded border border-subtle bg-elevated px-2 py-1.5">
            <p className="text-2xs text-faint">切片</p>
            <p className="text-xs font-semibold text-primary tabular-nums">
              {stats ? formatNumber(stats.chunk_count) : '—'}
            </p>
          </div>
        </div>

        <div className="space-y-1">
          <div className="flex items-center justify-between text-2xs text-faint">
            <span>已向量化</span>
            <span className="tabular-nums">
              {stats ? `${formatNumber(stats.embedded_chunk_count)}/${formatNumber(stats.chunk_count)}` : '—'}
            </span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-elevated">
            <div
              className="h-full rounded-full bg-accent transition-[width]"
              style={{ width: `${Math.round(embeddedRatio * 100)}%` }}
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1 text-2xs text-faint">
          <span className="tabular-nums">{stats ? formatBytes(stats.total_size_bytes) : '—'}</span>
          {stats && stats.processing_documents > 0 ? (
            <Badge tone="info">处理中 {stats.processing_documents}</Badge>
          ) : null}
          {stats && stats.failed_documents > 0 ? (
            <Badge tone="danger">失败 {stats.failed_documents}</Badge>
          ) : null}
          <span className="ml-auto">
            {stats?.last_updated ? relativeTime(stats.last_updated) : base.embedding_model}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-1 border-t border-subtle px-2.5 py-1.5">
        <Link to={`/knowledge/${base.id}/documents`}>
          <Button size="xs" variant="ghost" icon={<FileText className="size-3" />}>
            文档
          </Button>
        </Link>
        <span className="ml-auto flex items-center gap-1">
          <IconButton label="编辑" size="xs" onClick={onEdit}>
            <Pencil className="size-3" />
          </IconButton>
          <IconButton
            label={base.is_archived ? '取消归档' : '归档'}
            size="xs"
            onClick={onToggleArchive}
          >
            {base.is_archived ? <ArchiveRestore className="size-3" /> : <Archive className="size-3" />}
          </IconButton>
          <IconButton label="删除" size="xs" onClick={onDelete}>
            <Trash2 className="size-3 text-danger" />
          </IconButton>
        </span>
      </div>
    </Card>
  )
}

export default function KnowledgePage() {
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [projectFilter, setProjectFilter] = useState('')
  const [includeArchived, setIncludeArchived] = useState(false)
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR)

  const listQuery = useQuery({
    queryKey: ['knowledge-bases', { page, search, projectFilter, includeArchived }],
    queryFn: ({ signal }) =>
      api.knowledgeBases.list(
        {
          page,
          page_size: PAGE_SIZE,
          q: search || undefined,
          project_id: projectFilter || undefined,
          include_archived: includeArchived,
        },
        signal,
      ),
  })

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })

  const bases = useMemo(() => listQuery.data?.items ?? [], [listQuery.data])

  const detailQueries = useQueries({
    queries: bases.map((base) => ({
      queryKey: ['knowledge-bases', base.id],
      queryFn: ({ signal }: { signal: AbortSignal }) => api.knowledgeBases.get(base.id, signal),
      staleTime: 20_000,
    })),
  })

  const detailsById = useMemo(() => {
    const map = new Map<string, KnowledgeBaseDetail>()
    bases.forEach((base, index) => {
      const result = detailQueries[index]
      if (result?.data) map.set(base.id, result.data)
    })
    return map
  }, [bases, detailQueries])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['knowledge-bases'] })
    void queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
  }

  const createMutation = useMutation({
    mutationFn: (payload: KnowledgeBasePayload) => api.knowledgeBases.create(payload),
    onSuccess: (result) => {
      success('知识库已创建', result.data.name)
      invalidate()
    },
    onError: (error) => toastApiError(error, '创建知识库失败'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<KnowledgeBasePayload> }) =>
      api.knowledgeBases.update(id, payload),
    onSuccess: () => invalidate(),
    onError: (error) => toastApiError(error, '更新知识库失败'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.knowledgeBases.remove(id),
    onSuccess: () => {
      success('知识库已删除')
      invalidate()
    },
    onError: (error) => toastApiError(error, '删除知识库失败'),
  })

  const openCreate = () => setEditor({ ...EMPTY_EDITOR, open: true, project_id: projectFilter })

  const openEdit = (base: KnowledgeBase) =>
    setEditor({
      open: true,
      editing: base,
      name: base.name,
      description: base.description,
      icon: base.icon,
      color: base.color,
      project_id: base.project_id ?? '',
    })

  const submitEditor = () => {
    const name = editor.name.trim()
    if (!name) return
    const payload: KnowledgeBasePayload = {
      name,
      description: editor.description,
      icon: editor.icon || 'book',
      color: editor.color,
      project_id: editor.project_id || null,
    }
    if (editor.editing) {
      updateMutation.mutate(
        { id: editor.editing.id, payload },
        { onSuccess: () => setEditor(EMPTY_EDITOR) },
      )
    } else {
      createMutation.mutate(payload, { onSuccess: () => setEditor(EMPTY_EDITOR) })
    }
  }

  const handleDelete = async (base: KnowledgeBase) => {
    const ok = await confirm({
      title: '删除知识库',
      description: '该知识库下的所有文档、切片与向量都会被一并删除，且无法恢复。',
      detail: base.name,
      confirmLabel: '删除',
      tone: 'danger',
    })
    if (ok) deleteMutation.mutate(base.id)
  }

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="知识库"
        description="每个知识库是一组独立向量化的文档集合，供检索、问答与规划使用。"
        actions={
          <Button variant="primary" size="sm" icon={<Plus className="size-3.5" />} onClick={openCreate}>
            新建知识库
          </Button>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <Input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value)
              setPage(1)
            }}
            placeholder="按名称搜索知识库…"
            className="pl-8"
            aria-label="搜索知识库"
          />
        </div>
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
        <Button
          size="sm"
          variant={includeArchived ? 'primary' : 'secondary'}
          onClick={() => {
            setIncludeArchived((value) => !value)
            setPage(1)
          }}
        >
          含已归档
        </Button>
      </div>

      {listQuery.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <SkeletonCard key={index} />
          ))}
        </div>
      ) : listQuery.isError ? (
        <ErrorState
          title="知识库加载失败"
          error={listQuery.error}
          onRetry={() => void listQuery.refetch()}
        />
      ) : bases.length === 0 ? (
        <EmptyState
          icon={<Layers className="size-4" />}
          title={search || projectFilter ? '没有匹配的知识库' : '还没有知识库'}
          description={
            search || projectFilter
              ? '换个关键词，或清除筛选条件。'
              : '创建第一个知识库，然后上传 Markdown / TXT / PDF 文档开始向量化。'
          }
          action={
            <Button variant="primary" size="sm" icon={<Plus className="size-3.5" />} onClick={openCreate}>
              新建知识库
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {bases.map((base) => (
              <KnowledgeBaseCard
                key={base.id}
                base={base}
                detail={detailsById.get(base.id)}
                onEdit={() => openEdit(base)}
                onDelete={() => void handleDelete(base)}
                onToggleArchive={() =>
                  updateMutation.mutate({
                    id: base.id,
                    payload: { is_archived: !base.is_archived },
                  })
                }
              />
            ))}
          </div>
          <Pagination
            page={listQuery.data?.meta.page ?? 1}
            pageSize={listQuery.data?.meta.page_size ?? PAGE_SIZE}
            total={listQuery.data?.meta.total ?? 0}
            onPage={setPage}
          />
        </>
      )}

      <Modal
        open={editor.open}
        onClose={() => setEditor(EMPTY_EDITOR)}
        title={editor.editing ? '编辑知识库' : '新建知识库'}
        description="名称与描述会显示在卡片与选择器中。"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setEditor(EMPTY_EDITOR)}>
              取消
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={createMutation.isPending || updateMutation.isPending}
              disabled={!editor.name.trim()}
              onClick={submitEditor}
            >
              {editor.editing ? '保存' : '创建'}
            </Button>
          </>
        }
      >
        <div className="space-y-3.5">
          <Field label="名称" htmlFor="kb-name" required>
            <Input
              id="kb-name"
              value={editor.name}
              maxLength={160}
              onChange={(event) => setEditor((prev) => ({ ...prev, name: event.target.value }))}
              placeholder="例如：分布式系统课程资料"
            />
          </Field>
          <Field label="描述" htmlFor="kb-desc">
            <Textarea
              id="kb-desc"
              rows={3}
              value={editor.description}
              maxLength={4000}
              onChange={(event) => setEditor((prev) => ({ ...prev, description: event.target.value }))}
              placeholder="这个知识库包含哪些资料？"
            />
          </Field>
          <div className="grid gap-3.5 sm:grid-cols-2">
            <Field label="图标标识" htmlFor="kb-icon" hint="后端存储的图标名，例如 book / flask">
              <Input
                id="kb-icon"
                value={editor.icon}
                maxLength={64}
                onChange={(event) => setEditor((prev) => ({ ...prev, icon: event.target.value }))}
              />
            </Field>
            <Field label="所属项目" htmlFor="kb-project">
              <Select
                id="kb-project"
                value={editor.project_id}
                onChange={(event) => setEditor((prev) => ({ ...prev, project_id: event.target.value }))}
              >
                <option value="">不关联项目</option>
                {(projectsQuery.data?.items ?? []).map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="主题色">
            <ColorPicker
              value={editor.color}
              onChange={(color) => setEditor((prev) => ({ ...prev, color }))}
            />
          </Field>
        </div>
      </Modal>
    </PageContainer>
  )
}
