import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmHost, Modal, confirm, useConfirmStore } from './overlay'

describe('Modal', () => {
  it('renders nothing while closed', () => {
    const { container } = render(
      <Modal open={false} onClose={() => undefined} title="标题">
        body
      </Modal>,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('renders the title, description, body and footer when open', () => {
    render(
      <Modal
        open
        onClose={() => undefined}
        title="上传文档"
        description="支持 md/txt/pdf"
        footer={<button type="button">完成</button>}
      >
        <p>拖拽到这里</p>
      </Modal>,
    )

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('上传文档')).toBeInTheDocument()
    expect(screen.getByText('支持 md/txt/pdf')).toBeInTheDocument()
    expect(screen.getByText('拖拽到这里')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '完成' })).toBeInTheDocument()
  })

  it('closes on Escape and on the close button', async () => {
    const onClose = vi.fn()
    render(
      <Modal open onClose={onClose} title="标题">
        body
      </Modal>,
    )

    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)

    await userEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})

describe('confirm()', () => {
  it('resolves true when the confirm action is pressed', async () => {
    render(<ConfirmHost />)

    let pending!: Promise<boolean>
    act(() => {
      pending = confirm({
        title: '删除知识库',
        description: '该操作不可撤销',
        detail: '分布式系统课程资料',
        confirmLabel: '删除',
        tone: 'danger',
      })
    })

    expect(screen.getByText('删除知识库')).toBeInTheDocument()
    expect(screen.getByText('分布式系统课程资料')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '删除' }))
    await expect(pending).resolves.toBe(true)
    expect(useConfirmStore.getState().options).toBeNull()
  })

  it('resolves false when cancelled', async () => {
    render(<ConfirmHost />)

    let pending!: Promise<boolean>
    act(() => {
      pending = confirm({ title: '删除任务' })
    })

    await userEvent.click(screen.getByRole('button', { name: '取消' }))
    await expect(pending).resolves.toBe(false)
  })
})
