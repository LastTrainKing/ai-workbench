import { describe, expect, it } from 'vitest'
import {
  cn,
  colorFromString,
  compactNumber,
  documentStatusTone,
  formatBytes,
  formatMinutes,
  formatMs,
  formatNumber,
  initials,
  isDueToday,
  isOverdue,
  percent,
  priorityTone,
  runStatusTone,
  runTypeLabel,
  relativeTime,
  taskStatusLabel,
  taskStatusTone,
  truncate,
} from './utils'

describe('cn', () => {
  it('merges conflicting tailwind classes with the last one winning', () => {
    expect(cn('px-2 py-1', 'px-4')).toBe('py-1 px-4')
  })

  it('drops falsy values', () => {
    expect(cn('a', false, undefined, null, 'b')).toBe('a b')
  })
})

describe('formatBytes', () => {
  it('formats zero and sub-kilobyte values', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(500)).toBe('500 B')
  })

  it('scales to KB / MB / GB', () => {
    expect(formatBytes(1024)).toBe('1.0 KB')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB')
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe('3.0 GB')
  })

  it('handles nullish input', () => {
    expect(formatBytes(null)).toBe('—')
    expect(formatBytes(undefined)).toBe('—')
  })
})

describe('formatMs', () => {
  it('formats milliseconds, seconds and minutes', () => {
    expect(formatMs(500)).toBe('500 ms')
    expect(formatMs(1500)).toBe('1.50 s')
    expect(formatMs(15000)).toBe('15.0 s')
    expect(formatMs(90000)).toBe('1m 30s')
  })
})

describe('compactNumber / formatNumber', () => {
  it('compacts large numbers', () => {
    expect(compactNumber(999)).toBe('999')
    expect(compactNumber(12000)).toBe('12k')
    expect(compactNumber(1500000)).toBe('1.5M')
  })

  it('groups digits for full formatting', () => {
    expect(formatNumber(1234567)).toBe('1,234,567')
    expect(formatNumber(null)).toBe('—')
  })
})

describe('formatMinutes', () => {
  it('formats minutes and hours', () => {
    expect(formatMinutes(45)).toBe('45 分钟')
    expect(formatMinutes(60)).toBe('1 小时')
    expect(formatMinutes(95)).toBe('1 小时 35 分')
    expect(formatMinutes(0)).toBe('—')
  })
})

describe('relativeTime', () => {
  it('describes recent timestamps', () => {
    const twoMinutesAgo = new Date(Date.now() - 2 * 60_000).toISOString()
    expect(relativeTime(twoMinutesAgo)).toBe('2 分钟前')
  })

  it('describes very recent timestamps as 刚刚', () => {
    expect(relativeTime(new Date(Date.now() - 5_000).toISOString())).toBe('刚刚')
  })

  it('falls back to an absolute date for null input', () => {
    expect(relativeTime(null)).toBe('—')
  })
})

describe('date helpers', () => {
  it('detects due-today', () => {
    expect(isDueToday(new Date().toISOString())).toBe(true)
    expect(isDueToday(null)).toBe(false)
  })

  it('detects overdue but not today', () => {
    const yesterday = new Date(Date.now() - 48 * 3_600_000).toISOString()
    expect(isOverdue(yesterday)).toBe(true)
    expect(isOverdue(new Date().toISOString())).toBe(false)
  })
})

describe('misc formatters', () => {
  it('computes percentages safely', () => {
    expect(percent(1, 4)).toBe(25)
    expect(percent(5, 0)).toBe(0)
  })

  it('derives initials for latin and CJK names', () => {
    expect(initials('Demo User')).toBe('DU')
    expect(initials('张三')).toBe('张三')
    expect(initials('')).toBe('?')
  })

  it('truncates with an ellipsis', () => {
    expect(truncate('abcdefghij', 5)).toBe('abcd…')
    expect(truncate('abc', 5)).toBe('abc')
  })

  it('maps enums to Chinese labels', () => {
    expect(runTypeLabel('RAG_ANSWER')).toBe('RAG 问答')
    expect(runTypeLabel('UNKNOWN')).toBe('UNKNOWN')
    expect(taskStatusLabel('IN_PROGRESS')).toBe('进行中')
  })
})

describe('tone mapping', () => {
  it('maps statuses to semantic tones', () => {
    expect(taskStatusTone('DONE')).toBe('success')
    expect(taskStatusTone('BLOCKED')).toBe('danger')
    expect(documentStatusTone('PROCESSING')).toBe('info')
    expect(documentStatusTone('FAILED')).toBe('danger')
    expect(runStatusTone('SUCCEEDED')).toBe('success')
    expect(priorityTone('URGENT')).toBe('danger')
  })

  it('falls back to neutral for unknown values', () => {
    expect(taskStatusTone('WHATEVER')).toBe('neutral')
  })
})

describe('colorFromString', () => {
  it('is deterministic and always returns a hex colour', () => {
    const first = colorFromString('demo')
    const second = colorFromString('demo')
    expect(first).toBe(second)
    expect(first).toMatch(/^#[0-9a-f]{6}$/i)
  })
})
