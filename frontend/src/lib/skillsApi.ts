/**
 * The skill layer's API client.
 *
 * One place that knows the routes, so the panel does not build URLs. The two fields worth
 * noticing:
 *
 * - `adopt` sends `confirmed: true` explicitly. There is no default on the server, so a
 *   call that forgets it is refused rather than silently installing — and writing it here
 *   makes the yes visible in the code that gives it.
 * - `discover` is a POST that writes nothing. It is a POST because it carries a body, not
 *   because it changes anything.
 */

import { request } from './api'

export interface SkillParamValue {
  name: string
  description?: string
  source?: string
  value?: string
}

export interface SkillVerification {
  summary?: string
  unfilled_params?: string[]
  findings?: { rule: string; severity: string; detail: string; evidence?: string }[]
}

export interface InstalledSkill {
  id: string
  name: string
  description: string
  when_to_use: string
  skill_type: 'document' | 'script'
  version: string
  source: string
  source_commit: string
  license: string
  status: 'active' | 'disabled' | 'retired'
  enabled: boolean
  verified_at: string
  verification: SkillVerification
  params: SkillParamValue[]
  has_scripts: boolean
  use_count: number
  success_count: number
  last_used_at: string | null
  rating: number | null
  review_note: string
  rendered_body?: string
}

export interface SkillCandidate {
  name: string
  repo: string
  url: string
  stars: number
  license: string
  updated_at: string
  why: string
}

export interface RejectedCandidate {
  repo: string
  url: string
  reason: string
}

export interface DiscoveryResponse {
  queries: string[]
  summary: string
  error: string
  candidates: SkillCandidate[]
  rejected: RejectedCandidate[]
}

export interface SkillStats {
  installed: number
  active: number
  retired: number
  ever_used: number
  never_used: number
  total_uses: number
  rated: number
  average_rating: number | null
}

export const skillsApi = {
  list: () => request<{ items: InstalledSkill[]; total: number }>('/skills'),
  stats: () => request<SkillStats>('/skills/stats'),
  detail: (id: string) => request<InstalledSkill>(`/skills/${id}`),

  /** Search public sources. Writes nothing — adopting is a separate, explicit step. */
  discover: (need: string) =>
    request<DiscoveryResponse>('/skills/discover', { method: 'POST', body: { need } }),

  adopt: (payload: {
    name: string
    description: string
    when_to_use?: string
    body: string
    source: string
    source_commit?: string
    license?: string
    version?: string
    params?: { name: string; description?: string }[]
    param_values?: Record<string, string>
  }) =>
    request<InstalledSkill>('/skills/adopt', {
      method: 'POST',
      // Spelled out rather than defaulted: this is the user saying yes, and it should be
      // visible at the call site that gives it.
      body: { ...payload, confirmed: true },
    }),

  recordUse: (id: string, succeeded: boolean) =>
    request<{ recorded: boolean }>(`/skills/${id}/use`, { method: 'POST', body: { succeeded } }),

  review: (id: string, payload: { rating: number; note?: string; retire?: boolean }) =>
    request<InstalledSkill>(`/skills/${id}/review`, { method: 'POST', body: payload }),

  disable: (id: string) => request<InstalledSkill>(`/skills/${id}`, { method: 'DELETE' }),
}
