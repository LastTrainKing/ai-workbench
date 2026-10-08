import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/utils'

/* ------------------------------------------------------------------- field */

export interface FieldProps {
  label?: ReactNode
  hint?: ReactNode
  error?: ReactNode
  required?: boolean
  htmlFor?: string
  className?: string
  children: ReactNode
}

export function Field({ label, hint, error, required, htmlFor, className, children }: FieldProps) {
  return (
    <div className={cn('flex flex-col', className)}>
      {label ? (
        <label className="aiw-label" htmlFor={htmlFor}>
          {label}
          {required ? <span className="ml-0.5 text-danger">*</span> : null}
        </label>
      ) : null}
      {children}
      {error ? (
        <p className="mt-1 text-2xs text-danger">{error}</p>
      ) : hint ? (
        <p className="mt-1 text-2xs text-faint">{hint}</p>
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------------- input */

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, invalid, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn('aiw-input', invalid && 'border-danger', className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  )
})

export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, invalid, rows = 4, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn('aiw-input resize-y leading-relaxed', invalid && 'border-danger', className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  )
})

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  invalid?: boolean
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, invalid, children, ...rest },
  ref,
) {
  return (
    <select
      ref={ref}
      className={cn('aiw-input cursor-pointer appearance-none pr-7', invalid && 'border-danger', className)}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238b95a5' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")",
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'right 6px center',
        backgroundSize: '14px',
      }}
      {...rest}
    >
      {children}
    </select>
  )
})

/* --------------------------------------------------------------- search box */

export interface SearchInputProps extends InputHTMLAttributes<HTMLInputElement> {
  containerClassName?: string
  shortcut?: string
}

export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(function SearchInput(
  { className, containerClassName, shortcut, ...rest },
  ref,
) {
  return (
    <div className={cn('relative flex items-center', containerClassName)}>
      <Search className="pointer-events-none absolute left-2.5 size-3.5 text-faint" aria-hidden />
      <input ref={ref} type="search" className={cn('aiw-input pl-8', className)} {...rest} />
      {shortcut ? (
        <span className="aiw-kbd pointer-events-none absolute right-2 font-sans">{shortcut}</span>
      ) : null}
    </div>
  )
})

/* ------------------------------------------------------------------ switch */

/**
 * Switch geometry, in one place, derived rather than written out.
 *
 * ## What was wrong
 *
 * The knob was positioned with `top-[2px]` and nothing else — no `left` — plus
 * `translate-x-[2px]` / `translate-x-[16px]`. That reads as "2px from the left, then slide
 * 16px", and the author's arithmetic was `2 + 16 + 14 = 32`, i.e. exactly the track width.
 * **The measured result was nothing like it.** The knob's own rect, in the two states:
 *
 *     关   左 17   右  -1        开   左 31   右 -15
 *
 * 15px further right than intended, in both states, which is why the knob crossed the
 * track's right edge. Two separate mistakes stacked:
 *
 * 1. **An absolutely positioned box with `left: auto` uses its static position.** A
 *    `<button>` centres its content (`text-align: center`), so that position is the
 *    *centre* of the track — measured at 15px, not 0. Every offset was therefore added to
 *    15. Fixed by giving the knob an explicit `left`, which removes the static position
 *    from the question entirely.
 *
 * 2. **The knob lives in the track's padding box, not its border box.** The track has a
 *    1px border, so the space the knob can move in is 30px wide, not 32. `2 + 16 + 14`
 *    was 2px more than the room that exists even before the static-position error.
 *
 * ## The numbers
 *
 * Everything below is computed from `KNOB` and `INSET`, so changing one of them moves
 * everything else with it. The old version had three literals that had to agree and did
 * not; that is the whole bug.
 *
 *     轨道 32×20，边框 1 → 内边距盒 30×18，滑块 14，留白 2
 *     关  左 2  右 14        开  左 14  右 2        纵向 上 2  下 2
 */
const KNOB = 14
const INSET = 2
const BORDER = 1
const TRACK_WIDTH = 32
// 20 rather than 18: `INSET + KNOB + INSET` is 18, and the track needs a 1px border on
// top of that. At 18 the bottom gap was 0 — the knob sat on the bottom edge, which is the
// same mistake as the horizontal one, in the other axis.
const TRACK_HEIGHT = INSET * 2 + KNOB + BORDER * 2
//: How far the knob travels. This is what `translate-x-[16px]` was trying to be.
const TRAVEL = TRACK_WIDTH - BORDER * 2 - KNOB - INSET * 2

export interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: ReactNode
  description?: ReactNode
  disabled?: boolean
  className?: string
}

export function Switch({ checked, onChange, label, description, disabled, className }: SwitchProps) {
  const id = useId()
  return (
    <div className={cn('flex items-start gap-2.5', className)}>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-0.5 shrink-0 rounded-full border transition-colors duration-150',
          'disabled:cursor-not-allowed disabled:opacity-50',
          checked ? 'border-transparent bg-accent' : 'border-strong bg-elevated',
        )}
        style={{ height: TRACK_HEIGHT, width: TRACK_WIDTH }}
      >
        <span
          data-testid="switch-knob"
          className="absolute rounded-full bg-white transition-transform duration-150"
          style={{
            width: KNOB,
            height: KNOB,
            top: INSET,
            // Explicit, and this is the fix. With `left: auto` the knob started from the
            // button's centred static position (15px) instead of the padding box origin.
            left: INSET,
            transform: `translateX(${checked ? TRAVEL : 0}px)`,
          }}
        />
      </button>
      {label || description ? (
        <label htmlFor={id} className="cursor-pointer select-none">
          {label ? <span className="block text-sm text-primary">{label}</span> : null}
          {description ? <span className="block text-2xs text-muted">{description}</span> : null}
        </label>
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------ colour picker */

export const COLOR_SWATCHES = [
  '#6366f1',
  '#8b5cf6',
  '#ec4899',
  '#ef4444',
  '#f59e0b',
  '#10b981',
  '#06b6d4',
  '#3b82f6',
]

export interface ColorPickerProps {
  value: string
  onChange: (color: string) => void
  className?: string
}

export function ColorPicker({ value, onChange, className }: ColorPickerProps) {
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {COLOR_SWATCHES.map((color) => (
        <button
          key={color}
          type="button"
          aria-label={`选择颜色 ${color}`}
          aria-pressed={value.toLowerCase() === color}
          onClick={() => onChange(color)}
          className={cn(
            'size-6 rounded border transition-transform',
            value.toLowerCase() === color
              ? 'border-primary scale-110'
              : 'border-subtle hover:scale-105',
          )}
          style={{ backgroundColor: color }}
        />
      ))}
      <input
        type="color"
        value={/^#[0-9a-fA-F]{6}$/.test(value) ? value : '#6366f1'}
        onChange={(event) => onChange(event.target.value)}
        aria-label="自定义颜色"
        className="size-6 cursor-pointer rounded border border-subtle bg-transparent p-0"
      />
    </div>
  )
}
