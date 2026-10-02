import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  ArrowLeft,
  FolderKanban,
  Layers,
  ListChecks,
  NotebookPen,
} from 'lucide-react'
import { api } from '@/lib/api'
import { formatMs, formatNumber, relativeTime, runStatusTone, runTypeLabel } from '@/lib/utils'
import { Badge, Card, CardBody, Dot, PageContainer, SectionHeader, StatTile, Tabs } from '@/components/ui/primitives'
import { EmptyState, ErrorState, Skeleton, SkeletonTable } from '@/components/ui/feedback'
import { PriorityBadge, TaskStatusBadge } from '@/components/badges'
import type { ReactNode } from 'react'

type TabKey = 'knowledge' | 'tasks' | 'notes' | 'runs'

function EmptyTab({ icon, title, description }: { icon: ReactNode; title: string; description: string }) {
  return <EmptyState icon={icon} title={title} description={description} className="border-0" />
}

export default function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>()
  const [tab, setTab] = useState<TabKey>('knowledge')

  const projectQuery = useQuery({
    queryKey: ['projects', projectId],
    queryFn: ({ signal }) => api.projects.get(projectId as string, signal),
    enabled: Boolean(projectId),
  })

  const kbQuery = useQuery({
    queryKey: ['knowledge-bases', { project: projectId }],
    queryFn: ({ signal }) => api.knowledgeBases.list({ project_id: projectId, page_size: 50 }, signal),
    enabled: Boolean(projectId) && tab === 'knowledge',
  })

  const tasksQuery = useQuery({
    queryKey: ['tasks', { project: projectId }],
    queryFn: ({ signal }) => api.tasks.list({ project_id: projectId, page_size: 50 }, signal),
    enabled: Boolean(projectId) && tab === 'tasks',
  })

  const notesQuery = useQuery({
    queryKey: ['notes', { project: projectId }],
    queryFn: ({ signal }) => api.notes.list({ project_id: projectId, page_size: 50 }, signal),
    enabled: Boolean(projectId) && tab === 'notes',
  })

  const runsQuery = useQuery({
    queryKey: ['ai-runs', { project: projectId }],
    queryFn: ({ signal }) => api.aiRuns.list({ project_id: projectId, page_size: 50 }, signal),
    enabled: Boolean(projectId) && tab === 'runs',
  })

  if (!projectId) return null

  return (
    <PageContainer className="space-y-4">
      <Link
        to="/projects"
        className="inline-flex items-center gap-1.5 text-2xs text-muted transition-colors hover:text-primary"
      >
        <ArrowLeft className="size-3" />
        返回项目列表
      </Link>

      {projectQuery.isLoading ? (
        <Skeleton className="h-24" />
      ) : projectQuery.isError ? (
        <ErrorState title="项目加载失败" error={projectQuery.error} onRetry={() => void projectQuery.refetch()} />
      ) : projectQuery.data ? (
        <>
          <SectionHeader
            title={
              <span className="flex items-center gap-2">
                <span
                  className="flex size-6 items-center justify-center rounded-md border border-subtle"
                  style={{ backgroundColor: `${projectQuery.data.color}22`, color: projectQuery.data.color }}
                >
                  <FolderKanban className="size-3.5" />
                </span>
                {projectQuery.data.name}
              </span>
            }
            description={projectQuery.data.description || '该项目下的知识、任务与运行记录'}
          />

          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            <StatTile
              label="知识库 / 文档"
              value={`${projectQuery.data.stats.knowledge_bases} / ${projectQuery.data.stats.documents}`}
              sub={`${formatNumber(projectQuery.data.stats.chunks)} 个切片`}
              icon={<Layers className="size-3" />}
              tone="accent"
            />
            <StatTile
              label="任务"
              value={`${projectQuery.data.stats.tasks_done}/${projectQuery.data.stats.tasks_total}`}
              sub={`待办 ${projectQuery.data.stats.tasks_open} 项`}
              icon={<ListChecks className="size-3" />}
              tone={projectQuery.data.stats.tasks_open > 0 ? 'info' : 'success'}
            />
            <StatTile
              label="笔记"
              value={formatNumber(projectQuery.data.stats.notes)}
              sub="AI 生成与手写"
              icon={<NotebookPen className="size-3" />}
              tone="neutral"
            />
            <StatTile
              label="AI 运行"
              value={formatNumber(projectQuery.data.stats.ai_runs)}
              sub={`更新于 ${relativeTime(projectQuery.data.updated_at)}`}
              icon={<Activity className="size-3" />}
              tone="accent"
            />
          </div>
        </>
      ) : null}

      <Tabs
        value={tab}
        onChange={(key) => setTab(key as TabKey)}
        items={[
          { key: 'knowledge', label: '知识库', icon: <Layers className="size-3" /> },
          { key: 'tasks', label: '任务', icon: <ListChecks className="size-3" /> },
          { key: 'notes', label: '笔记', icon: <NotebookPen className="size-3" /> },
          { key: 'runs', label: 'AI 运行', icon: <Activity className="size-3" /> },
        ]}
      />

      {tab === 'knowledge' ? (
        kbQuery.isLoading ? (
          <Skeleton className="h-40" />
        ) : kbQuery.isError ? (
          <ErrorState error={kbQuery.error} onRetry={() => void kbQuery.refetch()} />
        ) : (kbQuery.data?.items ?? []).length === 0 ? (
          <EmptyTab
            icon={<Layers className="size-4" />}
            title="该项目还没有知识库"
            description="在知识库页面创建时选择该项目即可关联。"
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {(kbQuery.data?.items ?? []).map((base) => (
              <Card key={base.id}>
                <CardBody className="space-y-1.5">
                  <Link
                    to={`/knowledge/${base.id}/documents`}
                    className="block truncate text-xs font-medium text-primary hover:underline"
                  >
                    {base.name}
                  </Link>
                  <p className="line-clamp-2 text-2xs text-muted">{base.description || '暂无描述'}</p>
                  <div className="flex flex-wrap gap-1">
                    <Badge tone="neutral" mono>
                      {base.embedding_model}
                    </Badge>
                    <Badge tone="neutral">{base.vector_size}d</Badge>
                    {base.is_archived ? <Badge tone="neutral">已归档</Badge> : null}
                  </div>
                </CardBody>
              </Card>
            ))}
          </div>
        )
      ) : null}

      {tab === 'tasks' ? (
        tasksQuery.isLoading ? (
          <SkeletonTable rows={6} cols={4} />
        ) : tasksQuery.isError ? (
          <ErrorState error={tasksQuery.error} onRetry={() => void tasksQuery.refetch()} />
        ) : (tasksQuery.data?.items ?? []).length === 0 ? (
          <EmptyTab
            icon={<ListChecks className="size-4" />}
            title="该项目还没有任务"
            description="使用任务规划器把目标拆解成任务，或手动创建。"
          />
        ) : (
          <Card className="overflow-hidden">
            <ul className="divide-y divide-subtle">
              {(tasksQuery.data?.items ?? []).map((task) => (
                <li key={task.id}>
                  <Link
                    to={`/tasks?task=${task.id}`}
                    className="flex items-center gap-2.5 px-3.5 py-2 transition-colors hover:bg-elevated"
                  >
                    <span className="min-w-0 flex-1 truncate text-xs text-primary">{task.title}</span>
                    <PriorityBadge priority={task.priority} />
                    <TaskStatusBadge status={task.status} />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        )
      ) : null}

      {tab === 'notes' ? (
        notesQuery.isLoading ? (
          <Skeleton className="h-40" />
        ) : notesQuery.isError ? (
          <ErrorState error={notesQuery.error} onRetry={() => void notesQuery.refetch()} />
        ) : (notesQuery.data?.items ?? []).length === 0 ? (
          <EmptyTab
            icon={<NotebookPen className="size-4" />}
            title="该项目还没有笔记"
            description="在笔记页面创建时会自动带上项目标签。"
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {(notesQuery.data?.items ?? []).map((note) => (
              <Card key={note.id}>
                <CardBody className="space-y-1.5">
                  <Link
                    to={`/notes?note=${note.id}`}
                    className="block truncate text-xs font-medium text-primary hover:underline"
                  >
                    {note.title}
                  </Link>
                  <p className="line-clamp-3 text-2xs text-muted">{note.content_md.slice(0, 160)}</p>
                  <div className="flex items-center gap-1.5">
                    {note.is_ai_generated ? <Badge tone="accent">AI 生成</Badge> : null}
                    {note.is_pinned ? <Badge tone="warn">置顶</Badge> : null}
                    <span className="text-2xs text-faint">{relativeTime(note.updated_at)}</span>
                  </div>
                </CardBody>
              </Card>
            ))}
          </div>
        )
      ) : null}

      {tab === 'runs' ? (
        runsQuery.isLoading ? (
          <SkeletonTable rows={8} cols={5} />
        ) : runsQuery.isError ? (
          <ErrorState error={runsQuery.error} onRetry={() => void runsQuery.refetch()} />
        ) : (runsQuery.data?.items ?? []).length === 0 ? (
          <EmptyTab
            icon={<Activity className="size-4" />}
            title="该项目还没有 AI 运行"
            description="在问答、规划或 Agent 中选择该项目后，运行记录会出现在这里。"
          />
        ) : (
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-xs">
                <thead className="border-b border-subtle bg-elevated text-2xs uppercase tracking-wide text-faint">
                  <tr>
                    <th className="px-3 py-2 font-medium">类型</th>
                    <th className="w-[110px] px-3 py-2 font-medium">状态</th>
                    <th className="w-[90px] px-3 py-2 text-right font-medium">延迟</th>
                    <th className="w-[90px] px-3 py-2 text-right font-medium">Tokens</th>
                    <th className="w-[120px] px-3 py-2 font-medium">时间</th>
                  </tr>
                </thead>
                <tbody>
                  {(runsQuery.data?.items ?? []).map((run) => (
                    <tr key={run.run_id} className="border-b border-subtle last:border-b-0 hover:bg-elevated">
                      <td className="px-3 py-2">
                        <Link
                          to={`/ai-runs/${run.run_id}`}
                          className="flex items-center gap-2 text-primary hover:text-accent hover:underline"
                        >
                          <Dot tone={runStatusTone(run.status)} />
                          {runTypeLabel(run.run_type)}
                        </Link>
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone={runStatusTone(run.status)}>{run.status}</Badge>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted">
                        {formatMs(run.latency_ms)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-muted">
                        {formatNumber(run.total_tokens)}
                      </td>
                      <td className="px-3 py-2 text-faint">{relativeTime(run.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )
      ) : null}
    </PageContainer>
  )
}
