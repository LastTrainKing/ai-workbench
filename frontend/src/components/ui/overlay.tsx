import { useCallback, useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { create } from 'zustand'
import { AlertTriangle, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button, type ButtonVariant } from './Button'

/* ------------------------------------------------------------ portal shell */

function useEscapeAndLock(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previous
    }
  }, [open, onClose])
}

interface OverlayProps {
  open: boolean
  onClose: () => void
  children: ReactNode
  labelledBy?: string
  className?: string
}

/* ------------------------------------------------------------------- modal */

export interface ModalProps extends OverlayProps {
  title?: ReactNode
  description?: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg' | 'xl'
}

const MODAL_SIZES = {
  sm: 'max-w-sm',
  md: 'max-w-lg',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}

export function Modal({
  open,
  onClose,
  title,
  description,
  footer,
  size = 'md',
  children,
  labelledBy,
  className,
}: ModalProps) {
  const close = useCallback(() => onClose(), [onClose])
  useEscapeAndLock(open, close)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const timer = window.setTimeout(() => {
      const focusable = panelRef.current?.querySelector<HTMLElement>(
        'input, textarea, select, button, [tabindex]:not([tabindex="-1"])',
      )
      focusable?.focus()
    }, 30)
    return () => window.clearTimeout(timer)
  }, [open])

  if (!open || typeof document === 'undefined') return null

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto p-4 sm:items-center">
      <div
        className="fixed inset-0 bg-overlay animate-fade-in"
        onClick={close}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        className={cn(
          'relative z-10 my-auto w-full rounded-lg border border-subtle bg-surface shadow-pop animate-slide-up',
          MODAL_SIZES[size],
          className,
        )}
      >
        {title ? (
          <header className="flex items-start justify-between gap-3 border-b border-subtle px-4 py-3">
            <div className="min-w-0">
              <h2 id={labelledBy} className="text-sm font-semibold text-primary">
                {title}
              </h2>
              {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
            </div>
            <button
              type="button"
              onClick={close}
              aria-label="关闭"
              className="-mr-1 -mt-1 rounded p-1 text-faint transition-colors hover:bg-elevated hover:text-primary"
            >
              <X className="size-4" />
            </button>
          </header>
        ) : null}
        <div className="px-4 py-4">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-end gap-2 border-t border-subtle px-4 py-3">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}

/* ------------------------------------------------------------------ drawer */

export interface DrawerProps extends OverlayProps {
  title?: ReactNode
  subtitle?: ReactNode
  footer?: ReactNode
  headerActions?: ReactNode
  width?: string
}

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  footer,
  headerActions,
  width = 'max-w-2xl',
  children,
  className,
}: DrawerProps) {
  const close = useCallback(() => onClose(), [onClose])
  useEscapeAndLock(open, close)

  if (!open || typeof document === 'undefined') return null

  return createPortal(
    <div className="fixed inset-0 z-[60] flex justify-end">
      <div className="fixed inset-0 bg-overlay animate-fade-in" onClick={close} aria-hidden />
      <aside
        role="dialog"
        aria-modal="true"
        className={cn(
          'relative z-10 flex h-full w-full flex-col border-l border-subtle bg-surface shadow-pop animate-slide-in-right',
          width,
          className,
        )}
      >
        <header className="flex items-start justify-between gap-3 border-b border-subtle px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-primary">{title}</h2>
            {subtitle ? <div className="mt-0.5 text-xs text-muted">{subtitle}</div> : null}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {headerActions}
            <button
              type="button"
              onClick={close}
              aria-label="关闭"
              className="rounded p-1 text-faint transition-colors hover:bg-elevated hover:text-primary"
            >
              <X className="size-4" />
            </button>
          </div>
        </header>
        <div className="aiw-scroll flex-1 px-4 py-4">{children}</div>
        {footer ? (
          <footer className="flex items-center justify-between gap-2 border-t border-subtle px-4 py-3">
            {footer}
          </footer>
        ) : null}
      </aside>
    </div>,
    document.body,
  )
}

/* --------------------------------------------------------- confirm dialogs */

export interface ConfirmOptions {
  title: string
  description?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'danger' | 'primary'
  /** Extra detail, e.g. the entity name being deleted. */
  detail?: ReactNode
}

interface ConfirmState {
  options: ConfirmOptions | null
  resolve: ((value: boolean) => void) | null
  request: (options: ConfirmOptions) => Promise<boolean>
  settle: (value: boolean) => void
}

export const useConfirmStore = create<ConfirmState>((set, get) => ({
  options: null,
  resolve: null,
  request(options) {
    // A second request while one is pending resolves the previous one as cancelled.
    get().resolve?.(false)
    return new Promise<boolean>((resolve) => {
      set({ options, resolve })
    })
  },
  settle(value) {
    get().resolve?.(value)
    set({ options: null, resolve: null })
  },
}))

/** Imperative confirm: `if (await confirm({...})) { … }` */
export function confirm(options: ConfirmOptions): Promise<boolean> {
  return useConfirmStore.getState().request(options)
}

export function ConfirmHost() {
  const options = useConfirmStore((state) => state.options)
  const settle = useConfirmStore((state) => state.settle)

  const tone: ButtonVariant = options?.tone === 'danger' ? 'danger' : 'primary'

  return (
    <Modal
      open={Boolean(options)}
      onClose={() => settle(false)}
      title={options?.title ?? ''}
      size="sm"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={() => settle(false)}>
            {options?.cancelLabel ?? '取消'}
          </Button>
          <Button variant={tone} size="sm" onClick={() => settle(true)}>
            {options?.confirmLabel ?? '确认'}
          </Button>
        </>
      }
    >
      <div className="flex gap-3">
        <div
          className={cn(
            'flex size-8 shrink-0 items-center justify-center rounded-md border',
            options?.tone === 'danger'
              ? 'border-danger/40 bg-danger-soft text-danger'
              : 'border-subtle bg-elevated text-muted',
          )}
        >
          <AlertTriangle className="size-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1 space-y-2 text-xs text-muted">
          {options?.description ? <div>{options.description}</div> : null}
          {options?.detail ? (
            <div className="rounded border border-subtle bg-inset px-2.5 py-2 font-mono text-2xs text-primary">
              {options.detail}
            </div>
          ) : null}
        </div>
      </div>
    </Modal>
  )
}
