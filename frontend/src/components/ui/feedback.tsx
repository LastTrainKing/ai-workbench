import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, Loader2, RefreshCw, X, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from './Button'
import { useToastStore, type ToastKind } from '@/store/toast'

/* ---------------------------------------------------------------- skeleton */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('aiw-skeleton h-4 w-full', className)} aria-hidden />
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)} aria-hidden>
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} className={cn('h-3.5', index === lines - 1 ? 'w-2/3' : 'w-full')} />
      ))}
    </div>
  )
}

export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={cn('aiw-card space-y-3 p-4', className)} aria-hidden>
      <div className="flex items-center gap-2.5">
        <Skeleton className="size-8 shrink-0 rounded-md" />
        <div className="flex-1 space-y-1.5">
          <Skeleton className="h-3.5 w-1/3" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
      <SkeletonText lines={2} />
    </div>
  )
}

export function SkeletonTable({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="aiw-card overflow-hidden" aria-hidden>
      <div className="flex items-center gap-4 border-b border-subtle bg-elevated px-3 py-2">
        {Array.from({ length: cols }).map((_, index) => (
          <Skeleton key={index} className="h-3 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex items-center gap-4 border-b border-subtle px-3 py-2.5 last:border-b-0">
          {Array.from({ length: cols }).map((_, colIndex) => (
            <Skeleton key={colIndex} className={cn('h-3.5 flex-1', colIndex === 0 && 'max-w-[40%]')} />
          ))}
        </div>
      ))}
    </div>
  )
}

/** Generic loading block used by list pages. */
export function LoadingBlock({ label = '加载中…', className }: { label?: string; className?: string }) {
  return (
    <div className={cn('flex items-center justify-center gap-2 py-10 text-xs text-muted', className)}>
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </div>
  )
}

/* ------------------------------------------------------------ empty / error */

export interface EmptyStateProps {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  className?: string
  compact?: boolean
}

export function EmptyState({ icon, title, description, action, className, compact }: EmptyStateProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border border-dashed border-subtle text-center',
        compact ? 'gap-2 px-4 py-8' : 'gap-3 px-6 py-14',
        className,
      )}
    >
      {icon ? (
        <div className="flex size-9 items-center justify-center rounded-lg border border-subtle bg-elevated text-muted">
          {icon}
        </div>
      ) : null}
      <div className="space-y-1">
        <p className="text-sm font-medium text-primary">{title}</p>
        {description ? <p className="mx-auto max-w-md text-xs text-muted">{description}</p> : null}
      </div>
      {action}
    </div>
  )
}

export interface ErrorStateProps {
  title?: string
  error?: unknown
  onRetry?: () => void
  className?: string
  compact?: boolean
}

export function ErrorState({
  title = '加载失败',
  error,
  onRetry,
  className,
  compact,
}: ErrorStateProps) {
  const message =
    error instanceof Error ? error.message : typeof error === 'string' ? error : '发生了未知错误'
  const code =
    error && typeof error === 'object' && 'code' in error
      ? String((error as { code?: unknown }).code ?? '')
      : ''
  const requestId =
    error && typeof error === 'object' && 'requestId' in error
      ? String((error as { requestId?: unknown }).requestId ?? '')
      : ''

  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center gap-2.5 rounded-lg border border-subtle bg-surface text-center',
        compact ? 'px-4 py-6' : 'px-6 py-12',
        className,
      )}
    >
      <div className="flex size-9 items-center justify-center rounded-lg border border-subtle bg-danger-soft text-danger">
        <AlertTriangle className="size-4" aria-hidden />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium text-primary">{title}</p>
        <p className="mx-auto max-w-md break-words text-xs text-muted">{message}</p>
        {code || requestId ? (
          <p className="font-mono text-2xs text-faint">
            {code}
            {requestId ? ` · ${requestId}` : ''}
          </p>
        ) : null}
      </div>
      {onRetry ? (
        <Button size="sm" variant="secondary" icon={<RefreshCw className="size-3.5" />} onClick={onRetry}>
          重试
        </Button>
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------------ alerts */

export type AlertTone = 'info' | 'success' | 'warn' | 'danger'

const ALERT_STYLES: Record<AlertTone, { wrap: string; icon: ReactNode }> = {
  info: {
    wrap: 'border-info/40 bg-info-soft text-info',
    icon: <Info className="size-3.5" aria-hidden />,
  },
  success: {
    wrap: 'border-success/40 bg-success-soft text-success',
    icon: <CheckCircle2 className="size-3.5" aria-hidden />,
  },
  warn: {
    wrap: 'border-warn/40 bg-warn-soft text-warn',
    icon: <AlertTriangle className="size-3.5" aria-hidden />,
  },
  danger: {
    wrap: 'border-danger/40 bg-danger-soft text-danger',
    icon: <XCircle className="size-3.5" aria-hidden />,
  },
}

export interface InlineAlertProps {
  tone?: AlertTone
  title?: ReactNode
  children?: ReactNode
  action?: ReactNode
  className?: string
}

export function InlineAlert({ tone = 'info', title, children, action, className }: InlineAlertProps) {
  const styles = ALERT_STYLES[tone]
  return (
    <div className={cn('flex items-start gap-2.5 rounded-md border px-3 py-2', styles.wrap, className)}>
      <span className="mt-[3px] shrink-0">{styles.icon}</span>
      <div className="min-w-0 flex-1 text-xs">
        {title ? <p className="font-medium text-primary">{title}</p> : null}
        {children ? <div className="text-muted">{children}</div> : null}
      </div>
      {action}
    </div>
  )
}

/* --------------------------------------------------------------- progress */

export interface ProgressBarProps {
  value: number
  max?: number
  className?: string
  tone?: 'accent' | 'success' | 'warn' | 'danger' | 'info'
  showLabel?: boolean
}

export function ProgressBar({ value, max = 100, className, tone = 'accent', showLabel }: ProgressBarProps) {
  const pct = Math.max(0, Math.min(100, max === 0 ? 0 : (value / max) * 100))
  const toneClass = {
    accent: 'bg-accent',
    success: 'bg-success',
    warn: 'bg-warn',
    danger: 'bg-danger',
    info: 'bg-info',
  }[tone]
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <div
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-elevated"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={cn('h-full rounded-full transition-[width] duration-300', toneClass)}
          style={{ width: `${pct}%` }}
        />
      </div>
      {showLabel ? <span className="w-8 shrink-0 text-right text-2xs text-muted">{Math.round(pct)}%</span> : null}
    </div>
  )
}

/* ----------------------------------------------------------------- toaster */

const TOAST_STYLES: Record<ToastKind, { border: string; icon: ReactNode }> = {
  success: { border: 'border-l-success', icon: <CheckCircle2 className="size-4 text-success" /> },
  error: { border: 'border-l-danger', icon: <XCircle className="size-4 text-danger" /> },
  info: { border: 'border-l-info', icon: <Info className="size-4 text-info" /> },
  warn: { border: 'border-l-warn', icon: <AlertTriangle className="size-4 text-warn" /> },
}

export function Toaster() {
  const toasts = useToastStore((state) => state.toasts)
  const dismiss = useToastStore((state) => state.dismiss)

  if (toasts.length === 0) return null

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-[70] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2"
      role="region"
      aria-label="通知"
    >
      {toasts.map((toast) => {
        const styles = TOAST_STYLES[toast.kind]
        return (
          <div
            key={toast.id}
            role="status"
            className={cn(
              'pointer-events-auto flex items-start gap-2.5 rounded-md border border-l-2 border-subtle bg-elevated px-3 py-2.5 shadow-pop animate-slide-up',
              styles.border,
            )}
          >
            <span className="mt-[2px] shrink-0">{styles.icon}</span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium text-primary">{toast.title}</p>
              {toast.description ? (
                <p className="mt-0.5 break-words text-2xs text-muted">{toast.description}</p>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="关闭通知"
              onClick={() => dismiss(toast.id)}
              className="-mr-1 -mt-0.5 shrink-0 rounded p-1 text-faint transition-colors hover:bg-surface hover:text-primary"
            >
              <X className="size-3.5" />
            </button>
          </div>
        )
      })}
    </div>
  )
}

/** Small inline spinner for buttons and inline loading rows. */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('size-3.5 animate-spin', className)} aria-hidden />
}
