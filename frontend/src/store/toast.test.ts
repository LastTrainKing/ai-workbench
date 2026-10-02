import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toastApiError, useToastStore } from './toast'
import { ApiError } from '@/lib/api'

beforeEach(() => {
  useToastStore.setState({ toasts: [] })
})

describe('useToastStore', () => {
  it('pushes a toast and returns its id', () => {
    const id = useToastStore.getState().push({ kind: 'success', title: '已保存' })
    const { toasts } = useToastStore.getState()

    expect(toasts).toHaveLength(1)
    expect(toasts[0]).toMatchObject({ id, kind: 'success', title: '已保存' })
    expect(toasts[0].duration).toBe(4000)
  })

  it('gives errors a longer default duration', () => {
    useToastStore.getState().error('失败', 'conflict')
    expect(useToastStore.getState().toasts[0].duration).toBe(8000)
  })

  it('dismisses by id', () => {
    const id = useToastStore.getState().push({ kind: 'info', title: 'hi' })
    useToastStore.getState().dismiss(id)
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('keeps only the five most recent toasts', () => {
    for (let index = 0; index < 7; index += 1) {
      useToastStore.getState().push({ kind: 'info', title: `t${index}` })
    }
    const { toasts } = useToastStore.getState()
    expect(toasts).toHaveLength(5)
    expect(toasts[0].title).toBe('t2')
    expect(toasts[4].title).toBe('t6')
  })

  it('auto-dismisses after the duration elapses', () => {
    vi.useFakeTimers()
    useToastStore.getState().push({ kind: 'info', title: 'temp', duration: 1000 })
    expect(useToastStore.getState().toasts).toHaveLength(1)

    vi.advanceTimersByTime(1001)
    expect(useToastStore.getState().toasts).toHaveLength(0)
    vi.useRealTimers()
  })
})

describe('toastApiError', () => {
  it('surfaces the backend code and request id', () => {
    toastApiError(
      new ApiError({
        code: 'conflict',
        message: '任务存在未完成的依赖',
        status: 409,
        requestId: 'req-9',
      }),
      '无法完成任务',
    )

    const [toast] = useToastStore.getState().toasts
    expect(toast.kind).toBe('error')
    expect(toast.title).toBe('无法完成任务')
    expect(toast.description).toContain('任务存在未完成的依赖')
    expect(toast.description).toContain('conflict')
    expect(toast.description).toContain('request_id: req-9')
  })

  it('falls back to a generic title', () => {
    toastApiError(new Error('boom'))
    expect(useToastStore.getState().toasts[0].title).toBe('操作失败')
  })
})
