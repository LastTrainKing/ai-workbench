import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  CheckCircle2,
  Database,
  Moon,
  RefreshCw,
  RotateCcw,
  Save,
  Server,
  Sun,
  User as UserIcon,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cn, formatNumber, relativeTime } from '@/lib/utils'
import { Button, IconButton } from '@/components/ui/Button'
import { ColorPicker, Field, Input, Select, Switch, Textarea } from '@/components/ui/form'
import { confirm } from '@/components/ui/overlay'
import {
  Avatar,
  Badge,
  Card,
  CardBody,
  CardHeader,
  Dot,
  KeyValue,
  PageContainer,
  SectionHeader,
  StatTile,
  Tabs,
} from '@/components/ui/primitives'
import { EmptyState, ErrorState, InlineAlert, Skeleton, SkeletonText } from '@/components/ui/feedback'
import { useAuthStore } from '@/store/auth'
import { useUiStore } from '@/store/ui'
import { useToastStore, toastApiError } from '@/store/toast'

type TabKey = 'profile' | 'system' | 'prompts' | 'metrics'

/* --------------------------------------------------------------- profile */

function ProfileSection() {
  const user = useAuthStore((state) => state.user)
  const updateProfile = useAuthStore((state) => state.updateProfile)
  const success = useToastStore((state) => state.success)

  const [displayName, setDisplayName] = useState('')
  const [avatarColor, setAvatarColor] = useState('#6366f1')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!user) return
    setDisplayName(user.display_name ?? '')
    setAvatarColor(user.avatar_color || '#6366f1')
  }, [user])

  const dirty = Boolean(user) && (displayName !== user?.display_name || avatarColor !== user?.avatar_color)

  const save = async () => {
    setSaving(true)
    try {
      await updateProfile({ display_name: displayName, avatar_color: avatarColor })
      success('资料已更新')
    } catch (error) {
      toastApiError(error, '更新资料失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Card>
        <CardHeader title="个人资料" description="显示名称与头像颜色" />
        <CardBody className="space-y-3.5">
          <div className="flex items-center gap-3">
            <Avatar
              name={displayName || user?.username || '?'}
              color={avatarColor}
              size={44}
            />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-primary">
                {displayName || user?.username}
              </p>
              <p className="truncate text-2xs text-faint">{user?.email}</p>
            </div>
          </div>

          <Field label="显示名称" htmlFor="profile-name">
            <Input
              id="profile-name"
              value={displayName}
              maxLength={128}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </Field>

          <Field label="头像颜色">
            <ColorPicker value={avatarColor} onChange={setAvatarColor} />
          </Field>

          <Button
            variant="primary"
            size="sm"
            icon={<Save className="size-3.5" />}
            loading={saving}
            disabled={!dirty}
            onClick={() => void save()}
          >
            {dirty ? '保存修改' : '已保存'}
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="账号信息" />
        <CardBody>
          <KeyValue
            items={[
              { key: '用户名', value: user?.username ?? '—' },
              { key: '邮箱', value: user?.email ?? '—' },
              {
                key: '用户 ID',
                value: <span className="font-mono text-2xs">{user?.id ?? '—'}</span>,
              },
              { key: '状态', value: user?.is_active ? '正常' : '已禁用' },
              { key: '注册时间', value: user?.created_at ? relativeTime(user.created_at) : '—' },
            ]}
          />
        </CardBody>
      </Card>
    </div>
  )
}

/* ---------------------------------------------------------------- system */

function SystemSection() {
  const theme = useUiStore((state) => state.theme)
  const toggleTheme = useUiStore((state) => state.toggleTheme)

  const deps = useQuery({
    queryKey: ['health', 'deps'],
    queryFn: ({ signal }) => api.health.deps(signal),
    staleTime: 30_000,
  })

  const health = useQuery({
    queryKey: ['health', 'basic'],
    queryFn: ({ signal }) => api.health.basic(signal),
    staleTime: 60_000,
  })

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader title="外观" />
        <CardBody>
          <Switch
            checked={theme === 'dark'}
            onChange={() => toggleTheme()}
            label={theme === 'dark' ? '深色主题（默认）' : '浅色主题'}
            description="主题偏好会保存在 localStorage，刷新后保持。"
          />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="依赖状态"
          description="PostgreSQL / Redis / Qdrant / AI Provider"
          actions={
            <IconButton label="刷新" size="xs" onClick={() => void deps.refetch()}>
              <RefreshCw className={cn('size-3.5', deps.isFetching && 'animate-spin')} />
            </IconButton>
          }
        />
        <CardBody className="space-y-3">
          {deps.isLoading ? (
            <SkeletonText lines={4} />
          ) : deps.isError ? (
            <ErrorState compact error={deps.error} onRetry={() => void deps.refetch()} />
          ) : deps.data ? (
            <>
              {deps.data.ai_degraded ? (
                <InlineAlert tone="warn" title="AI 处于离线降级模式">
                  {deps.data.ai.llm.reason || '未配置云端 API Key，正在使用确定性离线引擎。'}
                </InlineAlert>
              ) : (
                <InlineAlert tone="success" title="AI Provider 已连接">
                  {deps.data.ai.llm.kind} · {deps.data.ai.llm.model}
                </InlineAlert>
              )}

              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  { name: 'PostgreSQL', info: deps.data.postgres },
                  { name: 'Redis', info: deps.data.redis },
                  { name: 'Qdrant', info: deps.data.qdrant },
                ].map((item) => (
                  <div key={item.name} className="rounded border border-subtle bg-elevated px-2.5 py-2">
                    <div className="flex items-center gap-1.5">
                      <Dot tone={item.info.ok ? 'success' : 'danger'} />
                      <span className="text-2xs font-medium text-primary">{item.name}</span>
                    </div>
                    <p className="mt-0.5 truncate text-2xs text-faint" title={item.info.detail}>
                      {item.info.detail || (item.info.ok ? 'ok' : 'unavailable')}
                    </p>
                  </div>
                ))}
                <div className="rounded border border-subtle bg-elevated px-2.5 py-2">
                  <div className="flex items-center gap-1.5">
                    <Dot tone={deps.data.ai.llm.degraded ? 'warn' : 'success'} />
                    <span className="text-2xs font-medium text-primary">LLM</span>
                  </div>
                  <p className="mt-0.5 truncate text-2xs text-faint">{deps.data.ai.llm.model}</p>
                </div>
                <div className="rounded border border-subtle bg-elevated px-2.5 py-2">
                  <div className="flex items-center gap-1.5">
                    <Dot tone={deps.data.ai.embedding.degraded ? 'warn' : 'success'} />
                    <span className="text-2xs font-medium text-primary">Embedding</span>
                  </div>
                  <p className="mt-0.5 truncate text-2xs text-faint">
                    {deps.data.ai.embedding.model} · {deps.data.ai.embedding_dimension}d
                  </p>
                </div>
              </div>

              <div>
                <p className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-faint">
                  运行配置
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(deps.data.config ?? {}).map(([key, value]) => (
                    <Badge key={key} tone="neutral" mono>
                      {key}={String(value)}
                    </Badge>
                  ))}
                </div>
              </div>

              <p className="text-2xs text-faint">
                工具调用：{deps.data.ai.tool_calling ? '已启用' : '未启用'}
              </p>
            </>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="应用信息" />
        <CardBody>
          {health.isLoading ? (
            <Skeleton className="h-16" />
          ) : health.data ? (
            <KeyValue
              columns={2}
              items={[
                { key: '应用', value: health.data.app },
                { key: '版本', value: health.data.version },
                { key: '环境', value: health.data.env },
                { key: '状态', value: health.data.status },
              ]}
            />
          ) : (
            <p className="text-2xs text-faint">无法读取 /health。</p>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

/* --------------------------------------------------------------- prompts */

function PromptManager() {
  const queryClient = useQueryClient()
  const success = useToastStore((state) => state.success)
  const [selected, setSelected] = useState('')
  const [content, setContent] = useState('')
  const [changeNote, setChangeNote] = useState('')
  const [activate, setActivate] = useState(true)

  const list = useQuery({
    queryKey: ['prompts'],
    queryFn: ({ signal }) => api.prompts.list(signal),
    staleTime: 30_000,
  })

  useEffect(() => {
    if (!selected && list.data && list.data.length > 0) setSelected(list.data[0].name)
  }, [list.data, selected])

  const detail = useQuery({
    queryKey: ['prompts', selected],
    queryFn: ({ signal }) => api.prompts.get(selected, signal),
    enabled: Boolean(selected),
  })

  useEffect(() => {
    if (detail.data) {
      const current = detail.data.versions.find((version) => version.is_current)
      setContent(current?.content ?? detail.data.builtin_content)
      setChangeNote('')
    }
  }, [detail.data])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['prompts'] })
  }

  const createVersion = useMutation({
    mutationFn: () =>
      api.prompts.createVersion(selected, { content, change_note: changeNote, activate }),
    onSuccess: (result) => {
      success(`已创建版本 v${result.data.version}`)
      invalidate()
      setChangeNote('')
    },
    onError: (error) => toastApiError(error, '创建版本失败'),
  })

  const activateVersion = useMutation({
    mutationFn: (version: number) => api.prompts.activateVersion(selected, version),
    onSuccess: (_result, version) => {
      success(`已切换到 v${version}`)
      invalidate()
    },
    onError: (error) => toastApiError(error, '切换版本失败'),
  })

  const reset = useMutation({
    mutationFn: () => api.prompts.reset(selected),
    onSuccess: () => {
      success('已恢复内置模板')
      invalidate()
    },
    onError: (error) => toastApiError(error, '恢复内置模板失败'),
  })

  if (list.isLoading) return <SkeletonText lines={8} />
  if (list.isError) return <ErrorState error={list.error} onRetry={() => void list.refetch()} />
  if (!list.data || list.data.length === 0) {
    return (
      <EmptyState
        icon={<Server className="size-4" />}
        title="还没有提示词模板"
        description="后端启动时会自动注册内置模板，请确认服务已正常运行。"
        className="border-0"
      />
    )
  }

  const currentVersion = detail.data?.versions.find((version) => version.is_current)

  return (
    <div className="grid gap-3 lg:grid-cols-[280px_minmax(0,1fr)]">
      <div className="space-y-2">
        <Select value={selected} onChange={(event) => setSelected(event.target.value)} aria-label="选择提示词">
          {list.data.map((prompt) => (
            <option key={prompt.name} value={prompt.name}>
              {prompt.name}
            </option>
          ))}
        </Select>

        <ul className="space-y-1">
          {list.data.map((prompt) => (
            <li key={prompt.name}>
              <button
                type="button"
                onClick={() => setSelected(prompt.name)}
                className={cn(
                  'w-full rounded-md border px-2.5 py-2 text-left transition-colors',
                  selected === prompt.name
                    ? 'border-accent/50 bg-accent-soft'
                    : 'border-subtle bg-surface hover:border-strong',
                )}
              >
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate font-mono text-2xs text-primary">
                    {prompt.name}
                  </span>
                  {prompt.is_active ? <Badge tone="success">启用</Badge> : null}
                </div>
                <p className="mt-0.5 truncate text-2xs text-faint">{prompt.description || prompt.category}</p>
                <p className="mt-0.5 text-2xs text-faint">
                  v{prompt.current_version} / {prompt.version_count} 个版本
                </p>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="space-y-3">
        {detail.isLoading ? (
          <Skeleton className="h-64" />
        ) : detail.isError ? (
          <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
        ) : detail.data ? (
          <>
            <Card>
              <CardHeader
                title={detail.data.name}
                description={detail.data.description || detail.data.category}
                actions={
                  <>
                    <Badge tone={detail.data.json_output ? 'info' : 'neutral'}>
                      {detail.data.json_output ? 'JSON 输出' : '文本输出'}
                    </Badge>
                    <Button
                      size="xs"
                      variant="ghost"
                      icon={<RotateCcw className="size-3" />}
                      loading={reset.isPending}
                      onClick={async () => {
                        const ok = await confirm({
                          title: '恢复内置模板',
                          description: '会新建一个版本，内容为后端内置模板。',
                          confirmLabel: '恢复',
                        })
                        if (ok) reset.mutate()
                      }}
                    >
                      恢复内置
                    </Button>
                  </>
                }
              />
              <CardBody className="space-y-3">
                <div className="flex flex-wrap gap-1.5">
                  <Badge tone="accent">当前 v{currentVersion?.version ?? '—'}</Badge>
                  {(currentVersion?.variables ?? []).map((variable) => (
                    <Badge key={variable} tone="neutral" mono>
                      {'{'}
                      {variable}
                      {'}'}
                    </Badge>
                  ))}
                </div>

                <Field
                  label="模板内容"
                  hint="使用 {variable} 占位符；保存会创建一个新版本。"
                >
                  <Textarea
                    rows={12}
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    className="font-mono text-xs"
                  />
                </Field>

                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
                  <Field label="变更说明">
                    <Input
                      value={changeNote}
                      maxLength={500}
                      onChange={(event) => setChangeNote(event.target.value)}
                      placeholder="例如：增强引用约束"
                    />
                  </Field>
                  <Switch checked={activate} onChange={setActivate} label="保存后立即启用" />
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    icon={<Save className="size-3.5" />}
                    loading={createVersion.isPending}
                    disabled={content.trim().length < 10}
                    onClick={() => createVersion.mutate()}
                  >
                    创建新版本
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setContent(detail.data.builtin_content)}
                  >
                    载入内置内容
                  </Button>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title={`版本历史（${detail.data.versions.length}）`} />
              <CardBody className="p-0">
                <ul className="divide-y divide-subtle">
                  {[...detail.data.versions]
                    .sort((a, b) => b.version - a.version)
                    .map((version) => (
                      <li key={version.version} className="flex flex-wrap items-center gap-2 px-3.5 py-2">
                        <Badge tone={version.is_current ? 'success' : 'neutral'} mono>
                          v{version.version}
                        </Badge>
                        <span className="min-w-0 flex-1 truncate text-2xs text-muted">
                          {version.change_note || '（无说明）'}
                        </span>
                        <span className="text-2xs text-faint">
                          {version.created_by} · {relativeTime(version.created_at)}
                        </span>
                        {version.is_current ? (
                          <CheckCircle2 className="size-3.5 text-success" />
                        ) : (
                          <Button
                            size="xs"
                            variant="ghost"
                            loading={activateVersion.isPending}
                            onClick={() => activateVersion.mutate(version.version)}
                          >
                            启用
                          </Button>
                        )}
                      </li>
                    ))}
                </ul>
              </CardBody>
            </Card>
          </>
        ) : null}
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- metrics */

function MetricsSection() {
  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['metrics'],
    queryFn: ({ signal }) => api.health.metrics(signal),
    staleTime: 20_000,
  })

  if (isLoading) return <SkeletonText lines={6} />
  if (isError) return <ErrorState error={error} onRetry={() => void refetch()} />

  const ai = data?.ai_24h ?? {}
  const cache = data?.cache ?? {}
  const hitRate = typeof cache.hit_rate === 'number' ? cache.hit_rate : 0

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <IconButton label="刷新指标" size="sm" disabled={isFetching} onClick={() => void refetch()}>
          <RefreshCw className={cn('size-3.5', isFetching && 'animate-spin')} />
        </IconButton>
      </div>

      <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
        <StatTile
          label="24h 运行"
          value={formatNumber(ai.total ?? 0)}
          sub={`失败 ${ai.failed ?? 0} 次`}
          icon={<Activity className="size-3" />}
          tone="accent"
        />
        <StatTile
          label="失败率"
          value={`${((ai.failure_rate ?? 0) * 100).toFixed(1)}%`}
          sub={`窗口 ${ai.window_hours ?? 24} 小时`}
          tone={(ai.failure_rate ?? 0) > 0.1 ? 'danger' : 'success'}
        />
        <StatTile
          label="平均延迟"
          value={`${Math.round(ai.avg_latency_ms ?? 0)} ms`}
          sub="24 小时窗口"
          tone="info"
        />
        <StatTile
          label="缓存命中率"
          value={`${(hitRate * 100).toFixed(1)}%`}
          sub={`命中 ${cache.hit ?? 0} / 未命中 ${cache.miss ?? 0}`}
          icon={<Database className="size-3" />}
          tone="neutral"
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader title="AI 运行（24 小时）" />
          <CardBody>
            <KeyValue
              items={Object.entries(ai).map(([key, value]) => ({
                key,
                value: typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—'),
              }))}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="缓存统计" />
          <CardBody>
            <KeyValue
              items={Object.entries(cache).map(([key, value]) => ({
                key,
                value: String(value ?? '—'),
              }))}
            />
          </CardBody>
        </Card>
      </div>

      <p className="text-2xs text-faint">
        指标开关：{data?.enabled ? '已启用' : '已禁用'} · 数据源 {data?.uptime_source ?? 'process'}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ page */

export default function SettingsPage() {
  const [tab, setTab] = useState<TabKey>('profile')
  const theme = useUiStore((state) => state.theme)
  const toggleTheme = useUiStore((state) => state.toggleTheme)

  return (
    <PageContainer className="space-y-4">
      <SectionHeader
        title="设置"
        description="账户资料、模型依赖、提示词模板与运行指标。"
        actions={
          <Button
            size="sm"
            variant="secondary"
            icon={theme === 'dark' ? <Sun className="size-3.5" /> : <Moon className="size-3.5" />}
            onClick={toggleTheme}
          >
            {theme === 'dark' ? '切换到浅色' : '切换到深色'}
          </Button>
        }
      />

      <Tabs
        value={tab}
        onChange={(key) => setTab(key as TabKey)}
        items={[
          { key: 'profile', label: '账户', icon: <UserIcon className="size-3" /> },
          { key: 'system', label: '模型与依赖', icon: <Server className="size-3" /> },
          { key: 'prompts', label: '提示词模板', icon: <Database className="size-3" /> },
          { key: 'metrics', label: '缓存与指标', icon: <Activity className="size-3" /> },
        ]}
      />

      {tab === 'profile' ? <ProfileSection /> : null}
      {tab === 'system' ? <SystemSection /> : null}
      {tab === 'prompts' ? <PromptManager /> : null}
      {tab === 'metrics' ? <MetricsSection /> : null}
    </PageContainer>
  )
}
