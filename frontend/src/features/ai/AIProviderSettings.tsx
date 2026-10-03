/**
 * AI Provider settings — a real control surface, not a decorative form.
 *
 * Changing the provider / model / key here changes which implementation answers
 * the next RAG query, document analysis, plan or agent run: the value is
 * persisted server-side and the provider cache is dropped, so the very next AI
 * call uses the new configuration.
 *
 * The API key is write-only. The server returns only `api_key_set` and a masked
 * hint, and this component never holds the raw value beyond the current edit.
 */
import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  ExternalLink,
  KeyRound,
  Plug,
  RefreshCw,
  RotateCcw,
  Save,
  Zap,
} from 'lucide-react'

import { api, toApiError } from '@/lib/api'
import type { AIConfigPatch, AIProviderId, AIProviderOption } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/Button'
import { Field, Input, Select, Switch } from '@/components/ui/form'
import { Badge, Card, CardBody, CardHeader, Dot } from '@/components/ui/primitives'
import { ErrorState, InlineAlert, SkeletonText } from '@/components/ui/feedback'
import { toastApiError, useToastStore } from '@/store/toast'

/* --------------------------------------------------------------- helpers */

const PROVIDER_ICON: Record<string, typeof Cpu> = {
  deepseek: Zap,
  openai_compatible: Plug,
  ollama: Cpu,
  offline: Cpu,
}

const KIND_LABEL: Record<string, string> = {
  cloud: '云端',
  local: '本地',
  offline: '离线',
}

const SOURCE_LABEL: Record<string, string> = {
  runtime: '界面设置',
  env: '.env',
  default: '默认值',
}

/** What the effective provider actually is, in one honest sentence. */
function effectiveSummary(
  provider: AIProviderId,
  model: string,
  degraded: boolean,
  apiKeySet: boolean,
): { tone: 'ok' | 'warn' | 'danger'; title: string; detail: string } {
  if (degraded) {
    return {
      tone: 'danger',
      title: '当前由离线确定性引擎作答',
      detail:
        '选定的 Provider 不可用（缺少 API Key 或服务未启动），系统已降级。' +
        '离线引擎的答案完全来自检索片段，不会编造，但生成能力有限。',
    }
  }
  if (provider === 'offline') {
    return {
      tone: 'warn',
      title: '当前为 Offline Provider（主动选择）',
      detail: '不调用任何外部服务。答案由内置确定性抽取算法生成。',
    }
  }
  if (provider === 'ollama') {
    return {
      tone: 'ok',
      title: `当前为 Ollama 本地模型：${model}`,
      detail: '完全本地推理，数据不出本机。',
    }
  }
  return {
    tone: 'ok',
    title: `当前为 ${provider === 'deepseek' ? 'DeepSeek' : 'OpenAI 兼容端点'}：${model}`,
    detail: apiKeySet
      ? 'RAG 问答、文档分析、任务规划与 Agent 均通过该模型执行。'
      : '尚未配置 API Key。',
  }
}

/* ------------------------------------------------------------- component */

export function AIProviderSettings() {
  const queryClient = useQueryClient()
  const pushToast = useToastStore((state) => state.push)

  const configQuery = useQuery({
    queryKey: ['ai-config'],
    queryFn: ({ signal }) => api.aiConfig.get(signal),
  })

  const healthQuery = useQuery({
    queryKey: ['ai-config-health'],
    queryFn: ({ signal }) => api.aiConfig.health(signal),
  })

  const [draft, setDraft] = useState<AIConfigPatch>({})
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [testResult, setTestResult] = useState<
    Awaited<ReturnType<typeof api.aiConfig.test>> | null
  >(null)

  const effective = configQuery.data?.effective
  // Memoised so the identity is stable across renders: without it, the
  // `?? []` fallback would create a new array every render and invalidate
  // `selectedProvider` below on every keystroke.
  const catalog = useMemo<AIProviderOption[]>(
    () => configQuery.data?.catalog ?? [],
    [configQuery.data],
  )

  const selectedProviderId = (draft.provider ?? effective?.provider ?? 'deepseek') as AIProviderId
  const selectedProvider = useMemo(
    () => catalog.find((p) => p.id === selectedProviderId),
    [catalog, selectedProviderId],
  )

  // Reset the draft whenever the server state is refreshed.
  useEffect(() => {
    setDraft({})
    setApiKeyInput('')
  }, [configQuery.dataUpdatedAt])

  const dirty = Object.keys(draft).length > 0 || apiKeyInput.trim().length > 0

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload: AIConfigPatch = { ...draft }
      if (apiKeyInput.trim()) payload.api_key = apiKeyInput.trim()
      return api.aiConfig.update(payload)
    },
    onSuccess: (data) => {
      setDraft({})
      setApiKeyInput('')
      setTestResult(null)
      void queryClient.invalidateQueries({ queryKey: ['ai-config'] })
      void queryClient.invalidateQueries({ queryKey: ['ai-config-health'] })
      void queryClient.invalidateQueries({ queryKey: ['health-deps'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      pushToast({
        kind: 'success',
        title: 'AI 配置已应用',
        description: `下一次 AI 调用将使用 ${data.effective.provider} · ${data.effective.model}`,
      })
    },
    onError: (error) => toastApiError(error, '应用 AI 配置失败'),
  })

  const resetMutation = useMutation({
    mutationFn: () => api.aiConfig.reset(),
    onSuccess: () => {
      setDraft({})
      setApiKeyInput('')
      setTestResult(null)
      void queryClient.invalidateQueries({ queryKey: ['ai-config'] })
      void queryClient.invalidateQueries({ queryKey: ['ai-config-health'] })
      pushToast({ kind: 'info', title: '已恢复为 .env 中的配置' })
    },
    onError: (error) => toastApiError(error, '恢复默认配置失败'),
  })

  const testMutation = useMutation({
    mutationFn: () => api.aiConfig.test(),
    onSuccess: (data) => {
      setTestResult(data)
      if (data.ok) {
        pushToast({
          kind: 'success',
          title: `连接成功 · ${data.model}`,
          description: `${data.latency_ms} ms · ${data.usage?.total_tokens ?? 0} tokens`,
        })
      } else {
        pushToast({
          kind: 'error',
          title: '连接失败',
          description: `${data.error_code}: ${data.error_message}`,
        })
      }
    },
    onError: (error) => {
      setTestResult(null)
      toastApiError(error, '连接测试失败')
    },
  })

  const toggleMutation = useMutation({
    mutationFn: ({ provider, enabled }: { provider: string; enabled: boolean }) =>
      api.aiConfig.toggle(provider, enabled),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ai-config'] })
      void queryClient.invalidateQueries({ queryKey: ['ai-config-health'] })
    },
    onError: (error) => toastApiError(error, '更新 Provider 状态失败'),
  })

  if (configQuery.isPending) {
    return (
      <Card>
        <CardBody>
          <SkeletonText lines={6} />
        </CardBody>
      </Card>
    )
  }
  if (configQuery.isError) {
    return (
      <ErrorState
        title="无法读取 AI 配置"
        error={toApiError(configQuery.error)}
        onRetry={() => void configQuery.refetch()}
      />
    )
  }
  if (!effective) return null

  const llmHealth = (healthQuery.data?.llm ?? {}) as Record<string, unknown>
  const degraded = Boolean(llmHealth.degraded)
  const summary = effectiveSummary(effective.provider, effective.model, degraded, effective.api_key_set)

  const modelOptions = selectedProvider?.models ?? []
  const currentModel = draft.model ?? effective.model
  const modelInList = modelOptions.some((m) => m.id === currentModel)

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------ status ---- */}
      <Card>
        <CardHeader
          title="当前生效的 AI Provider"
          description="所有 AI 功能（RAG、文档分析、任务规划、Agent）都通过这里显示的模型执行。"
          actions={
            <div className="flex items-center gap-2">
              <Button
                size="xs"
                variant="secondary"
                icon={<RefreshCw className={cn('size-3', healthQuery.isFetching && 'animate-spin')} />}
                onClick={() => {
                  void healthQuery.refetch()
                  void configQuery.refetch()
                }}
              >
                刷新
              </Button>
              <Button
                size="xs"
                variant="secondary"
                icon={<Plug className="size-3" />}
                loading={testMutation.isPending}
                onClick={() => testMutation.mutate()}
              >
                测试连接
              </Button>
            </div>
          }
        />
        <CardBody className="space-y-3">
          <InlineAlert
            tone={summary.tone === 'ok' ? 'success' : summary.tone === 'warn' ? 'warn' : 'danger'}
            title={summary.title}
          >
            <p>{summary.detail}</p>
            {degraded && typeof llmHealth.reason === 'string' && llmHealth.reason ? (
              <p className="mt-1 font-mono text-2xs opacity-80">{llmHealth.reason}</p>
            ) : null}
          </InlineAlert>

          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { key: 'Provider', value: effective.provider },
              { key: 'Model', value: effective.model },
              { key: 'API Key', value: effective.api_key_set ? effective.api_key_hint : '未配置' },
              { key: 'Base URL', value: effective.base_url || '—' },
            ].map((item) => (
              <div key={item.key} className="min-w-0 border-b border-subtle pb-1.5">
                <dt className="text-2xs uppercase tracking-wide text-faint">{item.key}</dt>
                <dd className="mt-0.5 truncate font-mono text-xs text-primary" title={item.value}>
                  {item.value}
                </dd>
              </div>
            ))}
          </dl>

          {testResult ? (
            <InlineAlert
              tone={testResult.ok ? 'success' : 'danger'}
              title={testResult.ok ? `连接成功 · ${testResult.model}` : '连接失败'}
            >
              {testResult.ok ? (
                <span>
                  延迟 <span className="font-mono">{testResult.latency_ms} ms</span> · 消耗{' '}
                  <span className="font-mono">{testResult.usage?.total_tokens ?? 0}</span> tokens
                  （prompt {testResult.usage?.prompt_tokens ?? 0} / completion{' '}
                  {testResult.usage?.completion_tokens ?? 0}）
                </span>
              ) : (
                <span className="font-mono text-2xs">
                  {testResult.error_code}: {testResult.error_message}
                </span>
              )}
            </InlineAlert>
          ) : null}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <p className="text-2xs font-medium text-muted">配置来源</p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(effective.sources).map(([field, source]) => (
                  <Badge key={field} tone={source === 'runtime' ? 'accent' : 'neutral'} mono>
                    {field}={SOURCE_LABEL[source] ?? source}
                  </Badge>
                ))}
              </div>
            </div>
            <div className="space-y-1.5">
              <p className="text-2xs font-medium text-muted">Embedding</p>
              <p className="font-mono text-2xs text-faint">
                {effective.embedding_provider} · {effective.embedding_model}
                {effective.embedding_api_key_set ? ' · key set' : ' · 无 Key（走离线向量化）'}
              </p>
            </div>
          </div>
        </CardBody>
      </Card>

      {/* ---------------------------------------------------- providers ---- */}
      <Card>
        <CardHeader
          title="Provider 目录"
          description="选择一个 Provider，填写参数后点击「应用配置」即可生效。可随时切换或禁用。"
        />
        <CardBody className="space-y-2.5">
          {catalog.map((provider) => {
            const Icon = PROVIDER_ICON[provider.id] ?? Cpu
            const active = provider.id === effective.provider
            const selected = provider.id === selectedProviderId
            return (
              <div
                key={provider.id}
                className={cn(
                  'rounded-md border p-3 transition-colors',
                  selected ? 'border-accent bg-accent/5' : 'border-subtle bg-surface hover:bg-elevated',
                )}
              >
                <div className="flex items-start gap-3">
                  <button
                    type="button"
                    className="flex flex-1 items-start gap-3 text-left"
                    onClick={() => setDraft((d) => ({ ...d, provider: provider.id, model: undefined }))}
                  >
                    <span
                      className={cn(
                        'mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border',
                        selected ? 'border-accent/40 bg-accent/10 text-accent' : 'border-subtle bg-inset text-muted',
                      )}
                    >
                      <Icon className="size-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="text-xs font-medium text-primary">{provider.label}</span>
                        <Badge tone="neutral">{KIND_LABEL[provider.kind] ?? provider.kind}</Badge>
                        {active ? (
                          <Badge tone="accent" icon={<Dot tone="accent" />}>
                            当前生效
                          </Badge>
                        ) : null}
                        {provider.configured ? (
                          <Badge tone="success" icon={<CheckCircle2 className="size-2.5" />}>
                            已配置
                          </Badge>
                        ) : (
                          <Badge tone="warn" icon={<AlertTriangle className="size-2.5" />}>
                            未配置
                          </Badge>
                        )}
                        {!provider.enabled ? <Badge tone="neutral">已禁用</Badge> : null}
                      </span>
                      <span className="mt-1 block text-2xs leading-relaxed text-muted">
                        {provider.description}
                      </span>
                    </span>
                  </button>

                  <div className="flex shrink-0 items-center gap-2">
                    {provider.docs_url ? (
                      <a
                        href={provider.docs_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-faint transition-colors hover:text-accent"
                        title="查看官方文档"
                      >
                        <ExternalLink className="size-3.5" />
                      </a>
                    ) : null}
                    <Switch
                      checked={provider.enabled}
                      disabled={provider.id === 'offline' || toggleMutation.isPending}
                      onChange={(next) =>
                        toggleMutation.mutate({ provider: provider.id, enabled: next })
                      }
                      label=""
                    />
                  </div>
                </div>
              </div>
            )
          })}
        </CardBody>
      </Card>

      {/* ------------------------------------------------------- form ------ */}
      <Card>
        <CardHeader
          title="参数"
          description="留空表示沿用 .env 或默认值。API Key 仅写入本机 .runtime/ai-config.json，不会提交到 Git。"
        />
        <CardBody className="space-y-4">
          <div className="grid gap-3.5 sm:grid-cols-2">
            <Field label="Provider" htmlFor="ai-provider">
              <Select
                id="ai-provider"
                value={selectedProviderId}
                onChange={(event) =>
                  setDraft((d) => ({
                    ...d,
                    provider: event.target.value as AIProviderId,
                    model: undefined,
                  }))
                }
              >
                {catalog.map((provider) => (
                  <option key={provider.id} value={provider.id} disabled={!provider.enabled}>
                    {provider.label}
                    {provider.enabled ? '' : '（已禁用）'}
                  </option>
                ))}
              </Select>
            </Field>

            <Field
              label="Model"
              htmlFor="ai-model"
              hint={
                selectedProvider?.id === 'deepseek'
                  ? 'deepseek-flash = DeepSeek V4.1 Flash（默认）；deepseek-v4-pro 推理更强、成本更高。'
                  : '可直接输入任意模型名。'
              }
            >
              <Select
                id="ai-model"
                value={modelInList ? currentModel : '__custom__'}
                onChange={(event) => {
                  const value = event.target.value
                  setDraft((d) => ({ ...d, model: value === '__custom__' ? '' : value }))
                }}
              >
                {modelOptions.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.label}
                    {model.description ? ` — ${model.description}` : ''}
                  </option>
                ))}
                <option value="__custom__">自定义模型名…</option>
              </Select>
            </Field>
          </div>

          {!modelInList ? (
            <Field label="自定义模型名" htmlFor="ai-model-custom">
              <Input
                id="ai-model-custom"
                value={currentModel}
                placeholder={selectedProvider?.default_model ?? 'model-name'}
                onChange={(event) => setDraft((d) => ({ ...d, model: event.target.value }))}
              />
            </Field>
          ) : null}

          <div className="grid gap-3.5 sm:grid-cols-2">
            <Field
              label="Base URL"
              htmlFor="ai-base-url"
              hint={selectedProvider?.default_base_url ? `默认：${selectedProvider.default_base_url}` : undefined}
            >
              <Input
                id="ai-base-url"
                value={draft.base_url ?? effective.base_url}
                placeholder={selectedProvider?.default_base_url ?? 'https://…'}
                onChange={(event) => setDraft((d) => ({ ...d, base_url: event.target.value }))}
              />
            </Field>

            <Field
              label="API Key"
              htmlFor="ai-api-key"
              hint={
                effective.api_key_set
                  ? `已配置（${effective.api_key_hint}）。留空表示不修改。`
                  : '尚未配置。写入后立即生效。'
              }
            >
              <div className="relative">
                <KeyRound className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-faint" />
                <Input
                  id="ai-api-key"
                  type="password"
                  autoComplete="off"
                  className="pl-8"
                  value={apiKeyInput}
                  placeholder={effective.api_key_set ? '••••••••（留空不修改）' : 'sk-…'}
                  onChange={(event) => setApiKeyInput(event.target.value)}
                />
              </div>
            </Field>
          </div>

          <div className="grid gap-3.5 sm:grid-cols-3">
            <Field label="Temperature" htmlFor="ai-temperature" hint={`当前 ${effective.temperature}`}>
              <Input
                id="ai-temperature"
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={draft.temperature ?? effective.temperature}
                onChange={(event) => setDraft((d) => ({ ...d, temperature: Number(event.target.value) }))}
              />
            </Field>
            <Field label="Max Tokens" htmlFor="ai-max-tokens">
              <Input
                id="ai-max-tokens"
                type="number"
                min={1}
                max={32000}
                step={128}
                value={draft.max_tokens ?? effective.max_tokens}
                onChange={(event) => setDraft((d) => ({ ...d, max_tokens: Number(event.target.value) }))}
              />
            </Field>
            <Field label="超时（秒）" htmlFor="ai-timeout">
              <Input
                id="ai-timeout"
                type="number"
                min={5}
                max={600}
                step={5}
                value={draft.timeout_seconds ?? effective.timeout_seconds}
                onChange={(event) =>
                  setDraft((d) => ({ ...d, timeout_seconds: Number(event.target.value) }))
                }
              />
            </Field>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-subtle pt-3.5">
            <p className="text-2xs text-faint">
              {dirty ? '有未应用的修改' : '当前与服务器配置一致'} · 配置写入{' '}
              <span className="font-mono">{configQuery.data?.runtime_config_path}</span>
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="ghost"
                icon={<RotateCcw className="size-3.5" />}
                loading={resetMutation.isPending}
                onClick={() => resetMutation.mutate()}
              >
                恢复 .env 配置
              </Button>
              <Button
                size="sm"
                variant="primary"
                icon={<Save className="size-3.5" />}
                disabled={!dirty}
                loading={saveMutation.isPending}
                onClick={() => saveMutation.mutate()}
              >
                应用配置
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>
    </div>
  )
}

export default AIProviderSettings
