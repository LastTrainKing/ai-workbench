import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  Link2,
  Plus,
  Trash2,
  X,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatDateTime, formatMinutes, isOverdue, relativeTime } from '@/lib/utils'
import { Button, IconButton } from '@/components/ui/Button'
import { Field, Input, Select, Textarea } from '@/components/ui/form'
import { Modal } from '@/components/ui/overlay'
import { Badge, Card, CardBody, CardHeader, KeyValue } from '@/components/ui/primitives'
import { EmptyState, ErrorState, InlineAlert, SkeletonText } from '@/components/ui/feedback'
import { PriorityBadge, SourceBadge, TaskStatusBadge } from '@/components/badges'
import { Markdown } from '@/components/Markdown'
import { useToastStore, toastApiError } from '@/store/toast'
import type { Task, TaskPayload, TaskStatus } from '@/lib/types'

export const TASK_STATUSES: { value: TaskStatus; label: string }[] = [
  { value: 'TODO', label: '待办' },
  { value: 'IN_PROGRESS', label: '进行中' },
  { value: 'BLOCKED', label: '受阻' },
  { value: 'DONE', label: '已完成' },
  { value: 'CANCELLED', label: '已取消' },
]

const PRIORITIES = [
  { value: 'LOW', label: '低' },
  { value: 'MEDIUM', label: '中' },
  { value: 'HIGH', label: '高' },
  { value: 'URGENT', label: '紧急' },
]

export interface TaskEditorState {
  open: boolean
  task: Task | null
  parentId: string | null
}

interface TaskEditorModalProps extends TaskEditorState {
  onClose: () => void
  onSaved: () => void
}

export function TaskEditorModal({ open, task, parentId, onClose, onSaved }: TaskEditorModalProps) {
  const success = useToastStore((state) => state.success)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [status, setStatus] = useState<TaskStatus>('TODO')
  const [priority, setPriority] = useState('MEDIUM')
  const [projectId, setProjectId] = useState('')
  const [dueDate, setDueDate] = useState('')
  const [estimated, setEstimated] = useState(0)
  const [actual, setActual] = useState(0)
  const [criteria, setCriteria] = useState('')

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    enabled: open,
    staleTime: 60_000,
  })

  useEffect(() => {
    if (!open) return
    setTitle(task?.title ?? '')
    setDescription(task?.description ?? '')
    setStatus((task?.status as TaskStatus) ?? 'TODO')
    setPriority(task?.priority ?? 'MEDIUM')
    setProjectId(task?.project_id ?? '')
    setDueDate(task?.due_date ? task.due_date.slice(0, 10) : '')
    setEstimated(task?.estimated_minutes ?? 0)
    setActual(task?.actual_minutes ?? 0)
    setCriteria(task?.completion_criteria ?? '')
  }, [open, task])

  const createMutation = useMutation({
    mutationFn: (payload: TaskPayload) => api.tasks.create(payload),
    onSuccess: (result) => {
      success('任务已创建', result.data.title)
      onSaved()
      onClose()
    },
    onError: (error) => toastApiError(error, '创建任务失败'),
  })

  const updateMutation = useMutation({
    mutationFn: (payload: Partial<TaskPayload>) => api.tasks.update(task?.id as string, payload),
    onSuccess: () => {
      success('任务已更新')
      onSaved()
      onClose()
    },
    onError: (error) => toastApiError(error, '更新任务失败'),
  })

  const submit = () => {
    const trimmed = title.trim()
    if (!trimmed) return
    const payload: TaskPayload = {
      title: trimmed,
      description,
      status,
      priority: priority as TaskPayload['priority'],
      project_id: projectId || null,
      due_date: dueDate.trim() || null,
      estimated_minutes: estimated,
      actual_minutes: actual,
      completion_criteria: criteria,
    }
    if (task) updateMutation.mutate(payload)
    else createMutation.mutate({ ...payload, parent_id: parentId })
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={task ? '编辑任务' : parentId ? '新建子任务' : '新建任务'}
      size="lg"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={createMutation.isPending || updateMutation.isPending}
            disabled={!title.trim()}
            onClick={submit}
          >
            {task ? '保存' : '创建'}
          </Button>
        </>
      }
    >
      <div className="space-y-3.5">
        <Field label="标题" htmlFor="task-title" required>
          <Input
            id="task-title"
            value={title}
            maxLength={300}
            autoFocus
            onChange={(event) => setTitle(event.target.value)}
            placeholder="要做什么？"
          />
        </Field>

        <Field label="描述" htmlFor="task-desc">
          <Textarea
            id="task-desc"
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="补充背景、上下文或验收说明"
          />
        </Field>

        <div className="grid gap-3.5 sm:grid-cols-2">
          <Field label="状态" htmlFor="task-status">
            <Select
              id="task-status"
              value={status}
              onChange={(event) => setStatus(event.target.value as TaskStatus)}
            >
              {TASK_STATUSES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="优先级" htmlFor="task-priority">
            <Select
              id="task-priority"
              value={priority}
              onChange={(event) => setPriority(event.target.value)}
            >
              {PRIORITIES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="所属项目" htmlFor="task-project">
            <Select
              id="task-project"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            >
              <option value="">不关联项目</option>
              {(projectsQuery.data?.items ?? []).map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="截止日期"
            htmlFor="task-due"
            hint="支持 2026-11-01 或相对写法 +7d / 2w / 3个月"
          >
            <Input
              id="task-due"
              value={dueDate}
              onChange={(event) => setDueDate(event.target.value)}
              placeholder="+7d"
            />
          </Field>
          <Field label="预估耗时（分钟）" htmlFor="task-est">
            <Input
              id="task-est"
              type="number"
              min={0}
              value={estimated}
              onChange={(event) => setEstimated(Number(event.target.value))}
            />
          </Field>
          <Field label="实际耗时（分钟）" htmlFor="task-actual">
            <Input
              id="task-actual"
              type="number"
              min={0}
              value={actual}
              onChange={(event) => setActual(Number(event.target.value))}
            />
          </Field>
        </div>

        <Field label="完成标准" htmlFor="task-criteria">
          <Textarea
            id="task-criteria"
            rows={2}
            value={criteria}
            onChange={(event) => setCriteria(event.target.value)}
            placeholder="怎样算完成？"
          />
        </Field>
      </div>
    </Modal>
  )
}

export interface TaskDetailDrawerProps {
  taskId: string | null
  open: boolean
  onClose: () => void
  onEdit: (task: Task) => void
  onChanged: () => void
}

export function TaskDetailDrawer({ taskId, open, onClose, onEdit, onChanged }: TaskDetailDrawerProps) {
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)
  const [dependencyId, setDependencyId] = useState('')
  const [documentId, setDocumentId] = useState('')

  const detail = useQuery({
    queryKey: ['tasks', taskId, 'detail'],
    queryFn: ({ signal }) => api.tasks.get(taskId as string, signal),
    enabled: Boolean(taskId) && open,
  })

  const candidates = useQuery({
    queryKey: ['tasks', 'candidates'],
    queryFn: ({ signal }) => api.tasks.list({ page_size: 200 }, signal),
    enabled: open,
    staleTime: 20_000,
  })

  const documentsQuery = useQuery({
    queryKey: ['documents', 'options'],
    queryFn: ({ signal }) => api.documents.list({ page_size: 100, status: 'COMPLETED' }, signal),
    enabled: open,
    staleTime: 60_000,
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['tasks'] })
    void queryClient.invalidateQueries({ queryKey: ['insights', 'dashboard'] })
    onChanged()
  }

  const addDependency = useMutation({
    mutationFn: () => api.tasks.addDependency(taskId as string, dependencyId),
    onSuccess: () => {
      success('依赖已建立')
      setDependencyId('')
      invalidate()
    },
    onError: (error) => toastApiError(error, '建立依赖失败（可能存在循环依赖）'),
  })

  const removeDependency = useMutation({
    mutationFn: (depId: string) => api.tasks.removeDependency(taskId as string, depId),
    onSuccess: () => {
      success('依赖已移除')
      invalidate()
    },
    onError: (error) => toastApiError(error, '移除依赖失败'),
  })

  const linkDocument = useMutation({
    mutationFn: () => api.tasks.linkKnowledge(taskId as string, documentId),
    onSuccess: () => {
      success('知识关联已建立')
      setDocumentId('')
      invalidate()
    },
    onError: (error) => toastApiError(error, '关联知识失败'),
  })

  const unlinkDocument = useMutation({
    mutationFn: (docId: string) => api.tasks.unlinkKnowledge(taskId as string, docId),
    onSuccess: () => {
      success('知识关联已移除')
      invalidate()
    },
    onError: (error) => toastApiError(error, '移除关联失败'),
  })

  const complete = useMutation({
    mutationFn: () => api.tasks.complete(taskId as string, detail.data?.task.actual_minutes || undefined),
    onSuccess: () => {
      success('任务已完成')
      invalidate()
    },
    onError: (error) => toastApiError(error, '无法完成任务'),
  })

  const task = detail.data?.task
  const dependencyIds = new Set((detail.data?.dependencies ?? []).map((dep) => dep.id))
  const linkedDocIds = new Set((detail.data?.documents ?? []).map((doc) => doc.id))

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={task?.title ?? '任务详情'}
      description={task ? `更新于 ${relativeTime(task.updated_at)}` : undefined}
      size="lg"
      footer={
        task ? (
          <>
            <Button size="sm" variant="ghost" onClick={() => onEdit(task)}>
              编辑
            </Button>
            <Button
              size="sm"
              variant="success"
              icon={<CheckCircle2 className="size-3.5" />}
              disabled={task.status === 'DONE'}
              loading={complete.isPending}
              onClick={() => complete.mutate()}
            >
              {task.status === 'DONE' ? '已完成' : '标记完成'}
            </Button>
          </>
        ) : null
      }
    >
      {!taskId ? null : detail.isLoading ? (
        <SkeletonText lines={8} />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : task && detail.data ? (
        <div className="space-y-4">
          {detail.data.is_blocked ? (
            <InlineAlert tone="warn" title="该任务被未完成的依赖阻塞">
              完成全部前置任务后才能标记完成。完成时会返回 409 冲突错误。
            </InlineAlert>
          ) : null}

          <div className="flex flex-wrap items-center gap-1.5">
            <TaskStatusBadge status={task.status} />
            <PriorityBadge priority={task.priority} />
            <SourceBadge source={task.source} />
            {task.due_date ? (
              <Badge tone={isOverdue(task.due_date) ? 'danger' : 'neutral'}>
                截止 {task.due_date.slice(0, 10)}
              </Badge>
            ) : null}
            {task.estimated_minutes ? (
              <Badge tone="neutral">预估 {formatMinutes(task.estimated_minutes)}</Badge>
            ) : null}
            {task.actual_minutes ? (
              <Badge tone="info">实际 {formatMinutes(task.actual_minutes)}</Badge>
            ) : null}
          </div>

          {task.ai_suggestion ? (
            <InlineAlert tone="info" title="AI 建议">
              {task.ai_suggestion}
            </InlineAlert>
          ) : null}

          {task.description ? (
            <Card>
              <CardHeader title="描述" />
              <CardBody>
                <Markdown>{task.description}</Markdown>
              </CardBody>
            </Card>
          ) : null}

          {task.completion_criteria ? (
            <Card>
              <CardHeader title="完成标准" />
              <CardBody>
                <p className="text-xs text-muted">{task.completion_criteria}</p>
              </CardBody>
            </Card>
          ) : null}

          <Card>
            <CardHeader title="基本信息" />
            <CardBody>
              <KeyValue
                columns={2}
                items={[
                  { key: '任务 ID', value: <span className="font-mono text-2xs">{task.id}</span> },
                  { key: '层级深度', value: task.depth },
                  { key: '创建时间', value: formatDateTime(task.created_at) },
                  { key: '完成时间', value: task.completed_at ? formatDateTime(task.completed_at) : '—' },
                ]}
              />
            </CardBody>
          </Card>

          {detail.data.children.length > 0 ? (
            <Card>
              <CardHeader title={`子任务（${detail.data.children.length}）`} />
              <CardBody className="space-y-1">
                {detail.data.children.map((child) => (
                  <div key={child.id} className="flex items-center gap-2">
                    <TaskStatusBadge status={child.status} />
                    <span className="min-w-0 flex-1 truncate text-xs text-primary">{child.title}</span>
                    <PriorityBadge priority={child.priority} />
                  </div>
                ))}
              </CardBody>
            </Card>
          ) : null}

          <Card>
            <CardHeader
              title={`依赖（${detail.data.dependencies.length}）`}
              description="存在未完成的依赖时无法标记完成"
            />
            <CardBody className="space-y-2">
              {detail.data.dependencies.length === 0 ? (
                <p className="text-2xs text-faint">暂无依赖。</p>
              ) : (
                <ul className="space-y-1">
                  {detail.data.dependencies.map((dep) => (
                    <li key={dep.id} className="flex items-center gap-2">
                      <GitBranch className="size-3 shrink-0 text-faint" />
                      <span className="min-w-0 flex-1 truncate text-xs text-primary">{dep.title}</span>
                      <TaskStatusBadge status={dep.status} />
                      <IconButton
                        label="移除依赖"
                        size="xs"
                        disabled={removeDependency.isPending}
                        onClick={() => removeDependency.mutate(dep.id)}
                      >
                        <X className="size-3" />
                      </IconButton>
                    </li>
                  ))}
                </ul>
              )}

              <div className="flex gap-1.5">
                <Select
                  value={dependencyId}
                  onChange={(event) => setDependencyId(event.target.value)}
                  aria-label="选择前置任务"
                >
                  <option value="">选择前置任务…</option>
                  {(candidates.data?.items ?? [])
                    .filter((item) => item.id !== task.id && !dependencyIds.has(item.id))
                    .map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                      </option>
                    ))}
                </Select>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Plus className="size-3.5" />}
                  disabled={!dependencyId || addDependency.isPending}
                  onClick={() => addDependency.mutate()}
                >
                  添加
                </Button>
              </div>

              {detail.data.dependents.length > 0 ? (
                <p className="text-2xs text-faint">
                  被 {detail.data.dependents.length} 个任务依赖：
                  {detail.data.dependents.map((dep) => dep.title).join('、')}
                </p>
              ) : null}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title={`关联知识（${detail.data.documents.length}）`} />
            <CardBody className="space-y-2">
              {detail.data.documents.length === 0 ? (
                <p className="text-2xs text-faint">还没有关联文档。</p>
              ) : (
                <ul className="space-y-1">
                  {detail.data.documents.map((doc) => (
                    <li key={doc.id} className="flex items-center gap-2">
                      <Link2 className="size-3 shrink-0 text-faint" />
                      <Link
                        to={`/documents?doc=${doc.id}`}
                        className="min-w-0 flex-1 truncate text-xs text-primary hover:text-accent hover:underline"
                      >
                        {doc.title}
                      </Link>
                      <Badge tone="neutral">{doc.status}</Badge>
                      <IconButton
                        label="移除关联"
                        size="xs"
                        disabled={unlinkDocument.isPending}
                        onClick={() => unlinkDocument.mutate(doc.id)}
                      >
                        <X className="size-3" />
                      </IconButton>
                    </li>
                  ))}
                </ul>
              )}

              <div className="flex gap-1.5">
                <Select
                  value={documentId}
                  onChange={(event) => setDocumentId(event.target.value)}
                  aria-label="选择文档"
                >
                  <option value="">选择文档…</option>
                  {(documentsQuery.data?.items ?? [])
                    .filter((doc) => !linkedDocIds.has(doc.id))
                    .map((doc) => (
                      <option key={doc.id} value={doc.id}>
                        {doc.title}
                      </option>
                    ))}
                </Select>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Plus className="size-3.5" />}
                  disabled={!documentId || linkDocument.isPending}
                  onClick={() => linkDocument.mutate()}
                >
                  关联
                </Button>
              </div>
            </CardBody>
          </Card>

          <p className="flex items-center gap-1.5 text-2xs text-faint">
            <AlertTriangle className="size-3" />
            完成存在未完成依赖的任务会返回 409 冲突，错误信息会通过通知提示。
          </p>
        </div>
      ) : (
        <EmptyState compact title="任务不存在" className="border-0" />
      )}
    </Modal>
  )
}

export function DeleteTaskButton({ task, onDeleted }: { task: Task; onDeleted: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const success = useToastStore((state) => state.success)

  const remove = useMutation({
    mutationFn: () => api.tasks.remove(task.id),
    onSuccess: () => {
      success('任务已删除')
      onDeleted()
    },
    onError: (error) => toastApiError(error, '删除任务失败'),
  })

  if (!confirming) {
    return (
      <IconButton label="删除任务" size="xs" onClick={() => setConfirming(true)}>
        <Trash2 className="size-3 text-danger" />
      </IconButton>
    )
  }

  return (
    <span className={cn('flex items-center gap-1')}>
      <Button size="xs" variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>
        确认删除
      </Button>
      <IconButton label="取消" size="xs" onClick={() => setConfirming(false)}>
        <X className="size-3" />
      </IconButton>
    </span>
  )
}
