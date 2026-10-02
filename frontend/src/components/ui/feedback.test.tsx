import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EmptyState, ErrorState, ProgressBar, Skeleton, SkeletonTable, Toaster } from './feedback'
import { useToastStore } from '@/store/toast'
import { ApiError } from '@/lib/api'

describe('Skeleton', () => {
  it('renders a decorative placeholder', () => {
    const { container } = render(<Skeleton className="h-6" />)
    const node = container.firstElementChild
    expect(node).toHaveClass('aiw-skeleton', 'h-6')
    expect(node).toHaveAttribute('aria-hidden', 'true')
  })

  it('renders the requested number of table rows', () => {
    const { container } = render(<SkeletonTable rows={3} cols={2} />)
    // header row + 3 body rows
    expect(container.querySelectorAll('.border-b')).toHaveLength(4)
  })
})

describe('EmptyState', () => {
  it('renders title, description and the call to action', () => {
    render(
      <EmptyState
        title="还没有文档"
        description="上传一个文件试试"
        action={<button type="button">上传文档</button>}
      />,
    )
    expect(screen.getByText('还没有文档')).toBeInTheDocument()
    expect(screen.getByText('上传一个文件试试')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上传文档' })).toBeInTheDocument()
  })
})

describe('ErrorState', () => {
  it('shows the ApiError message, code and request id', () => {
    const error = new ApiError({
      code: 'conflict',
      message: '任务存在未完成的依赖',
      status: 409,
      requestId: 'req-42',
    })
    render(<ErrorState title="无法完成任务" error={error} />)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('无法完成任务')).toBeInTheDocument()
    expect(screen.getByText('任务存在未完成的依赖')).toBeInTheDocument()
    expect(screen.getByText(/conflict · req-42/)).toBeInTheDocument()
  })

  it('invokes onRetry when the retry button is pressed', async () => {
    const onRetry = vi.fn()
    render(<ErrorState error={new Error('boom')} onRetry={onRetry} />)

    await userEvent.click(screen.getByRole('button', { name: /重试/ }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('omits the retry button when no handler is given', () => {
    render(<ErrorState error={new Error('boom')} />)
    expect(screen.queryByRole('button', { name: /重试/ })).not.toBeInTheDocument()
  })
})

describe('ProgressBar', () => {
  it('exposes the percentage through ARIA', () => {
    render(<ProgressBar value={45} max={100} showLabel />)
    const bar = screen.getByRole('progressbar')
    expect(bar).toHaveAttribute('aria-valuenow', '45')
    expect(bar).toHaveAttribute('aria-valuemax', '100')
    expect(screen.getByText('45%')).toBeInTheDocument()
  })

  it('clamps values above the maximum', () => {
    render(<ProgressBar value={250} max={100} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
  })

  it('does not divide by zero when max is 0', () => {
    render(<ProgressBar value={10} max={0} />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
  })
})

describe('Toaster', () => {
  it('renders queued toasts and can dismiss one', async () => {
    useToastStore.setState({ toasts: [] })
    useToastStore.getState().push({ kind: 'error', title: '保存失败', description: 'code=conflict' })

    render(<Toaster />)

    expect(screen.getByText('保存失败')).toBeInTheDocument()
    expect(screen.getByText('code=conflict')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '关闭通知' }))
    expect(useToastStore.getState().toasts).toHaveLength(0)
  })

  it('renders nothing when there are no toasts', () => {
    useToastStore.setState({ toasts: [] })
    const { container } = render(<Toaster />)
    expect(container).toBeEmptyDOMElement()
  })
})
