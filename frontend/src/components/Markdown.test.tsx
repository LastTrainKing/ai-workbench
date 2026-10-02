import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Markdown } from './Markdown'

describe('Markdown', () => {
  it('renders headings, lists and GFM tables', () => {
    const source = ['## 标题', '', '- 一', '- 二', '', '| A | B |', '| --- | --- |', '| 1 | 2 |'].join('\n')
    render(<Markdown>{source}</Markdown>)

    expect(screen.getByRole('heading', { level: 2, name: '标题' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('leaves [n] markers as literal text when citations are disabled', () => {
    render(<Markdown>{'结论如下[1]。'}</Markdown>)
    expect(screen.getByText(/结论如下\[1\]。/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('turns [n] markers into clickable citation chips', async () => {
    const onCitationClick = vi.fn()
    render(
      <Markdown citations onCitationClick={onCitationClick}>
        {'向量检索更擅长语义召回[1]，关键词召回更精确[2]。'}
      </Markdown>,
    )

    const chips = screen.getAllByRole('button')
    expect(chips).toHaveLength(2)
    expect(chips[0]).toHaveTextContent('1')
    expect(chips[1]).toHaveTextContent('2')

    await userEvent.click(chips[1])
    expect(onCitationClick).toHaveBeenCalledWith(2)
  })

  it('marks the active citation chip', () => {
    render(
      <Markdown citations activeCitation={2} onCitationClick={() => undefined}>
        {'引用[1]与[2]'}
      </Markdown>,
    )
    const chips = screen.getAllByRole('button')
    expect(chips[0].className).toContain('bg-accent-soft')
    expect(chips[1].className).toContain('bg-accent text-accent-fg')
  })

  it('keeps markers inside fenced code blocks as code', () => {
    const source = ['```python', 'items[0]', '```'].join('\n')
    render(
      <Markdown citations onCitationClick={() => undefined}>
        {source}
      </Markdown>,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText(/items\[0\]/)).toBeInTheDocument()
  })
})
