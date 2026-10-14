/**
 * Client for the delivery layer.
 *
 * The screens these back exist to answer one question the user is entitled to ask:
 * *did it actually make the thing, and is the thing any good?* So the shapes here
 * carry evidence rather than summaries — a verification is a list of checks, not a
 * boolean, and a rejection carries the reason the user gave.
 */

import { request, requestBlob, requestResult } from './api'

/* ------------------------------------------------------------------ types -- */

export type BuildStatus =
  | 'DRAFT'
  | 'PLANNING'
  | 'READY'
  | 'EXECUTING'
  | 'VERIFYING'
  | 'WAITING_USER'
  | 'COMPLETED'
  | 'PARTIAL'
  | 'FAILED'
  | 'CANCELLED'
  | 'LIMIT_REACHED'

export type DeliverableKind =
  | 'pptx'
  | 'docx'
  | 'xlsx'
  | 'pdf'
  | 'md'
  | 'csv'
  | 'report'
  | 'code'
  | 'mixed'

export interface VerificationCheck {
  name: string
  ok: boolean
  detail: string
}

/**
 * The producing run's state, reported *beside* the file's own verdict.
 *
 * Two different questions: `VerificationResult.checks` asks "is this file any
 * good?"; this asks "did the run that made it get all the way through?". A file
 * can be perfectly good while the run that made it did not finish, so this must
 * never be folded into `passed` — doing that made a readable 9-slide deck display
 * as 「未校验」 because an unrelated step had failed.
 *
 * Absent when the file has no producing run (registered by hand): that means the
 * question does not apply, which is not the same as "the run failed".
 */
export interface RunStateNote {
  state: string
  ok: boolean
  detail: string
}

export interface VerificationResult {
  checks: VerificationCheck[]
  passed: boolean
  check_count: number
  failed_count: number
  run_state?: RunStateNote
}

export interface BuildRun {
  id: string
  goal: string
  title: string
  status: BuildStatus
  deliverable_kind: DeliverableKind
  scope: { knowledge_base_ids?: string[]; document_ids?: string[] }
  project_id: string | null
  steps_total: number
  steps_done: number
  progress: number
  result_summary: string
  error: string
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  deliverable_count: number
  plan?: Record<string, unknown>
  context_snapshot_id?: string | null
  ai_run_id?: string | null
  deliverables?: Deliverable[]
}

export interface Deliverable {
  id: string
  build_run_id: string | null
  path: string
  kind: DeliverableKind
  title: string
  size_bytes: number
  version: number
  parent_id: string | null
  verified: boolean
  accepted: boolean | null
  feedback: string
  style: string
  decided_at: string | null
  created_at: string | null
  verification?: VerificationResult
  applied_preferences?: Record<string, unknown>
}

export interface DeliverySummary {
  build_runs_total: number
  build_runs_active: number
  deliverables_total: number
  deliverables_accepted: number
  deliverables_rejected: number
  deliverables_pending: number
  deliverables_verified: number
}

export interface BuildRunContext {
  available: boolean
  rendered: string
  reason?: string
  profile_digest?: string
  memory_ids?: string[]
  token_estimate?: number
  truncated?: boolean
}

/* ------------------------------------------------------------------ calls -- */

export const deliveryApi = {
  createBuildRun: (payload: {
    goal: string
    deliverable_kind?: DeliverableKind
    title?: string
    project_id?: string
    knowledge_base_ids?: string[]
    document_ids?: string[]
  }) => requestResult<BuildRun>('/build-runs', { method: 'POST', body: payload }),

  /**
   * Start it.
   *
   * Creating and starting are one action from the user's point of view — "hand it
   * a job" — even though the API splits them. Callers must therefore call create
   * and execute together; `startBuildRun` below is the composed form and is what
   * screens should use. Creating without executing leaves a run in DRAFT forever,
   * which is a real bug this codebase shipped once already.
   */
  executeBuildRun: (id: string) =>
    requestResult<{
      build_run_id: string
      run_id: string
      /** The controller's state at the moment it was handed the goal. */
      state: string
    }>(`/build-runs/${id}/execute`, { method: 'POST' }),

  buildRuns: (params: { status?: string; active_only?: boolean; limit?: number } = {}) =>
    request<{ build_runs: BuildRun[]; count: number }>('/build-runs', { query: params }),

  buildRun: (id: string) => request<BuildRun>(`/build-runs/${id}`),

  buildRunContext: (id: string) => request<BuildRunContext>(`/build-runs/${id}/context`),

  registerDeliverable: (
    buildRunId: string,
    payload: { path: string; kind: string; title?: string; style?: string; parent_id?: string },
  ) =>
    requestResult<{ deliverable: Deliverable; verification: VerificationResult }>(
      `/build-runs/${buildRunId}/deliverables`,
      { method: 'POST', body: payload },
    ),

  deliverables: (params: { build_run_id?: string; pending_only?: boolean; limit?: number } = {}) =>
    request<{ deliverables: Deliverable[]; count: number }>('/deliverables', { query: params }),

  deliverable: (id: string) => request<Deliverable>(`/deliverables/${id}`),

  versions: (id: string) =>
    request<{ versions: Deliverable[] }>(`/deliverables/${id}/versions`),

  verify: (id: string) =>
    requestResult<VerificationResult>(`/deliverables/${id}/verify`, { method: 'POST' }),

  accept: (id: string) =>
    requestResult<Deliverable>(`/deliverables/${id}/accept`, { method: 'POST' }),

  reject: (id: string, feedback: string) =>
    requestResult<Deliverable>(`/deliverables/${id}/reject`, {
      method: 'POST',
      body: { feedback },
    }),

  /**
   * Fetch the file's bytes, authenticated.
   *
   * There is deliberately no `downloadUrl(id)` returning a string. It used to
   * exist and was used as `<a href={downloadUrl(id)} download>`, which cannot
   * work: a navigation sends no `Authorization` header, so the endpoint answered
   * 401 and the button did nothing. Fetching with the token attached is the same
   * audited path every other call takes, and a refusal becomes an error the UI
   * can show instead of a silent no-op.
   */
  downloadDeliverable: (id: string) =>
    requestBlob(`/deliverables/${id}/download`, { signal: undefined }),

  /**
   * Open the file with the system's default program.
   *
   * An **id**, not a path — the backend resolves the path from its own record, so
   * the client cannot ask it to open an arbitrary file. The previous
   * implementation passed `file:///…` to `window.open`, which a WebView refuses.
   */
  openDeliverable: (id: string) =>
    requestResult<{ path: string; name: string; opened: boolean; method: string }>(
      `/deliverables/${id}/open`,
      { method: 'POST' },
    ),

  summary: () => request<DeliverySummary>('/delivery/summary'),
}

/* ---------------------------------------------------------------- display -- */

export const STATUS_LABELS: Record<BuildStatus, string> = {
  DRAFT: '待开始',
  PLANNING: '规划中',
  READY: '已就绪',
  EXECUTING: '执行中',
  VERIFYING: '校验中',
  WAITING_USER: '等你确认',
  COMPLETED: '已完成',
  PARTIAL: '部分完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  LIMIT_REACHED: '已达上限',
}

export type RunTone = 'doing' | 'done' | 'trouble' | 'waiting'

/**
 * The run's state as one sentence a person would say.
 *
 * The conversation shows this instead of the status code. `PARTIAL` and
 * `WAITING_USER` are precise words for the record and meaningless words on a
 * screen — the user's complaint about the old interface was exactly that: a row of
 * coloured pills that had to be decoded before anything could be understood.
 *
 * `STATUS_LABELS` is kept because the record pages (the run list, the workbench
 * header) are for looking into the system, and there the precise word is right.
 */
export function runSentence(run: BuildRun): { text: string; tone: RunTone } {
  switch (run.status) {
    case 'DRAFT':
    case 'READY':
      return { text: '还没开始 —— 打开它点一下「开始」', tone: 'waiting' }
    case 'PLANNING':
      return { text: '正在想怎么做…', tone: 'doing' }
    case 'EXECUTING':
      return {
        text: run.steps_total > 0 ? `正在做 —— ${run.steps_done}/${run.steps_total} 步` : '正在做…',
        tone: 'doing',
      }
    case 'VERIFYING':
      return { text: '正在检查它做出来的东西', tone: 'doing' }
    case 'WAITING_USER':
      return { text: '做完了，但它没能自己确认结果 —— 需要你看一眼', tone: 'waiting' }
    case 'COMPLETED':
      return {
        text: run.deliverable_count > 0 ? `做好了 —— ${run.deliverable_count} 份文件` : '做好了',
        tone: 'done',
      }
    case 'PARTIAL':
      return { text: '东西做出来了，但这次运行没有走完', tone: 'trouble' }
    case 'FAILED':
      return { text: run.error ? `没做成：${run.error}` : '没做成 —— 原因已记录在上面', tone: 'trouble' }
    case 'CANCELLED':
      return { text: '已取消', tone: 'waiting' }
    case 'LIMIT_REACHED':
      return { text: '做到一半就达到资源上限了', tone: 'trouble' }
    default:
      return { text: run.status, tone: 'waiting' }
  }
}

/** Tone for the status pill. Terminal states are visually distinct on purpose. */
export const STATUS_TONES: Record<BuildStatus, string> = {
  DRAFT: 'neutral',
  PLANNING: 'info',
  READY: 'info',
  EXECUTING: 'accent',
  VERIFYING: 'accent',
  WAITING_USER: 'warn',
  COMPLETED: 'success',
  PARTIAL: 'warn',
  FAILED: 'danger',
  CANCELLED: 'neutral',
  LIMIT_REACHED: 'warn',
}

export const KIND_LABELS: Record<string, string> = {
  pptx: 'PPT',
  docx: 'Word',
  xlsx: 'Excel',
  pdf: 'PDF',
  md: 'Markdown',
  csv: 'CSV',
  report: '报告',
  code: '代码',
  mixed: '混合',
}

/** `3.2 MB` — the size a person reads, not the byte count. */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** `2 小时前` — relative, because that is the question people actually ask. */
export function formatWhen(iso: string | null): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  const seconds = Math.max(0, (Date.now() - then) / 1000)
  if (seconds < 60) return '刚刚'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`
  return new Date(iso).toLocaleDateString('zh-CN')
}

/** The one-line state of a deliverable, as a person would say it. */
export function acceptanceLabel(item: Deliverable): {
  text: string
  tone: 'success' | 'danger' | 'warn'
} {
  if (item.accepted === true) return { text: '已验收', tone: 'success' }
  if (item.accepted === false) return { text: '已退回', tone: 'danger' }
  return { text: '待验收', tone: 'warn' }
}
