import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Columns2,
  Eye,
  NotebookPen,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, relativeTime } from '@/lib/utils'
import { Button, IconButton } from '@/components/ui/Button'
import { Input, Select, Textarea } from '@/components/ui/form'
import { confirm } from '@/components/ui/overlay'
import { Badge, Card, PageContainer, Pagination, SectionHeader, SegmentedControl } from '@/components/ui/primitives'
import { EmptyState, ErrorState, Skeleton, SkeletonText } from '@/components/ui/feedback'
import { Markdown } from '@/components/Markdown'
import { useToastStore, toastApiError } from '@/store/toast'
import type { Note } from '@/lib/types'

const PAGE_SIZE = 30
type EditorMode = 'edit' | 'split' | 'preview'

export default function NotesPage() {
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)

  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [projectFilter, setProjectFilter] = useState('')
  const [activeId, setActiveId] = useState<string | null>(params.get('note'))
  const [mode, setMode] = useState<EditorMode>('split')
  const [isNew, setIsNew] = useState(false)

  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [pinned, setPinned] = useState(false)
  const [projectId, setProjectId] = useState('')
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    const noteParam = params.get('note')
    if (noteParam) setActiveId(noteParam)
  }, [params])

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })

  const listQuery = useQuery({
    queryKey: ['notes', { page, search, projectFilter }],
    queryFn: ({ signal }) =>
      api.notes.list(
        {
          page,
          page_size: PAGE_SIZE,
          q: search || undefined,
          project_id: projectFilter || undefined,
        },
        signal,
      ),
  })

  const detailQuery = useQuery({
    queryKey: ['notes', activeId],
    queryFn: ({ signal }) => api.notes.get(activeId as string, signal),
    enabled: Boolean(activeId) && !isNew,
  })

  useEffect(() => {
    if (isNew || !detailQuery.data) return
    const note = detailQuery.data
    setTitle(note.title)
    setContent(note.content_md)
    setPinned(note.is_pinned)
    setProjectId(note.project_id ?? '')
    setDirty(false)
  }, [detailQuery.data, isNew])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['notes'] })
    void queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
  }

  const createMutation = useMutation({
    mutationFn: () =>
      api.notes.create({
        title: title.trim() || '未命名笔记',
        content_md: content,
        project_id: projectId || null,
        is_pinned: pinned,
      }),
    onSuccess: (result) => {
      success('笔记已创建')
      invalidate()
      setIsNew(false)
      setActiveId(result.data.id)
      setDirty(false)
    },
    onError: (error) => toastApiError(error, '创建笔记失败'),
  })

  const updateMutation = useMutation({
    mutationFn: () =>
      api.notes.update(activeId as string, {
        title: title.trim() || '未命名笔记',
        content_md: content,
        is_pinned: pinned,
        project_id: projectId || null,
      }),
    onSuccess: () => {
      success('笔记已保存')
      invalidate()
      setDirty(false)
    },
    onError: (error) => toastApiError(error, '保存笔记失败'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.notes.remove(id),
    onSuccess: () => {
      success('笔记已删除')
      invalidate()
      startNew()
    },
    onError: (error) => toastApiError(error, '删除笔记失败'),
  })

  const startNew = () => {
    setIsNew(true)
    setActiveId(null)
    setTitle('')
    setContent('')
    setPinned(false)
    setProjectId(projectFilter)
    setDirty(false)
    const next = new URLSearchParams(params)
    next.delete('note')
    setParams(next, { replace: true })
  }

  const selectNote = (id: string) => {
    setIsNew(false)
    setActiveId(id)
    const next = new URLSearchParams(params)
    next.set('note', id)
    setParams(next, { replace: true })
  }

  const save = () => {
    if (isNew || !activeId) createMutation.mutate()
    else updateMutation.mutate()
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        if (!activeId && !isNew) return
        event.preventDefault()
        save()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, isNew, title, content, pinned, projectId])

  const handleDelete = async (note: Note) => {
    const ok = await confirm({
      title: '删除笔记',
      description: '删除后无法恢复。',
      detail: note.title,
      confirmLabel: '删除',
      tone: 'danger',
    })
    if (ok) deleteMutation.mutate(note.id)
  }

  const notes = useMemo(() => listQuery.data?.items ?? [], [listQuery.data])
  const sortedNotes = useMemo(
    () => [...notes].sort((a, b) => Number(b.is_pinned) - Number(a.is_pinned)),
    [notes],
  )

  const activeNote = detailQuery.data
  const editorOpen = isNew || Boolean(activeId)

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="笔记"
        description="支持 Markdown 实时预览，AI 生成的内容会带有标记。"
        actions={
          <Button variant="primary" size="sm" icon={<Plus className="size-3.5" />} onClick={startNew}>
            新建笔记
          </Button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        {/* list */}
        <div className="space-y-2.5">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
            <Input
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(1)
              }}
              placeholder="搜索笔记…"
              className="pl-8"
              aria-label="搜索笔记"
            />
          </div>
          <Select
            value={projectFilter}
            onChange={(event) => {
              setProjectFilter(event.target.value)
              setPage(1)
            }}
            aria-label="按项目筛选"
          >
            <option value="">全部项目</option>
            {(projectsQuery.data?.items ?? []).map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </Select>

          {listQuery.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, index) => (
                <Skeleton key={index} className="h-14" />
              ))}
            </div>
          ) : listQuery.isError ? (
            <ErrorState compact error={listQuery.error} onRetry={() => void listQuery.refetch()} />
          ) : sortedNotes.length === 0 ? (
            <EmptyState
              compact
              icon={<NotebookPen className="size-4" />}
              title="还没有笔记"
              description="新建一条笔记，或让 AI 分析文档后自动生成。"
              action={
                <Button size="xs" variant="secondary" icon={<Plus className="size-3" />} onClick={startNew}>
                  新建
                </Button>
              }
              className="border-0"
            />
          ) : (
            <>
              <ul className="space-y-1">
                {sortedNotes.map((note) => (
                  <li key={note.id}>
                    <button
                      type="button"
                      onClick={() => selectNote(note.id)}
                      className={cn(
                        'w-full rounded-md border px-2.5 py-2 text-left transition-colors',
                        activeId === note.id
                          ? 'border-accent/50 bg-accent-soft'
                          : 'border-subtle bg-surface hover:border-strong',
                      )}
                    >
                      <div className="flex items-center gap-1.5">
                        {note.is_pinned ? <Pin className="size-3 shrink-0 text-warn" /> : null}
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-primary">
                          {note.title}
                        </span>
                        {note.is_ai_generated ? <Badge tone="accent">AI</Badge> : null}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-2xs text-faint">
                        {note.content_md.slice(0, 90) || '（空笔记）'}
                      </p>
                      <p className="mt-0.5 text-2xs text-faint">{relativeTime(note.updated_at)}</p>
                    </button>
                  </li>
                ))}
              </ul>
              <Pagination
                page={listQuery.data?.meta.page ?? 1}
                pageSize={listQuery.data?.meta.page_size ?? PAGE_SIZE}
                total={listQuery.data?.meta.total ?? 0}
                onPage={setPage}
              />
            </>
          )}
        </div>

        {/* editor */}
        <Card className="flex min-h-[560px] flex-col">
          {!editorOpen ? (
            <div className="flex flex-1 items-center justify-center p-6">
              <EmptyState
                icon={<Pencil className="size-4" />}
                title="选择或新建一条笔记"
                description="左侧列表中选择一条笔记开始编辑，支持实时 Markdown 预览。"
                className="border-0"
              />
            </div>
          ) : detailQuery.isLoading && !isNew ? (
            <div className="p-4">
              <SkeletonText lines={10} />
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b border-subtle px-3 py-2">
                <Input
                  value={title}
                  onChange={(event) => {
                    setTitle(event.target.value)
                    setDirty(true)
                  }}
                  placeholder="笔记标题"
                  className="h-8 min-w-[180px] flex-1"
                  aria-label="笔记标题"
                />

                {activeNote?.is_ai_generated ? <Badge tone="accent" icon={<Sparkles className="size-3" />}>AI 生成</Badge> : null}

                <SegmentedControl
                  value={mode}
                  onChange={setMode}
                  options={[
                    { value: 'edit' as EditorMode, label: '编辑', icon: <Pencil className="size-3" /> },
                    { value: 'split' as EditorMode, label: '分栏', icon: <Columns2 className="size-3" /> },
                    { value: 'preview' as EditorMode, label: '预览', icon: <Eye className="size-3" /> },
                  ]}
                />

                <IconButton
                  label={pinned ? '取消置顶' : '置顶'}
                  size="sm"
                  active={pinned}
                  onClick={() => {
                    setPinned((value) => !value)
                    setDirty(true)
                  }}
                >
                  {pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
                </IconButton>

                <Button
                  size="sm"
                  variant="primary"
                  icon={<Save className="size-3.5" />}
                  loading={createMutation.isPending || updateMutation.isPending}
                  disabled={!dirty && !isNew}
                  onClick={save}
                >
                  {dirty || isNew ? '保存' : '已保存'}
                </Button>

                {activeId && !isNew ? (
                  <IconButton
                    label="删除笔记"
                    size="sm"
                    onClick={() => activeNote && void handleDelete(activeNote)}
                  >
                    <Trash2 className="size-3.5 text-danger" />
                  </IconButton>
                ) : null}
              </div>

              <div className="flex flex-wrap items-center gap-2 border-b border-subtle px-3 py-1.5">
                <Select
                  value={projectId}
                  onChange={(event) => {
                    setProjectId(event.target.value)
                    setDirty(true)
                  }}
                  className="h-7 w-auto min-w-[150px] py-0 text-2xs"
                  aria-label="关联项目"
                >
                  <option value="">不关联项目</option>
                  {(projectsQuery.data?.items ?? []).map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </Select>
                <span className="text-2xs text-faint">
                  {content.length} 字符 · Ctrl/Cmd+S 保存
                  {activeNote ? ` · 更新于 ${relativeTime(activeNote.updated_at)}` : ''}
                </span>
              </div>

              <div className="grid min-h-0 flex-1 lg:grid-cols-2">
                {mode !== 'preview' ? (
                  <Textarea
                    value={content}
                    onChange={(event) => {
                      setContent(event.target.value)
                      setDirty(true)
                    }}
                    placeholder="用 Markdown 写点什么…"
                    aria-label="笔记内容"
                    className={cn(
                      'min-h-[420px] resize-none rounded-none border-0 font-mono text-xs leading-relaxed',
                      mode === 'split' && 'border-r border-subtle',
                    )}
                  />
                ) : null}
                {mode !== 'edit' ? (
                  <div className="aiw-scroll max-h-[70vh] overflow-y-auto px-4 py-3">
                    {content.trim() ? (
                      <Markdown>{content}</Markdown>
                    ) : (
                      <p className="text-2xs text-faint">（预览区为空）</p>
                    )}
                  </div>
                ) : null}
              </div>
            </>
          )}
        </Card>
      </div>
    </PageContainer>
  )
}
