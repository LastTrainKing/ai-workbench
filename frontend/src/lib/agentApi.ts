/**
 * Client for the autonomous agent surface.
 *
 * Kept separate from `api.ts` because it is a different kind of thing: the
 * endpoints here drive a *live process* rather than reading and writing records.
 * A run is started, polled, paused and cancelled, and the UI needs to reason
 * about its state machine rather than just fetching data.
 *
 * Every call goes to the real backend. There is no mock layer and no fixture
 * data anywhere in this module — a UI that fakes agent execution would be worse
 * than no UI, because it would make a broken agent look like a working one.
 */

import { request, requestResult } from './api'

/* ------------------------------------------------------------------ types -- */

export type RunState =
  | 'PLANNING'
  | 'READY'
  | 'EXECUTING'
  | 'OBSERVING'
  | 'VERIFYING'
  | 'RECOVERING'
  | 'WAITING_CONFIRMATION'
  | 'PAUSED'
  | 'CANCELLED'
  | 'FAILED'
  | 'LIMIT_REACHED'
  | 'COMPLETED'

export type StepStatus = 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'SKIPPED'

export interface PlanStep {
  id: string
  description: string
  verification: string
  status: StepStatus
  tool_hint: string
  started_at: string
  completed_at: string
  tool_calls: number
  retry_count: number
  result: string
  error: string
}

export interface ExecutionPlan {
  goal: string
  steps: PlanStep[]
  success_criteria: string[]
  risk_level: string
  estimated_tools: string[]
  estimated_duration_seconds: number
  created_at: string
  revision: number
  progress: number
  done_steps: number
  failed_steps: number
  total_steps: number
}

export interface RunEvent {
  seq: number
  kind: 'state' | 'plan' | 'step' | 'tool' | 'verification' | 'confirmation' | 'error' | 'result'
  state: string
  message: string
  data: Record<string, unknown>
  at: string
}

export interface RunStatus {
  run_id: string
  state: RunState
  paused: boolean
  elapsed_seconds: number
  tool_calls: number
  files_modified: number
  retries: number
  confirmations_requested: number
  current_tool: string
  current_step_id: string
  plan: ExecutionPlan | null
  event_count: number
}

export interface DiffFile {
  path: string
  change: 'added' | 'removed' | 'modified'
  lines_added: number
  lines_removed: number
  before_size: number
  after_size: number
}

export interface DiffResult {
  available: boolean
  message?: string
  snapshot_id?: string
  root?: string
  files: DiffFile[]
  summary: Record<string, number>
  changed?: number
  unified?: string
}

export interface RollbackResult {
  restored: boolean
  conflict?: boolean
  conflicts?: { path: string; reason: string }[]
  files_restored?: number
  files_removed?: number
  forced?: boolean
  message: string
}

export interface Confirmation {
  id: string
  run_id: string
  tool: string
  operation: string
  target: string
  reason: string
  risk: string
  impact: string
  detail: string
  arguments: Record<string, unknown>
  age_seconds: number
  answered: boolean | null
}

export interface SnapshotSummary {
  id: string
  root: string
  label: string
  run_id: string
  created_at: string
  file_count: number
  total_bytes: number
  truncated: boolean
  has_post_state: boolean
}

export interface AgentPermissions {
  mode: string
  level: number
  level_label: string
  workspace: string
  allowed_paths: string[]
  denied_paths: string[]
  allowed_domains: string[]
  blocked_domains: string[]
  limits: Record<string, number>
  overrides_active: boolean
  capabilities: Record<string, boolean>
  source: string
  modes?: { value: string; label: string; description: string }[]
  levels?: { value: number; label: string }[]
}

export interface Capability {
  key: string
  label: string
  description: string
  required_level: number
  required_level_label: string
  enabled: boolean
  user_enabled: boolean
  tools: string[]
}

export interface ToolRow {
  name: string
  description: string
  category: string
  source: 'business' | 'computer'
  permission_level: number
  permission_label: string
  risk_level: string
  mutates_state: boolean
  requires_confirmation: boolean
  timeout_seconds?: number
  available: boolean
  parameters?: Record<string, unknown>
}

/* ------------------------------------------------------------------ calls -- */

export const agentApi = {
  /* ------------------------------------------------------------- runs --- */
  start: (goal: string, context = '') =>
    requestResult<{ run_id: string; state: RunState; goal: string }>('/agent/runs', {
      method: 'POST',
      body: { goal, context },
    }),

  active: () => request<{ runs: RunStatus[]; count: number }>('/agent/runs'),

  status: (runId: string) => request<RunStatus>(`/agent/runs/${runId}`),

  /**
   * Incremental events. Passing the last sequence number seen means the UI never
   * re-renders history it already has, which matters for a run emitting hundreds
   * of events.
   */
  events: (runId: string, since = 0) =>
    request<{
      events: RunEvent[]
      count: number
      last_seq: number
      state: RunState
      finished: boolean
    }>(`/agent/runs/${runId}/events`, { query: { since } }),

  plan: (runId: string) =>
    request<{ available: boolean; message?: string } & Partial<ExecutionPlan>>(
      `/agent/runs/${runId}/plan`,
    ),

  pause: (runId: string) =>
    requestResult<{ state: RunState }>(`/agent/runs/${runId}/pause`, { method: 'POST' }),
  resume: (runId: string) =>
    requestResult<{ state: RunState }>(`/agent/runs/${runId}/resume`, { method: 'POST' }),
  cancel: (runId: string) =>
    requestResult<{ state: RunState }>(`/agent/runs/${runId}/cancel`, { method: 'POST' }),

  diff: (runId: string, unified = false) =>
    request<DiffResult>(`/agent/runs/${runId}/diff`, { query: { unified } }),

  rollback: (runId: string, force = false) =>
    requestResult<RollbackResult>(`/agent/runs/${runId}/rollback`, {
      method: 'POST',
      body: { force },
    }),

  /* ----------------------------------------------------- confirmations --- */
  confirmations: (runId?: string) =>
    request<{ confirmations: Confirmation[]; count: number }>('/agent/confirmations', {
      query: runId ? { run_id: runId } : {},
    }),

  decide: (id: string, approved: boolean) =>
    requestResult<{ id: string; approved: boolean }>(`/agent/confirmations/${id}`, {
      method: 'POST',
      body: { approved },
    }),

  /* -------------------------------------------------------- permissions --- */
  permissions: () => request<AgentPermissions>('/agent/permissions'),

  updatePermissions: (patch: Record<string, unknown>) =>
    requestResult<AgentPermissions>('/agent/permissions', { method: 'PATCH', body: patch }),

  resetPermissions: () =>
    requestResult<AgentPermissions>('/agent/permissions', { method: 'DELETE' }),

  capabilities: () =>
    request<{
      capabilities: Capability[]
      mode: string
      level: number
      level_label: string
      tool_count: number
    }>('/agent/capabilities'),

  updateCapabilities: (capabilities: Record<string, boolean>) =>
    requestResult<{ capabilities: Record<string, boolean>; tool_count: number }>(
      '/agent/capabilities',
      { method: 'PATCH', body: { capabilities } },
    ),

  tools: (params: { category?: string; source?: string } = {}) =>
    request<{
      tools: ToolRow[]
      count: number
      available: number
      excluded: {
        name: string
        category: string
        permission_label: string
        reason: string
      }[]
      policy: { mode: string; level: number; level_label: string }
    }>('/agent/tools', { query: params }),

  classifyCommand: (command: string) =>
    request<{
      command: string
      risk: string
      reason: string
      requires_confirmation: boolean
    }>('/agent/tools/classify-command', { method: 'POST', query: { command } }),

  /* ---------------------------------------------------------- snapshots --- */
  snapshots: () => request<{ snapshots: SnapshotSummary[]; count: number }>('/agent/snapshots'),

  createSnapshot: (label = '', path = '') =>
    requestResult<SnapshotSummary>('/agent/snapshots', { method: 'POST', body: { label, path } }),

  snapshotDiff: (id: string, unified = false) =>
    request<DiffResult>(`/agent/snapshots/${id}/diff`, { query: { unified } }),

  restoreSnapshot: (id: string, force = false) =>
    requestResult<RollbackResult>(`/agent/snapshots/${id}/restore`, {
      method: 'POST',
      body: { force },
    }),

  deleteSnapshot: (id: string) =>
    requestResult<{ deleted: string }>(`/agent/snapshots/${id}`, { method: 'DELETE' }),
}

/* ---------------------------------------------------------------- helpers -- */

/** States where the run is over. Kept here so the UI and the backend agree. */
export const TERMINAL_STATES: RunState[] = ['COMPLETED', 'FAILED', 'CANCELLED', 'LIMIT_REACHED']

export function isTerminal(state: RunState): boolean {
  return TERMINAL_STATES.includes(state)
}

/** Human-readable label and tone for a run state, for badges. */
export function describeState(state: RunState): { label: string; tone: string } {
  const table: Record<RunState, { label: string; tone: string }> = {
    PLANNING: { label: '制定计划', tone: 'info' },
    READY: { label: '准备执行', tone: 'info' },
    EXECUTING: { label: '执行中', tone: 'accent' },
    OBSERVING: { label: '分析结果', tone: 'accent' },
    VERIFYING: { label: '验证中', tone: 'info' },
    RECOVERING: { label: '修复中', tone: 'warn' },
    WAITING_CONFIRMATION: { label: '等待确认', tone: 'warn' },
    PAUSED: { label: '已暂停', tone: 'warn' },
    CANCELLED: { label: '已取消', tone: 'muted' },
    FAILED: { label: '失败', tone: 'danger' },
    LIMIT_REACHED: { label: '达到上限', tone: 'warn' },
    COMPLETED: { label: '已完成', tone: 'success' },
  }
  return table[state] ?? { label: state, tone: 'muted' }
}
