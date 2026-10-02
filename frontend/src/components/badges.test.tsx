import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  BoolBadge,
  DocumentStatusBadge,
  PriorityBadge,
  RunStatusBadge,
  SourceBadge,
  TagChip,
  TaskStatusBadge,
} from './badges'

describe('TaskStatusBadge', () => {
  it('renders the Chinese label for a status', () => {
    render(<TaskStatusBadge status="IN_PROGRESS" />)
    expect(screen.getByText('进行中')).toBeInTheDocument()
  })

  it('falls back to the raw value for unknown statuses', () => {
    render(<TaskStatusBadge status="WEIRD" />)
    expect(screen.getByText('WEIRD')).toBeInTheDocument()
  })
})

describe('DocumentStatusBadge', () => {
  it('shows progress while the document is processing', () => {
    render(<DocumentStatusBadge status="PROCESSING" progress={42} />)
    expect(screen.getByText(/处理中 42%/)).toBeInTheDocument()
  })

  it('does not show a percentage once completed', () => {
    render(<DocumentStatusBadge status="COMPLETED" progress={100} />)
    expect(screen.getByText('已完成')).toBeInTheDocument()
    expect(screen.queryByText(/100%/)).not.toBeInTheDocument()
  })

  it('labels queued documents as 排队中', () => {
    render(<DocumentStatusBadge status="PENDING" />)
    expect(screen.getByText(/排队中/)).toBeInTheDocument()
  })
})

describe('PriorityBadge', () => {
  it('renders the Chinese priority label', () => {
    render(<PriorityBadge priority="URGENT" />)
    expect(screen.getByText('紧急')).toBeInTheDocument()
  })
})

describe('SourceBadge', () => {
  it('translates planner and agent sources', () => {
    const { rerender } = render(<SourceBadge source="PLANNER" />)
    expect(screen.getByText('规划器')).toBeInTheDocument()

    rerender(<SourceBadge source="AGENT" />)
    expect(screen.getByText('Agent')).toBeInTheDocument()

    rerender(<SourceBadge source="MANUAL" />)
    expect(screen.getByText('手动')).toBeInTheDocument()
  })
})

describe('RunStatusBadge', () => {
  it('renders the raw run status', () => {
    render(<RunStatusBadge status="FAILED" />)
    expect(screen.getByText('FAILED')).toBeInTheDocument()
  })
})

describe('BoolBadge', () => {
  it('switches between the true and false labels', () => {
    const { rerender } = render(<BoolBadge value={true} trueLabel="已接地" falseLabel="未接地" />)
    expect(screen.getByText('已接地')).toBeInTheDocument()

    rerender(<BoolBadge value={false} trueLabel="已接地" falseLabel="未接地" />)
    expect(screen.getByText('未接地')).toBeInTheDocument()
  })
})

describe('TagChip', () => {
  it('renders the tag name and a colour dot', () => {
    const { container } = render(<TagChip name="rag" color="#6366f1" />)
    expect(screen.getByText('rag')).toBeInTheDocument()
    expect(container.querySelector('span[style]')).not.toBeNull()
  })
})
