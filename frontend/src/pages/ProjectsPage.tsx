import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FolderKanban, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { confirm, Modal } from '@/components/ui/overlay'
import { Button, IconButton } from '@/components/ui/Button'
import { ColorPicker, Field, Input, Select, Textarea } from '@/components/ui/form'
import { Badge, Card, PageContainer, Pagination, SectionHeader } from '@/components/ui/primitives'
import { EmptyState, ErrorState, SkeletonCard } from '@/components/ui/feedback'
import { useToastStore, toastApiError } from '@/store/toast'
import type { Project, ProjectPayload } from '@/lib/types'

const PAGE_SIZE = 12

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'ACTIVE', label: '进行中' },
  { value: 'COMPLETED', label: '已完成' },
  { value: 'ARCHIVED', label: '已归档' },
]

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: '进行中',
  COMPLETED: '已完成',
  ARCHIVED: '已归档',
}

interface EditorState {
  open: boolean
  editing: Project | null
  name: string
  description: string
  color: string
  status: string
}

const EMPTY_EDITOR: EditorState = {
  open: false,
  editing: null,
  name: '',
  description: '',
  color: '#6366f1',
  status: 'ACTIVE',
}

export default function ProjectsPage() {
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR)

  const listQuery = useQuery({
    queryKey: ['projects', { page, search, status }],
    queryFn: ({ signal }) =>
      api.projects.list(
        { page, page_size: PAGE_SIZE, q: search || undefined, status: status || undefined },
        signal,
      ),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['projects'] })
    void queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
  }

  const createMutation = useMutation({
    mutationFn: (payload: ProjectPayload) => api.projects.create(payload),
    onSuccess: (result) => {
      success('项目已创建', result.data.name)
      invalidate()
    },
    onError: (error) => toastApiError(error, '创建项目失败'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<ProjectPayload> & { status?: string } }) =>
      api.projects.update(id, payload),
    onSuccess: () => invalidate(),
    onError: (error) => toastApiError(error, '更新项目失败'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.projects.remove(id),
    onSuccess: () => {
      success('项目已删除')
      invalidate()
    },
    onError: (error) => toastApiError(error, '删除项目失败'),
  })

  const submit = () => {
    const name = editor.name.trim()
    if (!name) return
    if (editor.editing) {
      updateMutation.mutate(
        {
          id: editor.editing.id,
          payload: { name, description: editor.description, color: editor.color, status: editor.status },
        },
        { onSuccess: () => setEditor(EMPTY_EDITOR) },
      )
    } else {
      createMutation.mutate(
        { name, description: editor.description, color: editor.color },
        { onSuccess: () => setEditor(EMPTY_EDITOR) },
      )
    }
  }

  const handleDelete = async (project: Project) => {
    const ok = await confirm({
      title: '删除项目',
      description: '项目下的知识库、文档、任务与笔记会级联删除，且无法恢复。',
      detail: project.name,
      confirmLabel: '删除',
      tone: 'danger',
    })
    if (ok) deleteMutation.mutate(project.id)
  }

  const items = listQuery.data?.items ?? []

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="项目"
        description="项目把知识库、任务、笔记与 AI 运行聚合成一个上下文边界。"
        actions={
          <Button
            variant="primary"
            size="sm"
            icon={<Plus className="size-3.5" />}
            onClick={() => setEditor({ ...EMPTY_EDITOR, open: true })}
          >
            新建项目
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
            placeholder="搜索项目…"
            className="pl-8"
            aria-label="搜索项目"
          />
        </div>
        <Select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value)
            setPage(1)
          }}
          className="w-auto min-w-[140px]"
          aria-label="按状态筛选"
        >
          {STATUS_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </div>

      {listQuery.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <SkeletonCard key={index} />
          ))}
        </div>
      ) : listQuery.isError ? (
        <ErrorState title="项目加载失败" error={listQuery.error} onRetry={() => void listQuery.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<FolderKanban className="size-4" />}
          title={search || status ? '没有匹配的项目' : '还没有项目'}
          description={
            search || status ? '换个关键词或清除筛选。' : '创建项目，把相关知识库与任务组织在一起。'
          }
          action={
            <Button
              variant="primary"
              size="sm"
              icon={<Plus className="size-3.5" />}
              onClick={() => setEditor({ ...EMPTY_EDITOR, open: true })}
            >
              新建项目
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {items.map((project) => (
              <Card key={project.id} className="flex flex-col overflow-hidden transition-colors hover:border-strong">
                <div className="h-0.5 w-full" style={{ backgroundColor: project.color }} />
                <div className="flex flex-1 flex-col gap-2 p-3.5">
                  <div className="flex items-start gap-2.5">
                    <span
                      className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border border-subtle"
                      style={{ backgroundColor: `${project.color}22`, color: project.color }}
                    >
                      <FolderKanban className="size-3.5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <Link
                        to={`/projects/${project.id}`}
                        className="block truncate text-sm font-medium text-primary hover:underline"
                      >
                        {project.name}
                      </Link>
                      <p className="mt-0.5 line-clamp-2 text-2xs text-muted">
                        {project.description || '暂无描述'}
                      </p>
                    </div>
                  </div>
                  <div className="mt-auto flex items-center gap-1.5">
                    <Badge tone={project.status === 'ACTIVE' ? 'accent' : 'neutral'}>
                      {STATUS_LABEL[project.status] ?? project.status}
                    </Badge>
                    <span className="text-2xs text-faint">创建于 {formatDate(project.created_at)}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 border-t border-subtle px-2.5 py-1.5">
                  <Link to={`/projects/${project.id}`}>
                    <Button size="xs" variant="ghost">
                      查看详情
                    </Button>
                  </Link>
                  <span className="ml-auto flex items-center gap-1">
                    <IconButton
                      label="编辑"
                      size="xs"
                      onClick={() =>
                        setEditor({
                          open: true,
                          editing: project,
                          name: project.name,
                          description: project.description,
                          color: project.color,
                          status: project.status,
                        })
                      }
                    >
                      <Pencil className="size-3" />
                    </IconButton>
                    <IconButton label="删除" size="xs" onClick={() => void handleDelete(project)}>
                      <Trash2 className="size-3 text-danger" />
                    </IconButton>
                  </span>
                </div>
              </Card>
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
        title={editor.editing ? '编辑项目' : '新建项目'}
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
              onClick={submit}
            >
              {editor.editing ? '保存' : '创建'}
            </Button>
          </>
        }
      >
        <div className="space-y-3.5">
          <Field label="名称" htmlFor="project-name" required>
            <Input
              id="project-name"
              value={editor.name}
              maxLength={160}
              autoFocus
              onChange={(event) => setEditor((prev) => ({ ...prev, name: event.target.value }))}
              placeholder="例如：毕业设计"
            />
          </Field>
          <Field label="描述" htmlFor="project-desc">
            <Textarea
              id="project-desc"
              rows={3}
              value={editor.description}
              maxLength={4000}
              onChange={(event) => setEditor((prev) => ({ ...prev, description: event.target.value }))}
            />
          </Field>
          {editor.editing ? (
            <Field label="状态" htmlFor="project-status">
              <Select
                id="project-status"
                value={editor.status}
                onChange={(event) => setEditor((prev) => ({ ...prev, status: event.target.value }))}
              >
                <option value="ACTIVE">进行中</option>
                <option value="COMPLETED">已完成</option>
                <option value="ARCHIVED">已归档</option>
              </Select>
            </Field>
          ) : null}
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
