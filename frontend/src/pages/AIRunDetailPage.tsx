import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, FileText, Wrench } from 'lucide-react'
import { api } from '@/lib/api'
import { formatDateTime, formatMs, formatNumber, runStatusTone, runTypeLabel } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  CodeBlock,
  KeyValue,
  PageContainer,
  SectionHeader,
  StatTile,
  Tabs,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, InlineAlert, Skeleton } from '@/components/ui/feedback'
import { Markdown } from '@/components/Markdown'

type TabKey = 'overview' | 'request' | 'retrieval' | 'tools' | 'response'

export default function AIRunDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const [tab, setTab] = useState<TabKey>('overview')

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['ai-runs', runId],
    queryFn: ({ signal }) => api.aiRuns.get(runId as string, signal),
    enabled: Boolean(runId),
  })

  if (!runId) return null

  return (
    <PageContainer className="space-y-4">
      <Link
        to="/ai-runs"
        className="inline-flex items-center gap-1.5 text-2xs text-muted transition-colors hover:text-primary"
      >
        <ArrowLeft className="size-3" />
        返回运行列表
      </Link>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-64" />
        </div>
      ) : isError ? (
        <ErrorState title="运行详情加载失败" error={error} onRetry={() => void refetch()} />
      ) : data ? (
        <>
          <SectionHeader
            title={
              <span className="flex flex-wrap items-center gap-2">
                {runTypeLabel(data.run.run_type)}
                <Badge tone={runStatusTone(data.run.status)}>{data.run.status}</Badge>
              </span>
            }
            description={
              <span className="font-mono text-2xs">
                {data.run.run_id} · {formatDateTime(data.run.created_at)}
              </span>
            }
          />

          <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
            <StatTile
              label="延迟"
              value={formatMs(data.run.latency_ms)}
              sub={`重试 ${data.run.retry_count} 次`}
              tone="info"
            />
            <StatTile
              label="Tokens"
              value={formatNumber(data.run.total_tokens)}
              sub={`提示 ${formatNumber(data.run.prompt_tokens)} / 生成 ${formatNumber(
                data.run.completion_tokens,
              )}`}
              tone="accent"
            />
            <StatTile
              label="检索片段"
              value={formatNumber(data.run.retrieval_count)}
              sub={`引用 ${data.sources.length} 条`}
              tone="neutral"
            />
            <StatTile
              label="工具调用"
              value={formatNumber(data.run.tool_call_count)}
              sub={`成本 $${data.run.cost_estimate.toFixed(5)}`}
              tone="neutral"
            />
          </div>

          {data.run.status === 'FAILED' || data.run.error_code ? (
            <InlineAlert tone="danger" title={`运行失败：${data.run.error_code || 'unknown'}`}>
              {data.run.error_message || '后端未提供错误详情'}
            </InlineAlert>
          ) : null}

          <Tabs
            value={tab}
            onChange={(key) => setTab(key as TabKey)}
            items={[
              { key: 'overview', label: '概览' },
              { key: 'request', label: '请求' },
              { key: 'retrieval', label: '检索与引用', count: data.sources.length },
              { key: 'tools', label: '工具调用', count: data.tool_calls.length },
              { key: 'response', label: '响应' },
            ]}
          />

          {tab === 'overview' ? (
            <div className="grid gap-3 lg:grid-cols-2">
              <Card>
                <CardHeader title="运行信息" />
                <CardBody>
                  <KeyValue
                    items={[
                      { key: '类型', value: runTypeLabel(data.run.run_type) },
                      { key: '状态', value: data.run.status },
                      { key: 'Provider', value: data.run.provider || '—' },
                      { key: '模型', value: <span className="font-mono">{data.run.model || '—'}</span> },
                      { key: '提示词', value: data.run.prompt_name || '—' },
                      {
                        key: '提示词版本',
                        value: <span className="font-mono">v{data.run.prompt_version}</span>,
                      },
                      { key: '开始时间', value: formatDateTime(data.run.created_at) },
                      { key: '结束时间', value: formatDateTime(data.run.finished_at) },
                    ]}
                  />
                </CardBody>
              </Card>

              <Card>
                <CardHeader title="检索元数据" />
                <CardBody>
                  {data.retrieval ? (
                    <KeyValue
                      items={Object.entries(data.retrieval).map(([key, value]) => ({
                        key,
                        value:
                          typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—'),
                      }))}
                    />
                  ) : (
                    <p className="text-2xs text-faint">本次运行没有检索元数据。</p>
                  )}
                </CardBody>
              </Card>

              {data.agent ? (
                <Card className="lg:col-span-2">
                  <CardHeader title="Agent 信息" />
                  <CardBody className="space-y-3">
                    <KeyValue
                      columns={2}
                      items={[
                        { key: '目标', value: data.agent.goal },
                        { key: '状态', value: data.agent.status },
                        { key: '已用步数', value: `${data.agent.steps_used} / ${data.agent.max_steps}` },
                        {
                          key: '创建任务',
                          value: String(data.agent.created?.tasks?.length ?? 0),
                        },
                        {
                          key: '创建笔记',
                          value: String(data.agent.created?.notes?.length ?? 0),
                        },
                      ]}
                    />
                    {data.agent.created?.tasks && data.agent.created.tasks.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {data.agent.created.tasks.map((id) => (
                          <Link key={id} to={`/tasks?task=${id}`}>
                            <Badge tone="accent">任务 {id.slice(0, 8)}</Badge>
                          </Link>
                        ))}
                      </div>
                    ) : null}
                  </CardBody>
                </Card>
              ) : null}
            </div>
          ) : null}

          {tab === 'request' ? (
            <div className="grid gap-3 lg:grid-cols-2">
              <Card>
                <CardHeader title="请求载荷" />
                <CardBody>
                  {data.request ? (
                    <CodeBlock value={data.request} maxHeight={420} />
                  ) : (
                    <p className="text-2xs text-faint">没有记录请求载荷。</p>
                  )}
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="结构化结果" />
                <CardBody>
                  {data.result ? (
                    <CodeBlock value={data.result} maxHeight={420} />
                  ) : (
                    <p className="text-2xs text-faint">没有结构化结果。</p>
                  )}
                </CardBody>
              </Card>
            </div>
          ) : null}

          {tab === 'retrieval' ? (
            data.sources.length === 0 ? (
              <EmptyState
                icon={<FileText className="size-4" />}
                title="本次运行没有引用来源"
                description="只有检索类运行（RAG、规划、Agent）才会记录引用片段。"
                className="border-0"
              />
            ) : (
              <ul className="space-y-2">
                {data.sources.map((source, index) => (
                  <li key={`${source.chunk_id ?? index}`}>
                    <Card>
                      <CardBody className="space-y-1.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="flex size-5 items-center justify-center rounded border border-accent/40 bg-accent-soft font-mono text-2xs font-semibold text-accent">
                            {index + 1}
                          </span>
                          {source.document_id ? (
                            <Link
                              to={`/documents?doc=${source.document_id}`}
                              className="truncate text-xs font-medium text-primary hover:text-accent hover:underline"
                            >
                              {source.document_title}
                            </Link>
                          ) : (
                            <span className="truncate text-xs font-medium text-primary">
                              {source.document_title}
                            </span>
                          )}
                          {source.chunk_index !== null ? (
                            <span className="font-mono text-2xs text-faint">#{source.chunk_index}</span>
                          ) : null}
                          <Badge tone="neutral" mono>
                            {source.score.toFixed(4)}
                          </Badge>
                        </div>
                        <p className="whitespace-pre-wrap text-2xs leading-relaxed text-muted">
                          {source.snippet}
                        </p>
                      </CardBody>
                    </Card>
                  </li>
                ))}
              </ul>
            )
          ) : null}

          {tab === 'tools' ? (
            data.tool_calls.length === 0 ? (
              <EmptyState
                icon={<Wrench className="size-4" />}
                title="本次运行没有工具调用"
                description="只有 Agent 运行会记录工具调用轨迹。"
                className="border-0"
              />
            ) : (
              <ul className="space-y-2">
                {data.tool_calls.map((call, index) => (
                  <li key={`${call.step}-${index}`}>
                    <Card>
                      <CardHeader
                        title={
                          <span className="flex items-center gap-2">
                            <span className="font-mono text-2xs text-faint">#{call.step}</span>
                            <Wrench className="size-3 text-accent" />
                            {call.tool}
                          </span>
                        }
                        actions={
                          <>
                            <Badge tone={call.status === 'SUCCEEDED' ? 'success' : 'danger'}>
                              {call.status}
                            </Badge>
                            <Badge tone="neutral">{formatMs(call.latency_ms)}</Badge>
                          </>
                        }
                      />
                      <CardBody className="space-y-2">
                        <div>
                          <p className="mb-1 text-2xs font-semibold uppercase tracking-wider text-faint">
                            参数
                          </p>
                          <CodeBlock value={call.arguments ?? {}} maxHeight={160} />
                        </div>
                        {call.result !== undefined && call.result !== null ? (
                          <div>
                            <p className="mb-1 text-2xs font-semibold uppercase tracking-wider text-faint">
                              返回结果
                            </p>
                            <CodeBlock value={call.result} maxHeight={220} />
                          </div>
                        ) : null}
                        {call.error ? (
                          <InlineAlert tone="danger" title="错误">
                            {call.error}
                          </InlineAlert>
                        ) : null}
                      </CardBody>
                    </Card>
                  </li>
                ))}
              </ul>
            )
          ) : null}

          {tab === 'response' ? (
            <Card>
              <CardHeader title="模型响应" />
              <CardBody>
                {data.response_text ? (
                  <Markdown>{data.response_text}</Markdown>
                ) : (
                  <p className="text-2xs text-faint">没有记录响应文本。</p>
                )}
              </CardBody>
            </Card>
          ) : null}

          <div className="flex justify-end">
            <Button size="sm" variant="ghost" onClick={() => void refetch()}>
              刷新
            </Button>
          </div>
        </>
      ) : null}
    </PageContainer>
  )
}
