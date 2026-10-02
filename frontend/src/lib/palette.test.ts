import { describe, expect, it } from 'vitest'
import { NO_MATCH, filterPaletteItems, fuzzyScore, groupPaletteItems, scorePaletteItem, type PaletteItem } from './palette'

const items: PaletteItem[] = [
  { id: 'd1', label: 'RAG 检索笔记', group: '文档', hint: 'COMPLETED' },
  { id: 'd2', label: 'distributed-systems.pdf', group: '文档', hint: 'PROCESSING' },
  { id: 't1', label: '完成中期答辩材料', group: '任务', keywords: ['答辩', 'defense'] },
  { id: 'p1', label: '毕业设计', group: '项目' },
  { id: 'a1', label: '上传文档', group: 'AI 操作', action: 'navigate', to: '/documents?upload=1' },
]

describe('fuzzyScore', () => {
  it('scores an exact match highest', () => {
    expect(fuzzyScore('毕业设计', '毕业设计')).toBe(1000)
  })

  it('scores a prefix match above a scattered match', () => {
    const prefix = fuzzyScore('dist', 'distributed-systems.pdf')
    const scattered = fuzzyScore('dsp', 'distributed-systems.pdf')
    expect(prefix).toBeGreaterThan(scattered)
    expect(scattered).toBeGreaterThan(NO_MATCH)
  })

  it('returns NO_MATCH when the characters are absent', () => {
    expect(fuzzyScore('zzz', '毕业设计')).toBe(NO_MATCH)
  })

  it('returns 0 for an empty query', () => {
    expect(fuzzyScore('', 'anything')).toBe(0)
  })
})

describe('scorePaletteItem', () => {
  it('considers the hint and keywords', () => {
    expect(scorePaletteItem('PROCESSING', items[1])).toBeGreaterThan(NO_MATCH)
    expect(scorePaletteItem('defense', items[2])).toBeGreaterThan(NO_MATCH)
    expect(scorePaletteItem('答辩', items[2])).toBeGreaterThan(NO_MATCH)
  })

  it('returns NO_MATCH when nothing matches', () => {
    expect(scorePaletteItem('qqqq', items[0])).toBe(NO_MATCH)
  })
})

describe('filterPaletteItems', () => {
  it('returns the head of the list for an empty query', () => {
    expect(filterPaletteItems('', items, 3).map((item) => item.id)).toEqual(['d1', 'd2', 't1'])
  })

  it('ranks the strongest match first', () => {
    const results = filterPaletteItems('毕业设计', items)
    expect(results[0].id).toBe('p1')
  })

  it('drops non-matching items entirely', () => {
    const results = filterPaletteItems('distributed', items)
    expect(results.map((item) => item.id)).toEqual(['d2'])
  })

  it('respects the limit', () => {
    expect(filterPaletteItems('', items, 2)).toHaveLength(2)
  })

  it('finds Chinese labels by subsequence', () => {
    const results = filterPaletteItems('中期答辩', items)
    expect(results.map((item) => item.id)).toContain('t1')
  })
})

describe('groupPaletteItems', () => {
  it('groups by the group field preserving rank order', () => {
    const groups = groupPaletteItems(filterPaletteItems('', items))
    expect(groups.map((group) => group.group)).toEqual(['文档', '任务', '项目', 'AI 操作'])
    expect(groups[0].items.map((item) => item.id)).toEqual(['d1', 'd2'])
  })

  it('returns an empty array for no items', () => {
    expect(groupPaletteItems([])).toEqual([])
  })
})
