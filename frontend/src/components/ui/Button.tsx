import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'outline'
export type ButtonSize = 'xs' | 'sm' | 'md'

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-accent-fg hover:bg-accent-hover border border-transparent shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]',
  secondary: 'bg-elevated text-primary border border-subtle hover:border-strong hover:bg-surface',
  outline: 'bg-transparent text-primary border border-strong hover:bg-elevated',
  ghost: 'bg-transparent text-muted border border-transparent hover:bg-elevated hover:text-primary',
  danger: 'bg-danger text-white border border-transparent hover:opacity-90',
  success: 'bg-success text-white border border-transparent hover:opacity-90',
}

const SIZES: Record<ButtonSize, string> = {
  xs: 'h-6 gap-1 rounded px-1.5 text-2xs',
  sm: 'h-8 gap-1.5 rounded px-2.5 text-xs',
  md: 'h-9 gap-2 rounded-md px-3.5 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
  iconRight?: ReactNode
  block?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size = 'sm',
    loading = false,
    icon,
    iconRight,
    block = false,
    className,
    children,
    disabled,
    type = 'button',
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={cn(
        'inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap font-medium',
        'transition-colors duration-100 disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        block && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : icon}
      {children}
      {iconRight}
    </button>
  )
})

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  variant?: ButtonVariant
  size?: ButtonSize
  active?: boolean
}

/** Square, label-only-for-screen-readers button used across toolbars. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, variant = 'ghost', size = 'sm', active = false, className, children, ...rest },
  ref,
) {
  const dimension = size === 'xs' ? 'size-6' : size === 'md' ? 'size-9' : 'size-8'
  return (
    <button
      ref={ref}
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded border transition-colors duration-100',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        dimension,
        active && 'bg-accent-soft text-accent',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
})
