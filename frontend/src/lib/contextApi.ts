/**
 * Client for the personal context surface.
 *
 * The screens these back exist to answer one question the user is entitled to
 * ask: *what does this thing believe about me?* So the shape of the client
 * matters — everything the system stores is readable, everything is editable,
 * and the context preview returns the exact text that would be sent rather than
 * a summary of it.
 */

import { request, requestResult } from './api'

/* ------------------------------------------------------------------ types -- */

export type MemoryKind =
  | 'identity'
  | 'goal'
  | 'preference'
  | 'skill'
  | 'constraint'
  | 'fact'
  | 'decision'

export type MemoryStatus = 'active' | 'archived' | 'rejected'

export interface Profile {
  display_name: string
  identity: string
  stage: string
  domain: string
  current_goals: string[]
  language: string
  timezone: string
  bio: string
  onboarded: boolean
  /** The exact text carried into every model call. */
  digest: string
}

/**
 * Where a memory came from.
 *
 * A memory with no origin is an assertion from nowhere: "you prefer more charts"
 * is only reviewable if the user can see which delivery taught it. Populated for
 * memories learned from a delivery decision; absent for hand-written ones.
 */
export interface MemoryOrigin {
  kind: string
  decision?: 'accepted' | 'rejected'
  deliverable_id?: string
  build_run_id?: string
  deliverable_title?: string
  deliverable_kind?: string
  version?: number
  feedback?: string
  decided_at?: string
}

export interface MemoryStructured {
  origin?: MemoryOrigin
}

export interface Memory {
  id: string
  kind: MemoryKind
  scope: string
  content: string
  structured?: MemoryStructured
  confidence: number
  source: string
  status: MemoryStatus
  confirmed_by_user: boolean
  use_count: number
  last_used_at: string | null
  created_at: string | null
}

export interface MemoryCandidate {
  id: string
  proposed_kind: MemoryKind
  proposed_scope: string
  content: string
  reason: string
  confidence: number
  structured?: MemoryStructured
  status: string
  conflicts_with: string
  created_at: string | null
}

export interface MemoryStats {
  total_entries: number
  active: number
  archived: number
  candidates_pending: number
  candidates_approved: number
  candidates_rejected: number
  rejection_rate: number
  avg_use_count: number
}

export interface ContextBlock {
  kind: 'profile' | 'project' | 'memory' | 'knowledge'
  text: string
  tokens: number
  truncated: boolean
  memory_ids: string[]
  knowledge_chunk_ids: string[]
}

export interface ContextPreview {
  blocks: ContextBlock[]
  token_estimate: number
  truncated: boolean
  profile_digest: string
  memory_ids: string[]
  knowledge_chunk_ids: string[]
  project_digest: string
  rendered: string
  empty: boolean
  budget_tokens?: number
}

export interface ContextStats {
  memories_total: number
  memories_active: number
  candidates_pending: number
  preferences: number
  context_snapshots: number
  profile_ready: boolean
  onboarded: boolean
}

export interface ContextSnapshot {
  id: string
  run_id: string
  purpose: string
  profile_digest: string
  memory_ids: string[]
  knowledge_chunk_ids: string[]
  project_digest: string
  token_estimate: number
  truncated: boolean
  created_at: string | null
  rendered?: string
}

/* ------------------------------------------------------------------ calls -- */


export interface TierAttempt {
  tier: 'memory' | 'skill' | 'knowledge' | 'general'
  hit: boolean
  elapsed_ms: number
  detail: string
}

export interface TierResult {
  tier: TierAttempt['tier']
  total_ms: number
  explanation: string
  attempts: TierAttempt[]
}

export const contextApi = {
  /* ------------------------------------------------------------- profile -- */
  profile: () => request<Profile>('/profile'),

  updateProfile: (patch: Partial<Omit<Profile, 'digest'>>) =>
    requestResult<{ updated: boolean; digest: string }>('/profile', {
      method: 'PATCH',
      body: patch,
    }),

  /* ------------------------------------------------------------ memories -- */
  memories: (params: { kind?: string; scope?: string; status?: string } = {}) =>
    request<{ memories: Memory[]; count: number }>('/memories', {
      query: { status: 'active', ...params },
    }),

  createMemory: (payload: { content: string; kind: MemoryKind; scope?: string }) =>
    requestResult<Memory>('/memories', { method: 'POST', body: payload }),

  updateMemory: (id: string, patch: Partial<Pick<Memory, 'content' | 'kind' | 'status'>>) =>
    requestResult<Memory>(`/memories/${id}`, { method: 'PATCH', body: patch }),

  deleteMemory: (id: string) =>
    requestResult<{ deleted: string }>(`/memories/${id}`, { method: 'DELETE' }),

  stats: () => request<MemoryStats>('/memories/stats'),

  /* ---------------------------------------------------------- candidates -- */
  candidates: (status = 'pending') =>
    request<{ candidates: MemoryCandidate[]; count: number }>('/memory-candidates', {
      query: { status },
    }),

  approveCandidate: (id: string) =>
    requestResult<Memory>(`/memory-candidates/${id}/approve`, { method: 'POST' }),

  rejectCandidate: (id: string) =>
    requestResult<{ rejected: string }>(`/memory-candidates/${id}/reject`, { method: 'POST' }),

  /* --------------------------------------------------------- preferences -- */
  preferences: (scope?: string) =>
    request<{
      preferences: { key: string; value: unknown; scope: string; source: string }[]
      count: number
    }>('/preferences', { query: scope ? { scope } : {} }),

  updatePreferences: (preferences: Record<string, unknown>, scope = 'global') =>
    requestResult<{ updated: string[] }>('/preferences', {
      method: 'PATCH',
      body: { preferences, scope },
    }),

  /* ------------------------------------------------------------- context -- */
  preview: (budget = 1800) => request<ContextPreview>('/context/preview', { query: { budget } }),

  snapshots: (limit = 20) =>
    request<{ snapshots: ContextSnapshot[]; count: number }>('/context/snapshots', {
      query: { limit },
    }),

  contextStats: () => request<ContextStats>('/context/stats'),

  /** Which tier answered this question, and how long each took. */
  tiers: (question: string) =>
    request<TierResult>('/retrieval/tiers', { method: 'POST', body: { question } }),
}

/* ---------------------------------------------------------------- labels -- */

export const KIND_LABELS: Record<MemoryKind, string> = {
  identity: '身份',
  goal: '目标',
  preference: '偏好',
  skill: '技能',
  constraint: '约束',
  fact: '事实',
  decision: '决定',
}

export const KIND_TONES: Record<MemoryKind, string> = {
  identity: 'accent',
  goal: 'info',
  preference: 'success',
  skill: 'neutral',
  constraint: 'warn',
  fact: 'neutral',
  decision: 'accent',
}

/** Human label for a scope string like `project:<uuid>`. */
export function scopeLabel(scope: string): string {
  if (scope === 'global') return '全局'
  if (scope.startsWith('project:')) return '项目'
  if (scope.startsWith('knowledge:')) return '知识库'
  return scope
}
