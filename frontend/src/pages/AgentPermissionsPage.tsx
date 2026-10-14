/**
 * Settings -> Agent Permissions.
 *
 * This screen is a control surface, not documentation. Every switch writes to the
 * backend policy store, and the next agent run is governed by it — the tool count
 * and the available capability list on this page are re-read from the server after
 * each change, so what is displayed is what the agent will actually get.
 *
 * The denied-path list is shown as read-only on purpose. Those entries are merged
 * in from the built-in policy on every load, so a user can add protections but
 * cannot remove them from here; presenting them as editable would be a lie.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  FolderLock,
  Globe,
  Info,
  ShieldCheck,
  ShieldAlert,
  Wrench,
} from 'lucide-react'

import { Badge, Card, CardBody, CardHeader, PageContainer, SectionHeader } from '@/components/ui/primitives'
import { Button } from '@/components/ui/Button'
import { ErrorState, InlineAlert, LoadingBlock } from '@/components/ui/feedback'
import { Switch } from '@/components/ui/form'
import { agentApi } from '@/lib/agentApi'
import type { AgentPermissions, Capability, ToolRow } from '@/lib/agentApi'
import { toApiError } from '@/lib/api'
import { cn } from '@/lib/utils'

const MODE_TONE: Record<string, string> = {
  assist: 'border-info/40 bg-info/10',
  supervised: 'border-accent/40 bg-accent/10',
  autonomous: 'border-warn/40 bg-warn/10',
}

const RISK_TONE: Record<string, string> = {
  low: 'text-success',
  medium: 'text-info',
  high: 'text-warn',
  critical: 'text-danger',
}

export default function AgentPermissionsPage() {
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)
  const [tab, setTab] = useState<'capabilities' | 'tools' | 'paths'>('capabilities')

  const permissions = useQuery<AgentPermissions>({
    queryKey: ['agent', 'permissions'],
    queryFn: () => agentApi.permissions(),
  })

  const capabilities = useQuery<{
    capabilities: Capability[]
    tool_count: number
    level_label: string
    mode: string
  }>({
    queryKey: ['agent', 'capabilities'],
    queryFn: () => agentApi.capabilities(),
  })

  const tools = useQuery<{
    tools: ToolRow[]
    count: number
    available: number
    excluded: { name: string; category: string; permission_label: string; reason: string }[]
  }>({
    queryKey: ['agent', 'tools'],
    queryFn: () => agentApi.tools(),
    enabled: tab === 'tools',
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['agent'] })
  }

  const setMode = useMutation({
    mutationFn: (mode: string) => agentApi.updatePermissions({ mode }),
    onSuccess: () => {
      setNotice('模式已更新，下一次 Agent 运行立即生效。')
      invalidate()
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const setLevel = useMutation({
    mutationFn: (level: number) => agentApi.updatePermissions({ level }),
    onSuccess: () => {
      setNotice('权限级别已更新。')
      invalidate()
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const toggleCapability = useMutation({
    mutationFn: (patch: Record<string, boolean>) => agentApi.updateCapabilities(patch),
    onSuccess: () => {
      setNotice('能力开关已更新。')
      invalidate()
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  const reset = useMutation({
    mutationFn: () => agentApi.resetPermissions(),
    onSuccess: () => {
      setNotice('已恢复默认权限策略。')
      invalidate()
    },
    onError: (exc) => setNotice(toApiError(exc).message),
  })

  useEffect(() => {
    if (notice) {
      const timer = window.setTimeout(() => setNotice(null), 6000)
      return () => window.clearTimeout(timer)
    }
    return undefined
  }, [notice])

  if (permissions.isLoading) return <LoadingBlock label="读取权限策略…" />
  if (permissions.isError) {
    return (
      <ErrorState
        title="无法读取权限策略"
        error={permissions.error}
        onRetry={() => void permissions.refetch()}
      />
    )
  }

  const policy = permissions.data!
  const busy = setMode.isPending || setLevel.isPending || toggleCapability.isPending

  return (
    <PageContainer className="space-y-5">
      <SectionHeader
        title="Agent 权限"
        description="决定 Agent 能在你的电脑上做什么。改动会立即作用于下一次运行。"
        actions={
          <Button size="sm" variant="secondary" disabled={reset.isPending} onClick={() => reset.mutate()}>
            恢复默认
          </Button>
        }
      />

      {notice && <InlineAlert tone="info" title="已更新">{notice}</InlineAlert>}

      {/* --------------------------------------------------------------- mode -- */}
      <Card>
        <CardHeader
          title="Agent 模式"
          description="决定自主程度，以及是否需要逐步确认"
          actions={
            <Badge tone={policy.source === 'runtime' ? 'accent' : 'neutral'}>
              {policy.source === 'runtime' ? '已自定义' : '默认值'}
            </Badge>
          }
        />
        <CardBody className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-3">
            {(policy.modes ?? []).map((mode) => {
              const active = policy.mode === mode.value
              return (
                <button
                  key={mode.value}
                  type="button"
                  disabled={busy}
                  onClick={() => setMode.mutate(mode.value)}
                  className={cn(
                    'rounded-xl border p-4 text-left transition-all disabled:opacity-60',
                    active
                      ? MODE_TONE[mode.value] ?? 'border-accent/40 bg-accent/10'
                      : 'border-border hover:border-border-strong',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[14px] font-medium text-text">{mode.label}</span>
                    {active && <CheckCircle2 className="size-4 text-accent" />}
                  </div>
                  <p className="mt-1 text-[12px] leading-relaxed text-muted">
                    {mode.description}
                  </p>
                </button>
              )
            })}
          </div>
          {policy.mode === 'autonomous' && (
            <InlineAlert tone="warn" title="自主模式不等于绕过权限">
              Agent 会自主完成目标，但高风险操作仍然需要你确认，受保护路径依然不可访问。
            </InlineAlert>
          )}
        </CardBody>
      </Card>

      {/* -------------------------------------------------------------- level -- */}
      <Card>
        <CardHeader
          title="权限级别"
          description={`当前：${policy.level_label} · Agent 可用 ${capabilities.data?.tool_count ?? '—'} 个工具`}
        />
        <CardBody className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-4">
            {(policy.levels ?? []).map((level) => {
              const active = policy.level === level.value
              return (
                <button
                  key={level.value}
                  type="button"
                  disabled={busy}
                  onClick={() => setLevel.mutate(level.value)}
                  className={cn(
                    'rounded-lg border px-3 py-2.5 text-left transition-all disabled:opacity-60',
                    active ? 'border-accent/50 bg-accent/10' : 'border-border hover:border-border-strong',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-medium text-text">{level.label}</span>
                    {active && <CheckCircle2 className="size-3.5 text-accent" />}
                  </div>
                  <p className="mt-0.5 font-mono text-[11px] text-faint">LEVEL {level.value}</p>
                </button>
              )
            })}
          </div>
          {policy.level >= 2 && (
            <InlineAlert tone="warn" title="已授予系统级权限">
              Agent 可以执行 shell 命令、安装依赖、访问网络。建议只在需要时临时开启。
            </InlineAlert>
          )}
        </CardBody>
      </Card>

      {/* --------------------------------------------------------------- tabs -- */}
      <div className="flex gap-1 border-b border-border">
        {(
          [
            ['capabilities', '能力开关'],
            ['tools', '工具清单'],
            ['paths', '路径与网络'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={cn(
              '-mb-px border-b-2 px-3.5 py-2 text-[13px] transition-colors',
              tab === key
                ? 'border-accent text-text'
                : 'border-transparent text-muted hover:text-text',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'capabilities' && (
        <Card>
          <CardBody className="divide-y divide-border/50 p-0">
            {(capabilities.data?.capabilities ?? []).map((capability) => {
              const blockedByLevel = !capability.enabled && !capability.user_enabled
              return (
                <div key={capability.key} className="flex items-start gap-4 px-4 py-3.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13.5px] font-medium text-text">
                        {capability.label}
                      </span>
                      {capability.enabled ? (
                        <Badge tone="success">已启用</Badge>
                      ) : (
                        <Badge tone="neutral">
                          {blockedByLevel ? `需要 ${capability.required_level_label}` : '已关闭'}
                        </Badge>
                      )}
                    </div>
                    <p className="mt-0.5 text-[12px] text-muted">{capability.description}</p>
                    <p className="mt-1 font-mono text-[11px] text-faint">
                      {capability.tools.length} 个工具 · {capability.tools.slice(0, 4).join(', ')}
                      {capability.tools.length > 4 && ' …'}
                    </p>
                  </div>
                  <Switch
                    checked={capability.user_enabled}
                    disabled={busy}
                    onChange={(next) => toggleCapability.mutate({ [capability.key]: next })}
                    className="mt-1"
                  />
                </div>
              )
            })}
          </CardBody>
        </Card>
      )}

      {tab === 'tools' && (
        <>
          {tools.isLoading && <LoadingBlock label="读取工具清单…" />}
          {tools.data && (
            <>
              <Card>
                <CardHeader
                  title="当前可用工具"
                  description={`${tools.data.available} 个可用 · 共 ${tools.data.count} 个`}
                  actions={<Wrench className="size-4 text-muted" />}
                />
                <CardBody className="divide-y divide-border/50 p-0">
                  {tools.data.tools.slice(0, 60).map((tool) => (
                    <div key={tool.name} className="flex items-start gap-3 px-4 py-2.5">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[12.5px] text-text">{tool.name}</span>
                          <Badge tone={tool.source === 'computer' ? 'accent' : 'neutral'}>
                            {tool.source === 'computer' ? '计算机' : '业务'}
                          </Badge>
                          <span className={cn('text-[11px]', RISK_TONE[tool.risk_level])}>
                            {tool.risk_level}
                          </span>
                          {tool.mutates_state && <span className="text-[11px] text-warn">写</span>}
                          {tool.requires_confirmation && (
                            <span className="text-[11px] text-warn">需确认</span>
                          )}
                        </div>
                        <p className="mt-0.5 line-clamp-1 text-[11.5px] text-muted">
                          {tool.description}
                        </p>
                      </div>
                    </div>
                  ))}
                </CardBody>
              </Card>

              {tools.data.excluded.length > 0 && (
                <Card>
                  <CardHeader
                    title="因权限级别不可用"
                    description={`${tools.data.excluded.length} 个工具需要更高权限`}
                    actions={<ShieldAlert className="size-4 text-warn" />}
                  />
                  <CardBody className="divide-y divide-border/50 p-0">
                    {tools.data.excluded.map((item) => (
                      <div key={item.name} className="flex items-center gap-3 px-4 py-2">
                        <span className="font-mono text-[12px] text-muted">{item.name}</span>
                        <span className="text-[11px] text-faint">{item.category}</span>
                        <span className="ml-auto text-[11px] text-warn">{item.reason}</span>
                      </div>
                    ))}
                  </CardBody>
                </Card>
              )}
            </>
          )}
        </>
      )}

      {tab === 'paths' && (
        <>
          <Card>
            <CardHeader
              title="工作区"
              description="Agent 相对路径的基准目录，始终可写"
              actions={<ShieldCheck className="size-4 text-success" />}
            />
            <CardBody>
              <p className="rounded-lg border border-border bg-inset px-3 py-2 font-mono text-[12.5px] text-text">
                {policy.workspace}
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="允许访问的目录" description="Agent 可以在这些目录内读写" />
            <CardBody className="space-y-1.5">
              {policy.allowed_paths.map((path) => (
                <p
                  key={path}
                  className="rounded-lg border border-border bg-inset px-3 py-1.5 font-mono text-[12px] text-text"
                >
                  {path}
                </p>
              ))}
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="受保护路径（只读）"
              description={`${policy.denied_paths.length} 个位置永远不可访问，设置无法解除`}
              actions={<FolderLock className="size-4 text-danger" />}
            />
            <CardBody className="space-y-1">
              <div className="max-h-64 overflow-y-auto">
                {policy.denied_paths.map((path) => (
                  <p key={path} className="truncate font-mono text-[11.5px] text-faint">
                    {path}
                  </p>
                ))}
              </div>
              <InlineAlert tone="info" title="为什么这些不可修改">
                凭据、浏览器配置和系统目录由内置策略强制保护。它们会与你的设置合并，
                因此任何设置都无法把 <code>.ssh</code> 或项目 <code>.env</code> 变成可读。
              </InlineAlert>
            </CardBody>
          </Card>

          <Card>
            <CardHeader
              title="网络白名单"
              description="Agent 只能访问这些域名"
              actions={<Globe className="size-4 text-info" />}
            />
            <CardBody>
              <div className="flex flex-wrap gap-1.5">
                {policy.allowed_domains.map((domain) => (
                  <span
                    key={domain}
                    className="rounded-full border border-border px-2.5 py-0.5 font-mono text-[11.5px] text-muted"
                  >
                    {domain}
                  </span>
                ))}
              </div>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="资源上限" description="防止 Agent 无限运行" actions={<Info className="size-4 text-muted" />} />
            <CardBody className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {Object.entries(policy.limits).map(([key, value]) => (
                <div key={key} className="rounded-lg border border-border bg-inset/50 px-3 py-2">
                  <p className="text-[10.5px] uppercase tracking-wide text-faint">{key}</p>
                  <p className="mt-0.5 font-mono text-[13px] text-text">{value}</p>
                </div>
              ))}
            </CardBody>
          </Card>
        </>
      )}
    </PageContainer>
  )
}
