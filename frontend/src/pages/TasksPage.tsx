import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CornerDownRight,
  Eye,
  ListChecks,
  ListTree,
  Plus,
  Search,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatMinutes, isOverdue, priorityLabel, taskStatusLabel } from '@/lib/utils'
import { flattenTaskTree, treeStats, type FlatTreeRow } from '@/lib/tree'
import { Button, IconButton } from '@/components/ui/Button'
import { Input, Select } from '@/components/ui/form'
import {
  Badge,
  Card,
  PageContainer,
  Pagination,
  SectionHeader,
  SegmentedControl,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, SkeletonTable } from '@/components/ui/feedback'
import { PriorityBadge, SourceBadge } from '@/components/badges'
import { DeleteTaskButton, TaskDetailDrawer, TaskEditorModal, TASK_STATUSES } from '@/features/tasks/TaskDialogs'
import { useToastStore, toastApiError } from '@/store/toast'
import type { Task, TaskStatus, TaskTreeNode } from '@/lib/types'

const PAGE_SIZE = 20
type ViewMode = 'list' | 'tree'

const PRIORITY_FILTERS = [
  { value: '', label: '全部优先级' },
  { value: 'URGENT', label: '紧急' },
  { value: 'HIGH', label: '高' },
  { value: 'MEDIUM', label: '中' },
  { value: 'LOW', label: '低' },
]

function StatusSelect({
  task,
  onChange,
  disabled,
}: {
  task: Task
  onChange: (status: TaskStatus) => void
  disabled?: boolean
}) {
  return (
    <Select
      value={task.status}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as TaskStatus)}
      aria-label={`修改「${task.title}」的状态`}
      className="h-6 w-[104px] py-0 text-2xs"
    >
      {TASK_STATUSES.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </Select>
  )
}

export default function TasksPage() {
  const [params, setParams] = useSearchParams()
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)

  const [view, setView] = useState<ViewMode>('list')
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [priority, setPriority] = useState('')
  const [projectFilter, setProjectFilter] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const [editor, setEditor] = useState<{ open: boolean; task: Task | null; parentId: string | null }>({
    open: false,
    task: null,
    parentId: null,
  })
  const [activeTask, setActiveTask] = useState<string | null>(params.get('task'))

  useEffect(() => {
    const taskParam = params.get('task')
    if (taskParam) setActiveTask(taskParam)
  }, [params])

  useEffect(() => {
    if (params.get('new') === '1') {
      setEditor({ open: true, task: null, parentId: null })
      const next = new URLSearchParams(params)
      next.delete('new')
      setParams(next, { replace: true })
    }
  }, [params, setParams])

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })

  const listQuery = useQuery({
    queryKey: ['tasks', 'list', { page, search, status, priority, projectFilter }],
    queryFn: ({ signal }) =>
      api.tasks.list(
        {
          page,
          page_size: PAGE_SIZE,
          q: search || undefined,
          status: status || undefined,
          priority: priority || undefined,
          project_id: projectFilter || undefined,
        },
        signal,
      ),
    enabled: view === 'list',
  })

  const treeQuery = useQuery({
    queryKey: ['tasks', 'tree', projectFilter],
    queryFn: ({ signal }) => api.tasks.tree(projectFilter || undefined, signal),
    enabled: view === 'tree',
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    void queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
  }

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: { status?: TaskStatus } }) =>
      api.tasks.update(id, payload),
    onSuccess: () => invalidate(),
    onError: (error) => toastApiError(error, '更新任务失败'),
  })

  const completeMutation = useMutation({
    mutationFn: (id: string) => api.tasks.complete(id),
    onSuccess: () => {
      success('任务已完成')
      invalidate()
    },
    // A 409 here means unfinished dependencies — surface the backend message verbatim.
    onError: (error) => toastApiError(error, '无法完成任务'),
  })

  const tree = useMemo(() => treeQuery.data?.tree ?? [], [treeQuery.data])
  const stats = useMemo(() => treeStats(tree), [tree])

  // Client-side filtering keeps the hierarchy intact when a parent is hidden.
  const filteredTree = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term && !status && !priority) return tree
    const keep = (node: TaskTreeNode): boolean => {
      const matchesTerm = !term || node.title.toLowerCase().includes(term)
      const matchesStatus = !status || node.status === status
      const matchesPriority = !priority || node.priority === priority
      return matchesTerm && matchesStatus && matchesPriority
    }
    const walk = (nodes: TaskTreeNode[]): TaskTreeNode[] => {
      const result: TaskTreeNode[] = []
      for (const node of nodes) {
        const children = walk(node.children)
        if (keep(node) || children.length > 0) result.push({ ...node, children })
      }
      return result
    }
    return walk(tree)
  }, [tree, search, status, priority])

  const treeRows: FlatTreeRow[] = useMemo(
    () => flattenTaskTree(filteredTree, collapsed),
    [filteredTree, collapsed],
  )

  const toggleCollapse = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const listItems = listQuery.data?.items ?? []
  const filtersActive = Boolean(search || status || priority || projectFilter)

  const rowActions = (task: Task) => (
    <div className="flex items-center justify-end gap-1">
      <IconButton
        label="标记完成"
        size="xs"
        disabled={task.status === 'DONE' || completeMutation.isPending}
        onClick={() => completeMutation.mutate(task.id)}
      >
        <CheckCircle2 className={cn('size-3', task.status === 'DONE' ? 'text-success' : 'text-muted')} />
      </IconButton>
      <IconButton label="查看详情" size="xs" onClick={() => setActiveTask(task.id)}>
        <Eye className="size-3" />
      </IconButton>
      <IconButton
        label="添加子任务"
        size="xs"
        onClick={() => setEditor({ open: true, task: null, parentId: task.id })}
      >
        <CornerDownRight className="size-3" />
      </IconButton>
      <DeleteTaskButton task={task} onDeleted={invalidate} />
    </div>
  )

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="任务"
        description="列表视图适合批量操作，树视图展示目标 → 阶段 → 任务 → 子任务的层级。"
        actions={
          <Button
            variant="primary"
            size="sm"
            icon={<Plus className="size-3.5" />}
            onClick={() => setEditor({ open: true, task: null, parentId: null })}
          >
            新建任务
          </Button>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl
          value={view}
          onChange={(value) => setView(value)}
          options={[
            { value: 'list' as ViewMode, label: '列表', icon: <ListChecks className="size-3" /> },
            { value: 'tree' as ViewMode, label: '树视图', icon: <ListTree className="size-3" /> },
          ]}
        />
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
          <Input
            value={search}
            onChange={(event) => {
              setSearch(event.target.value)
              setPage(1)
            }}
            placeholder="搜索任务标题…"
            className="pl-8"
            aria-label="搜索任务"
          />
        </div>
        <Select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value)
            setPage(1)
          }}
          className="w-auto min-w-[120px]"
          aria-label="按状态筛选"
        >
          <option value="">全部状态</option>
          {TASK_STATUSES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
        <Select
          value={priority}
          onChange={(event) => {
            setPriority(event.target.value)
            setPage(1)
          }}
          className="w-auto min-w-[120px]"
          aria-label="按优先级筛选"
        >
          {PRIORITY_FILTERS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
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
      </div>

      {view === 'tree' ? (
        treeQuery.isLoading ? (
          <SkeletonTable rows={8} cols={4} />
        ) : treeQuery.isError ? (
          <ErrorState title="任务树加载失败" error={treeQuery.error} onRetry={() => void treeQuery.refetch()} />
        ) : treeRows.length === 0 ? (
          <EmptyState
            icon={<ListTree className="size-4" />}
            title={filtersActive ? '没有匹配的任务' : '还没有任务'}
            description={
              filtersActive
                ? '调整筛选条件，树视图会保留匹配节点的父级层级。'
                : '手动创建任务，或在「任务规划」中由 AI 生成任务树。'
            }
            action={
              <Button
                variant="primary"
                size="sm"
                icon={<Plus className="size-3.5" />}
                onClick={() => setEditor({ open: true, task: null, parentId: null })}
              >
                新建任务
              </Button>
            }
          />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2 text-2xs text-faint">
              <Badge tone="neutral">共 {stats.total}</Badge>
              <Badge tone="success">已完成 {stats.done}</Badge>
              <Badge tone="info">进行中 {stats.inProgress}</Badge>
              <Badge tone={stats.blocked > 0 ? 'danger' : 'neutral'}>受阻 {stats.blocked}</Badge>
              <span className="ml-auto">{treeQuery.data?.count ?? 0} 个节点</span>
            </div>

            <Card className="overflow-hidden">
              <ul>
                {treeRows.map(({ node, depth }) => (
                  <li
                    key={node.id}
                    className="flex items-center gap-2 border-b border-subtle px-3 py-2 last:border-b-0 hover:bg-elevated"
                  >
                    <span style={{ width: depth * 18 }} aria-hidden />
                    {node.children.length > 0 ? (
                      <button
                        type="button"
                        aria-label={collapsed.has(node.id) ? '展开子任务' : '收起子任务'}
                        onClick={() => toggleCollapse(node.id)}
                        className="shrink-0 rounded p-0.5 text-faint hover:bg-surface hover:text-primary"
                      >
                        {collapsed.has(node.id) ? (
                          <ChevronRight className="size-3.5" />
                        ) : (
                          <ChevronDown className="size-3.5" />
                        )}
                      </button>
                    ) : (
                      <span className="size-4 shrink-0" aria-hidden />
                    )}

                    <button
                      type="button"
                      onClick={() => setActiveTask(node.id)}
                      className="min-w-0 flex-1 truncate text-left text-xs text-primary hover:text-accent hover:underline"
                    >
                      {node.title}
                    </button>

                    {node.children.length > 0 ? (
                      <span className="shrink-0 font-mono text-2xs text-faint">
                        {node.children.length}
                      </span>
                    ) : null}
                    {node.due_date ? (
                      <span
                        className={cn(
                          'hidden shrink-0 text-2xs tabular-nums sm:inline',
                          isOverdue(node.due_date) ? 'text-danger' : 'text-faint',
                        )}
                      >
                        {node.due_date.slice(0, 10)}
                      </span>
                    ) : null}
                    {node.estimated_minutes ? (
                      <span className="hidden shrink-0 text-2xs text-faint md:inline">
                        {formatMinutes(node.estimated_minutes)}
                      </span>
                    ) : null}
                    <PriorityBadge priority={node.priority} />
                    <StatusSelect
                      task={node}
                      disabled={updateMutation.isPending}
                      onChange={(next) => updateMutation.mutate({ id: node.id, payload: { status: next } })}
                    />
                    {rowActions(node)}
                  </li>
                ))}
              </ul>
            </Card>
          </>
        )
      ) : listQuery.isLoading ? (
        <SkeletonTable rows={10} cols={6} />
      ) : listQuery.isError ? (
        <ErrorState title="任务加载失败" error={listQuery.error} onRetry={() => void listQuery.refetch()} />
      ) : listItems.length === 0 ? (
        <EmptyState
          icon={<ListChecks className="size-4" />}
          title={filtersActive ? '没有匹配的任务' : '还没有任务'}
          description={
            filtersActive ? '调整筛选条件再试一次。' : '创建第一个任务，或让规划器帮你拆解目标。'
          }
          action={
            <Button
              variant="primary"
              size="sm"
              icon={<Plus className="size-3.5" />}
              onClick={() => setEditor({ open: true, task: null, parentId: null })}
            >
              新建任务
            </Button>
          }
        />
      ) : (
        <>
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left text-xs">
                <thead className="border-b border-subtle bg-elevated text-2xs uppercase tracking-wide text-faint">
                  <tr>
                    <th className="px-3 py-2 font-medium">任务</th>
                    <th className="w-[120px] px-3 py-2 font-medium">状态</th>
                    <th className="w-[90px] px-3 py-2 font-medium">优先级</th>
                    <th className="w-[110px] px-3 py-2 font-medium">截止</th>
                    <th className="w-[100px] px-3 py-2 font-medium">耗时</th>
                    <th className="w-[80px] px-3 py-2 font-medium">来源</th>
                    <th className="w-[150px] px-3 py-2 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {listItems.map((task) => (
                    <tr key={task.id} className="border-b border-subtle last:border-b-0 hover:bg-elevated">
                      <td className="px-3 py-2">
                        <button
                          type="button"
                          onClick={() => setActiveTask(task.id)}
                          className="block max-w-[380px] truncate text-left text-xs font-medium text-primary hover:text-accent hover:underline"
                        >
                          {task.title}
                        </button>
                        {task.parent_id ? (
                          <p className="font-mono text-2xs text-faint">子任务 · depth {task.depth}</p>
                        ) : null}
                      </td>
                      <td className="px-3 py-2">
                        <StatusSelect
                          task={task}
                          disabled={updateMutation.isPending}
                          onChange={(next) =>
                            updateMutation.mutate({ id: task.id, payload: { status: next } })
                          }
                        />
                      </td>
                      <td className="px-3 py-2">
                        <PriorityBadge priority={task.priority} />
                      </td>
                      <td className="px-3 py-2">
                        {task.due_date ? (
                          <span
                            className={cn(
                              'text-2xs tabular-nums',
                              isOverdue(task.due_date) ? 'text-danger' : 'text-muted',
                            )}
                          >
                            {task.due_date.slice(0, 10)}
                          </span>
                        ) : (
                          <span className="text-2xs text-faint">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-2xs text-muted">
                        {task.actual_minutes
                          ? `${formatMinutes(task.actual_minutes)} / ${formatMinutes(task.estimated_minutes)}`
                          : formatMinutes(task.estimated_minutes)}
                      </td>
                      <td className="px-3 py-2">
                        <SourceBadge source={task.source} />
                      </td>
                      <td className="px-3 py-2">{rowActions(task)}</td>
                    </tr>
                  ))}
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

      <p className="text-2xs text-faint">
        状态标签：{TASK_STATUSES.map((option) => taskStatusLabel(option.value)).join(' / ')} ·
        优先级：{PRIORITY_FILTERS.slice(1).map((option) => priorityLabel(option.value)).join(' / ')}
      </p>

      <TaskEditorModal
        open={editor.open}
        task={editor.task}
        parentId={editor.parentId}
        onClose={() => setEditor({ open: false, task: null, parentId: null })}
        onSaved={invalidate}
      />

      <TaskDetailDrawer
        taskId={activeTask}
        open={Boolean(activeTask)}
        onClose={() => {
          setActiveTask(null)
          const next = new URLSearchParams(params)
          next.delete('task')
          setParams(next, { replace: true })
        }}
        onEdit={(task) => setEditor({ open: true, task, parentId: null })}
        onChanged={invalidate}
      />
    </PageContainer>
  )
}
