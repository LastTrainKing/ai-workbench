import { create } from 'zustand'
import { toApiError } from '@/lib/api'

export type ToastKind = 'success' | 'error' | 'info' | 'warn'

export interface Toast {
  id: string
  kind: ToastKind
  title: string
  description?: string
  /** Milliseconds; 0 keeps the toast until it is dismissed manually. */
  duration: number
}

interface ToastState {
  toasts: Toast[]
  push: (toast: Omit<Toast, 'id' | 'duration'> & { duration?: number }) => string
  success: (title: string, description?: string) => string
  error: (title: string, description?: string) => string
  info: (title: string, description?: string) => string
  warn: (title: string, description?: string) => string
  dismiss: (id: string) => void
  clear: () => void
}

let counter = 0

function nextId(): string {
  counter += 1
  return `t${Date.now().toString(36)}${counter}`
}

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],

  push({ kind, title, description, duration }) {
    const id = nextId()
    const toast: Toast = {
      id,
      kind,
      title,
      description,
      duration: duration ?? (kind === 'error' ? 8000 : 4000),
    }
    set({ toasts: [...get().toasts, toast].slice(-5) })
    if (toast.duration > 0) {
      setTimeout(() => get().dismiss(id), toast.duration)
    }
    return id
  },

  success(title, description) {
    return get().push({ kind: 'success', title, description })
  },

  error(title, description) {
    return get().push({ kind: 'error', title, description })
  },

  info(title, description) {
    return get().push({ kind: 'info', title, description })
  },

  warn(title, description) {
    return get().push({ kind: 'warn', title, description })
  },

  dismiss(id) {
    set({ toasts: get().toasts.filter((toast) => toast.id !== id) })
  },

  clear() {
    set({ toasts: [] })
  },
}))

/**
 * Surface an API failure through the toast system, keeping the backend's
 * `error.code` and `request_id` visible so the user (and a marker) can trace it.
 */
export function toastApiError(error: unknown, fallbackTitle = '操作失败'): void {
  const apiError = toApiError(error)
  const detailParts = [apiError.code]
  if (apiError.requestId) detailParts.push(`request_id: ${apiError.requestId}`)
  useToastStore.getState().error(fallbackTitle, `${apiError.message} · ${detailParts.join(' · ')}`)
}
