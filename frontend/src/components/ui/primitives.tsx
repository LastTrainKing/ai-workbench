import { useState, type ReactNode } from 'react'
import { Check, ChevronLeft, ChevronRight, Copy } from 'lucide-react'
import { cn, initials, type Tone } from '@/lib/utils'
import { IconButton } from './Button'

/* ------------------------------------------------------------------- badge */

const TONE_CLASSES: Record<Tone, string> = {
  neutral: 'border-subtle bg-elevated text-muted',
  accent: 'border-accent/40 bg-accent-soft text-accent',
  success: 'border-success/40 bg-success-soft text-success',
  warn: 'border-warn/40 bg-warn-soft text-warn',
  danger: 'border-danger/40 bg-danger-soft text-danger',
  info: 'border-info/40 bg-info-soft text-info',
}

export interface BadgeProps {
  tone?: Tone
  children: ReactNode
  className?: string
  icon?: ReactNode
  mono?: boolean
  title?: string
}

export function Badge({ tone = 'neutral', children, className, icon, mono, title }: BadgeProps) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-px text-2xs font-medium',
        TONE_CLASSES[tone],
        mono && 'font-mono',
        className,
      )}
    >
      {icon}
      {children}
    </span>
  )
}

export function Dot({ tone = 'neutral', className }: { tone?: Tone; className?: string }) {
  const color = {
    neutral: 'bg-faint',
    accent: 'bg-accent',
    success: 'bg-success',
    warn: 'bg-warn',
    danger: 'bg-danger',
    info: 'bg-info',
  }[tone]
  return <span className={cn('inline-block size-1.5 shrink-0 rounded-full', color, className)} />
}

/* -------------------------------------------------------------------- card */

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('aiw-card', className)}>{children}</div>
}

export function CardHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex items-start justify-between gap-3 border-b border-subtle px-3.5 py-2.5', className)}>
      <div className="min-w-0">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
        {description ? <p className="mt-0.5 text-xs text-faint">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
    </div>
  )
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('px-3.5 py-3', className)}>{children}</div>
}

export function CardFooter({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div className={cn('flex items-center gap-2 border-t border-subtle px-3.5 py-2.5', className)}>
      {children}
    </div>
  )
}

/* --------------------------------------------------------------- stat tile */

export function StatTile({
  label,
  value,
  sub,
  icon,
  tone = 'neutral',
  className,
}: {
  label: ReactNode
  value: ReactNode
  sub?: ReactNode
  icon?: ReactNode
  tone?: Tone
  className?: string
}) {
  return (
    <div className={cn('aiw-card px-3.5 py-3', className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-2xs font-medium uppercase tracking-wide text-faint">{label}</span>
        {icon ? (
          <span
            className={cn(
              'flex size-5 items-center justify-center rounded border',
              TONE_CLASSES[tone],
            )}
          >
            {icon}
          </span>
        ) : null}
      </div>
      <p className="mt-1.5 text-xl font-semibold leading-tight text-primary tabular-nums">{value}</p>
      {sub ? <p className="mt-0.5 text-2xs text-muted">{sub}</p> : null}
    </div>
  )
}

/* -------------------------------------------------------------- score bars */

export interface ScoreBarProps {
  label: string
  /** 0..1 */
  value: number
  tone?: Tone
  className?: string
  format?: (value: number) => string
}

export function ScoreBar({ label, value, tone = 'accent', className, format }: ScoreBarProps) {
  const pct = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) * 100
  const fill = {
    neutral: 'bg-faint',
    accent: 'bg-accent',
    success: 'bg-success',
    warn: 'bg-warn',
    danger: 'bg-danger',
    info: 'bg-info',
  }[tone]
  return (
    <div className={cn('flex items-center gap-2', className)} title={`${label}: ${value.toFixed(4)}`}>
      <span className="w-16 shrink-0 text-2xs text-faint">{label}</span>
      <div className="h-1 flex-1 overflow-hidden rounded-full bg-elevated">
        <div className={cn('h-full rounded-full', fill)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 shrink-0 text-right font-mono text-2xs text-muted tabular-nums">
        {format ? format(value) : value.toFixed(3)}
      </span>
    </div>
  )
}

/* -------------------------------------------------------------- key/value */

export function KeyValue({
  items,
  className,
  columns = 1,
}: {
  items: { key: ReactNode; value: ReactNode }[]
  className?: string
  columns?: 1 | 2
}) {
  return (
    <dl
      className={cn('grid gap-x-6 gap-y-2', columns === 2 ? 'sm:grid-cols-2' : 'grid-cols-1', className)}
    >
      {items.map((item, index) => (
        <div key={index} className="flex items-baseline justify-between gap-3 border-b border-subtle pb-1.5">
          <dt className="shrink-0 text-2xs uppercase tracking-wide text-faint">{item.key}</dt>
          <dd className="min-w-0 truncate text-right text-xs text-primary">{item.value}</dd>
        </div>
      ))}
    </dl>
  )
}

/* ------------------------------------------------------------------ avatar */

export function Avatar({
  name,
  color,
  size = 28,
  className,
}: {
  name: string
  color?: string
  size?: number
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white',
        className,
      )}
      style={{
        width: size,
        height: size,
        backgroundColor: color || 'rgb(var(--c-accent))',
        fontSize: Math.round(size * 0.4),
      }}
      aria-hidden
    >
      {initials(name)}
    </span>
  )
}

/* -------------------------------------------------------------------- tabs */

export interface TabItem {
  key: string
  label: ReactNode
  icon?: ReactNode
  count?: number
}

export function Tabs({
  items,
  value,
  onChange,
  className,
}: {
  items: TabItem[]
  value: string
  onChange: (key: string) => void
  className?: string
}) {
  return (
    <div role="tablist" className={cn('flex items-center gap-0.5 border-b border-subtle', className)}>
      {items.map((item) => {
        const active = item.key === value
        return (
          <button
            key={item.key}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(item.key)}
            className={cn(
              'relative -mb-px inline-flex items-center gap-1.5 border-b-2 px-2.5 py-2 text-xs font-medium transition-colors',
              active
                ? 'border-accent text-primary'
                : 'border-transparent text-muted hover:text-primary',
            )}
          >
            {item.icon}
            {item.label}
            {item.count !== undefined ? (
              <span className="rounded bg-elevated px-1 font-mono text-2xs text-faint tabular-nums">
                {item.count}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------- segmented control */

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  className,
  size = 'sm',
}: {
  options: { value: T; label: ReactNode; icon?: ReactNode; title?: string }[]
  value: T
  onChange: (value: T) => void
  className?: string
  size?: 'sm' | 'md'
}) {
  return (
    <div
      role="radiogroup"
      className={cn('inline-flex items-center gap-0.5 rounded-md border border-subtle bg-inset p-0.5', className)}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded font-medium transition-colors',
              size === 'sm' ? 'px-2 py-1 text-2xs' : 'px-2.5 py-1.5 text-xs',
              active ? 'bg-accent text-accent-fg' : 'text-muted hover:bg-elevated hover:text-primary',
            )}
          >
            {option.icon}
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

/* -------------------------------------------------------------- pagination */

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  className,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
  className?: string
}) {
  const totalPages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)))
  if (total === 0) return null
  const from = (page - 1) * pageSize + 1
  const to = Math.min(page * pageSize, total)
  return (
    <div className={cn('flex items-center justify-between gap-3 pt-3', className)}>
      <span className="text-2xs text-faint tabular-nums">
        {from}–{to} / {total}
      </span>
      <div className="flex items-center gap-1.5">
        <IconButton label="上一页" size="xs" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft className="size-3.5" />
        </IconButton>
        <span className="min-w-[64px] text-center text-2xs text-muted tabular-nums">
          {page} / {totalPages}
        </span>
        <IconButton
          label="下一页"
          size="xs"
          disabled={page >= totalPages}
          onClick={() => onPage(page + 1)}
        >
          <ChevronRight className="size-3.5" />
        </IconButton>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------- code block */

export function CodeBlock({
  value,
  className,
  maxHeight = 320,
  language,
}: {
  value: unknown
  className?: string
  maxHeight?: number
  language?: string
}) {
  const [copied, setCopied] = useState(false)
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable — the code is still selectable */
    }
  }

  return (
    <div className={cn('relative overflow-hidden rounded-md border border-subtle bg-inset', className)}>
      <div className="flex items-center justify-between border-b border-subtle px-2.5 py-1">
        <span className="font-mono text-2xs text-faint">{language ?? 'json'}</span>
        <button
          type="button"
          onClick={copy}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs text-muted transition-colors hover:bg-elevated hover:text-primary"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre
        className="aiw-scroll overflow-x-auto p-2.5 font-mono text-2xs leading-relaxed text-primary"
        style={{ maxHeight }}
      >
        {text}
      </pre>
    </div>
  )
}

/* ----------------------------------------------------------- section header */

export function SectionHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-3', className)}>
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight text-primary">{title}</h1>
        {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

/* --------------------------------------------------------------- page shell */

export function PageContainer({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('mx-auto w-full max-w-[1400px] px-4 py-4 sm:px-6 sm:py-5', className)}>{children}</div>
}
