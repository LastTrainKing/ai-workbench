import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  ArrowRight,
  CalendarRange,
  CheckCircle2,
  Clock,
  GraduationCap,
  GitBranch,
  ListChecks,
  Sparkles,
  Target,
} from 'lucide-react'
import { api } from '@/lib/api'
import { formatMinutes } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select, Textarea } from '@/components/ui/form'
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  PageContainer,
  SectionHeader,
  Tabs,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, InlineAlert, SkeletonText } from '@/components/ui/feedback'
import { PriorityBadge } from '@/components/badges'
import { KnowledgeBasePicker } from '@/features/knowledge/KnowledgeBasePicker'
import { useToastStore, toastApiError } from '@/store/toast'
import type { Plan, StudyPlanResponse } from '@/lib/types'

type TabKey = 'plan' | 'study'

function PlanView({
  plan,
  onApply,
  applying,
  created,
}: {
  plan: Plan
  onApply: () => void
  applying: boolean
  created: { created: number; phases: number; task_ids: string[] } | null
}) {
  const totalTasks = plan.phases.reduce((sum, phase) => sum + phase.tasks.length, 0)

  return (
    <div className="space-y-3">
      <Card>
        <CardBody className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="flex items-center gap-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">
                <Target className="size-3" />
                目标
              </p>
              <p className="mt-0.5 text-sm font-medium text-primary">{plan.goal}</p>
              {plan.strategy ? <p className="mt-1 text-xs text-muted">{plan.strategy}</p> : null}
            </div>
            <div className="flex shrink-0 flex-wrap gap-1.5">
              <Badge tone="accent">{plan.phases.length} 个阶段</Badge>
              <Badge tone="neutral">{totalTasks} 个任务</Badge>
              {plan.horizon_days ? <Badge tone="info">{plan.horizon_days} 天</Badge> : null}
              {plan.estimated_total_hours ? (
                <Badge tone="neutral">≈ {plan.estimated_total_hours.toFixed(1)} 小时</Badge>
              ) : null}
              {plan.grounded_chunks ? (
                <Badge tone="success">基于 {plan.grounded_chunks} 个片段</Badge>
              ) : null}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              loading={applying}
              disabled={Boolean(created)}
              icon={<ListChecks className="size-3.5" />}
              onClick={onApply}
            >
              {created ? `已创建 ${created.created} 个任务` : '创建为任务'}
            </Button>
            <Link to={`/ai-runs/${plan.run_id}`} className="text-2xs text-accent hover:underline">
              查看运行轨迹
            </Link>
            {plan.provider ? (
              <span className="ml-auto font-mono text-2xs text-faint">
                {plan.provider} · {plan.model}
              </span>
            ) : null}
          </div>

          {created ? (
            <InlineAlert tone="success" title={`已创建 ${created.created} 个任务（${created.phases} 个阶段）`}>
              任务已写入任务清单，
              <Link to="/tasks" className="mx-1 text-accent hover:underline">
                前往任务页面
              </Link>
              查看。
            </InlineAlert>
          ) : null}
        </CardBody>
      </Card>

      <ol className="space-y-3">
        {plan.phases.map((phase, phaseIndex) => (
          <li key={`${phase.name}-${phaseIndex}`}>
            <Card>
              <CardHeader
                title={
                  <span className="flex items-center gap-2">
                    <span className="flex size-5 items-center justify-center rounded bg-accent-soft font-mono text-2xs font-semibold text-accent">
                      {phaseIndex + 1}
                    </span>
                    {phase.name}
                  </span>
                }
                description={phase.description}
              />
              <CardBody className="space-y-2">
                {phase.tasks.map((task, taskIndex) => (
                  <div
                    key={`${task.title}-${taskIndex}`}
                    className="rounded-md border border-subtle bg-elevated px-2.5 py-2.5"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <p className="min-w-0 flex-1 text-xs font-medium text-primary">{task.title}</p>
                      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                        {task.priority ? <PriorityBadge priority={task.priority} /> : null}
                        {task.estimated_minutes ? (
                          <Badge tone="neutral" icon={<Clock className="size-3" />}>
                            {formatMinutes(task.estimated_minutes)}
                          </Badge>
                        ) : null}
                      </div>
                    </div>

                    {task.description ? (
                      <p className="mt-1 text-2xs leading-relaxed text-muted">{task.description}</p>
                    ) : null}

                    {task.completion_criteria ? (
                      <p className="mt-1.5 flex items-start gap-1.5 text-2xs text-muted">
                        <CheckCircle2 className="mt-0.5 size-3 shrink-0 text-success" />
                        完成标准：{task.completion_criteria}
                      </p>
                    ) : null}

                    {task.depends_on && task.depends_on.length > 0 ? (
                      <p className="mt-1 flex items-center gap-1.5 text-2xs text-warn">
                        <GitBranch className="size-3 shrink-0" />
                        依赖：{task.depends_on.join('、')}
                      </p>
                    ) : null}

                    {task.subtasks && task.subtasks.length > 0 ? (
                      <ul className="mt-2 space-y-1 border-l border-subtle pl-3">
                        {task.subtasks.map((subtask, subtaskIndex) => (
                          <li
                            key={`${subtask.title}-${subtaskIndex}`}
                            className="flex items-center justify-between gap-2 text-2xs text-muted"
                          >
                            <span className="min-w-0 truncate">{subtask.title}</span>
                            {subtask.estimated_minutes ? (
                              <span className="shrink-0 tabular-nums text-faint">
                                {formatMinutes(subtask.estimated_minutes)}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                ))}
              </CardBody>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  )
}

function StudyPlanView({ result }: { result: StudyPlanResponse }) {
  const days = (result.days ?? []) as {
    day: number | string
    focus: string
    goal?: string
    estimated_minutes?: number
    deliverable?: string
  }[]

  if (days.length === 0) {
    return (
      <InlineAlert tone="info" title="学习计划已生成，但未返回按天条目">
        原始结果：{JSON.stringify(result).slice(0, 300)}
      </InlineAlert>
    )
  }

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">{days.length} 天</Badge>
        <Link to={`/ai-runs/${result.run_id}`} className="text-2xs text-accent hover:underline">
          查看运行轨迹
        </Link>
      </div>
      <ol className="space-y-2">
        {days.map((day, index) => (
          <li key={`${day.day}-${index}`}>
            <Card>
              <CardBody className="flex gap-3">
                <span className="flex size-8 shrink-0 flex-col items-center justify-center rounded bg-accent-soft text-accent">
                  <span className="font-mono text-2xs font-semibold leading-none">D{day.day}</span>
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-primary">{day.focus}</p>
                  {day.goal ? <p className="mt-0.5 text-2xs text-muted">{day.goal}</p> : null}
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    {day.estimated_minutes ? (
                      <Badge tone="neutral" icon={<Clock className="size-3" />}>
                        {formatMinutes(day.estimated_minutes)}
                      </Badge>
                    ) : null}
                    {day.deliverable ? <Badge tone="success">产出：{day.deliverable}</Badge> : null}
                  </div>
                </div>
              </CardBody>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  )
}

export default function PlannerPage() {
  const success = useToastStore((state) => state.success)
  const [tab, setTab] = useState<TabKey>('plan')

  const [goal, setGoal] = useState('')
  const [kbIds, setKbIds] = useState<string[]>([])
  const [horizon, setHorizon] = useState(30)
  const [createTasks, setCreateTasks] = useState(false)
  const [created, setCreated] = useState<{ created: number; phases: number; task_ids: string[] } | null>(null)

  const [studyGoal, setStudyGoal] = useState('')
  const [studyKbIds, setStudyKbIds] = useState<string[]>([])
  const [studyDays, setStudyDays] = useState(14)

  const projectsQuery = useQuery({
    queryKey: ['projects', 'options'],
    queryFn: ({ signal }) => api.projects.list({ page_size: 100 }, signal),
    staleTime: 60_000,
  })
  const [projectId, setProjectId] = useState('')

  const planMutation = useMutation({
    mutationFn: () =>
      api.planner.plan({
        goal: goal.trim(),
        knowledge_base_ids: kbIds,
        project_id: projectId || null,
        horizon_days: horizon,
        create_tasks: createTasks,
      }),
    onSuccess: (result) => {
      setCreated(result.data.created)
      success('计划已生成', result.message)
    },
    onError: (error) => toastApiError(error, '生成计划失败'),
  })

  const applyMutation = useMutation({
    mutationFn: (runId: string) => api.planner.apply({ run_id: runId, project_id: projectId || null }),
    onSuccess: (result) => {
      setCreated(result.data)
      success(`已创建 ${result.data.created} 个任务`, result.message)
    },
    onError: (error) => toastApiError(error, '创建任务失败'),
  })

  const studyMutation = useMutation({
    mutationFn: () =>
      api.planner.studyPlan({
        goal: studyGoal.trim(),
        knowledge_base_ids: studyKbIds,
        project_id: projectId || null,
        horizon_days: studyDays,
      }),
    onSuccess: () => success('学习计划已生成'),
    onError: (error) => toastApiError(error, '生成学习计划失败'),
  })

  const plan = planMutation.data?.data.plan

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="任务规划"
        description="把一个目标拆解为阶段 → 任务 → 子任务，可直接落库为可执行任务。"
      />

      <Tabs
        value={tab}
        onChange={(key) => setTab(key as TabKey)}
        items={[
          { key: 'plan', label: '执行计划', icon: <CalendarRange className="size-3" /> },
          { key: 'study', label: '学习计划', icon: <GraduationCap className="size-3" /> },
        ]}
      />

      {tab === 'plan' ? (
        <>
          <Card>
            <CardBody className="space-y-3.5">
              <Field label="目标" htmlFor="plan-goal" required hint="描述得越具体，拆解越可用">
                <Textarea
                  id="plan-goal"
                  rows={2}
                  value={goal}
                  maxLength={1000}
                  onChange={(event) => setGoal(event.target.value)}
                  placeholder="例如：两周内完成毕业设计的中期答辩材料与演示系统"
                />
              </Field>

              <Field label="参考资料（知识库）">
                <KnowledgeBasePicker selected={kbIds} onChange={setKbIds} projectId={projectId || undefined} />
              </Field>

              <div className="grid gap-3.5 sm:grid-cols-2">
                <Field label="计划周期（天）" htmlFor="plan-horizon">
                  <Input
                    id="plan-horizon"
                    type="number"
                    min={1}
                    max={365}
                    value={horizon}
                    onChange={(event) => setHorizon(Number(event.target.value))}
                  />
                </Field>
                <Field label="关联项目" htmlFor="plan-project">
                  <Select
                    id="plan-project"
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
              </div>

              <label className="flex items-center gap-2 text-2xs text-muted">
                <input
                  type="checkbox"
                  checked={createTasks}
                  onChange={(event) => setCreateTasks(event.target.checked)}
                  className="size-3.5 accent-[rgb(var(--c-accent))]"
                />
                生成后立即创建为任务
              </label>

              <Button
                variant="primary"
                size="md"
                loading={planMutation.isPending}
                disabled={goal.trim().length < 2}
                icon={<Sparkles className="size-3.5" />}
                onClick={() => {
                  setCreated(null)
                  planMutation.mutate()
                }}
              >
                生成计划
              </Button>
            </CardBody>
          </Card>

          {planMutation.isPending ? (
            <Card>
              <CardBody>
                <SkeletonText lines={8} />
              </CardBody>
            </Card>
          ) : planMutation.isError ? (
            <ErrorState title="生成计划失败" error={planMutation.error} onRetry={() => planMutation.mutate()} />
          ) : plan ? (
            <PlanView
              plan={plan}
              created={created}
              applying={applyMutation.isPending}
              onApply={() => applyMutation.mutate(plan.run_id)}
            />
          ) : (
            <EmptyState
              icon={<CalendarRange className="size-4" />}
              title="还没有计划"
              description="填写目标并选择参考资料，规划器会把目标拆成阶段与任务。"
            />
          )}
        </>
      ) : (
        <>
          <Card>
            <CardBody className="space-y-3.5">
              <Field label="学习目标" htmlFor="study-goal" required>
                <Textarea
                  id="study-goal"
                  rows={2}
                  value={studyGoal}
                  maxLength={1000}
                  onChange={(event) => setStudyGoal(event.target.value)}
                  placeholder="例如：系统掌握 RAG 检索增强生成的原理与工程实现"
                />
              </Field>
              <Field label="参考资料（知识库）">
                <KnowledgeBasePicker
                  selected={studyKbIds}
                  onChange={setStudyKbIds}
                  projectId={projectId || undefined}
                />
              </Field>
              <Field label="学习天数" htmlFor="study-days" className="max-w-[160px]">
                <Input
                  id="study-days"
                  type="number"
                  min={1}
                  max={365}
                  value={studyDays}
                  onChange={(event) => setStudyDays(Number(event.target.value))}
                />
              </Field>
              <Button
                variant="primary"
                size="md"
                loading={studyMutation.isPending}
                disabled={studyGoal.trim().length < 2}
                icon={<GraduationCap className="size-3.5" />}
                onClick={() => studyMutation.mutate()}
              >
                生成学习计划
              </Button>
            </CardBody>
          </Card>

          {studyMutation.isPending ? (
            <Card>
              <CardBody>
                <SkeletonText lines={6} />
              </CardBody>
            </Card>
          ) : studyMutation.isError ? (
            <ErrorState
              title="生成学习计划失败"
              error={studyMutation.error}
              onRetry={() => studyMutation.mutate()}
            />
          ) : studyMutation.data ? (
            <StudyPlanView result={studyMutation.data.data} />
          ) : (
            <EmptyState
              icon={<GraduationCap className="size-4" />}
              title="还没有学习计划"
              description="按天生成可执行的学习安排，并附带每天的目标与产出。"
            />
          )}
        </>
      )}

      <p className="flex items-center gap-1.5 text-2xs text-faint">
        <ArrowRight className="size-3" />
        提示：生成的计划会记录为一次 AI 运行，可在「AI 运行」中回看完整的提示词与引用。
      </p>
    </PageContainer>
  )
}
