import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Compose Tailwind classes with conflict resolution. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/* ---------------------------------------------------------------- formatting */

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

/** "3 分钟前" / "2 天前" — falls back to an absolute date beyond 30 days. */
export function relativeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  const diff = Date.now() - d.getTime()
  const abs = Math.abs(diff)
  const suffix = diff >= 0 ? '前' : '后'
  const units: [number, string][] = [
    [1000, '秒'],
    [60_000, '分钟'],
    [3_600_000, '小时'],
    [86_400_000, '天'],
  ]
  if (abs < 45_000) return '刚刚'
  if (abs < 3_600_000) return `${Math.round(abs / 60_000)} 分钟${suffix}`
  if (abs < 86_400_000) return `${Math.round(abs / 3_600_000)} 小时${suffix}`
  if (abs < 30 * 86_400_000) return `${Math.round(abs / 86_400_000)} 天${suffix}`
  void units
  return formatDate(value)
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '—'
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / Math.pow(1024, i)
  return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return new Intl.NumberFormat('zh-CN').format(value)
}

/** 12345 → "12.3k" for tight metric tiles. */
export function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  if (Math.abs(value) < 1000) return String(value)
  if (Math.abs(value) < 1_000_000) return `${(value / 1000).toFixed(value % 1000 === 0 ? 0 : 1)}k`
  return `${(value / 1_000_000).toFixed(1)}M`
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}

export function formatMinutes(minutes: number | null | undefined): string {
  if (!minutes) return '—'
  if (minutes < 60) return `${minutes} 分钟`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m === 0 ? `${h} 小时` : `${h} 小时 ${m} 分`
}

export function percent(part: number, total: number): number {
  if (!total) return 0
  return Math.round((part / total) * 100)
}

export function truncate(text: string, max = 120): string {
  if (!text) return ''
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`
}

export function initials(name: string): string {
  if (!name) return '?'
  const trimmed = name.trim()
  if (!trimmed) return '?'
  const ascii = trimmed.split(/\s+/)
  if (ascii.length >= 2 && /^[\x20-\x7e]+$/.test(trimmed)) {
    return (ascii[0][0] + ascii[1][0]).toUpperCase()
  }
  return trimmed.slice(0, 2).toUpperCase()
}

/* ------------------------------------------------------------------- tones */

export type Tone = 'neutral' | 'accent' | 'success' | 'warn' | 'danger' | 'info'

const TASK_STATUS_TONE: Record<string, Tone> = {
  TODO: 'neutral',
  IN_PROGRESS: 'info',
  BLOCKED: 'danger',
  DONE: 'success',
  CANCELLED: 'neutral',
}

const DOCUMENT_STATUS_TONE: Record<string, Tone> = {
  PENDING: 'warn',
  PROCESSING: 'info',
  COMPLETED: 'success',
  FAILED: 'danger',
}

const RUN_STATUS_TONE: Record<string, Tone> = {
  RUNNING: 'info',
  SUCCEEDED: 'success',
  // Stopped without finishing (step ceiling, loop guard, empty response, unverified
  // deliverable) and interrupted by a restart. Both are "not a failure, not a success"
  // and must not be painted green.
  INCOMPLETE: 'warn',
  INTERRUPTED: 'warn',
  FAILED: 'danger',
  TIMEOUT: 'warn',
  CANCELLED: 'neutral',
}

const PRIORITY_TONE: Record<string, Tone> = {
  LOW: 'neutral',
  MEDIUM: 'info',
  HIGH: 'warn',
  URGENT: 'danger',
}

export function taskStatusTone(status: string): Tone {
  return TASK_STATUS_TONE[status] ?? 'neutral'
}

export function documentStatusTone(status: string): Tone {
  return DOCUMENT_STATUS_TONE[status] ?? 'neutral'
}

export function runStatusTone(status: string): Tone {
  return RUN_STATUS_TONE[status] ?? 'neutral'
}

export function priorityTone(priority: string): Tone {
  return PRIORITY_TONE[priority] ?? 'neutral'
}

/* -------------------------------------------------------------- misc helpers */

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function isDueToday(value: string | null | undefined): boolean {
  if (!value) return false
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return false
  const now = new Date()
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  )
}

export function isOverdue(value: string | null | undefined): boolean {
  if (!value) return false
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return false
  return d.getTime() < Date.now() && !isDueToday(value)
}

/** Short, human label for a run type enum. */
export function runTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    RAG_ANSWER: 'RAG 问答',
    DOCUMENT_SUMMARY: '文档摘要',
    KNOWLEDGE_EXTRACTION: '知识抽取',
    TASK_PLANNER: '任务规划',
    AGENT: 'Agent 执行',
    NOTE_GENERATION: '笔记生成',
    INTERVIEW_GENERATOR: '面试题生成',
    STUDY_PLAN: '学习计划',
    EMBEDDING: '向量化',
    QUERY_REWRITE: '查询改写',
  }
  return labels[type] ?? type
}

export function taskStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    TODO: '待办',
    IN_PROGRESS: '进行中',
    BLOCKED: '受阻',
    DONE: '已完成',
    CANCELLED: '已取消',
  }
  return labels[status] ?? status
}

export function priorityLabel(priority: string): string {
  const labels: Record<string, string> = {
    LOW: '低',
    MEDIUM: '中',
    HIGH: '高',
    URGENT: '紧急',
  }
  return labels[priority] ?? priority
}

export function documentStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    PENDING: '排队中',
    PROCESSING: '处理中',
    COMPLETED: '已完成',
    FAILED: '失败',
  }
  return labels[status] ?? status
}

export function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    MANUAL: '手动',
    PLANNER: '规划器',
    AGENT: 'Agent',
  }
  return labels[source] ?? source
}

/** Simple deterministic colour from a string — used for avatars and graph nodes. */
export function colorFromString(input: string): string {
  const palette = ['#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#06b6d4', '#ef4444']
  let hash = 0
  for (let i = 0; i < input.length; i += 1) {
    hash = (hash * 31 + input.charCodeAt(i)) % 100000
  }
  return palette[hash % palette.length]
}
