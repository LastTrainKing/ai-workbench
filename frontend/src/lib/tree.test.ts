import { describe, expect, it } from 'vitest'
import {
  ancestorPath,
  buildTaskTree,
  countTreeNodes,
  filterTaskTree,
  flattenTaskTree,
  treeStats,
} from './tree'
import type { Task } from './types'

function task(partial: Partial<Task> & { id: string; title: string }): Task {
  return {
    project_id: null,
    parent_id: null,
    description: '',
    completion_criteria: '',
    status: 'TODO',
    priority: 'MEDIUM',
    source: 'MANUAL',
    ai_suggestion: '',
    due_date: null,
    estimated_minutes: 0,
    actual_minutes: 0,
    depth: 0,
    is_pinned: false,
    completed_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...partial,
  }
}

const flat: Task[] = [
  task({ id: 'root', title: '毕业设计' }),
  task({ id: 'phase-1', title: '阶段一', parent_id: 'root' }),
  task({ id: 'phase-2', title: '阶段二', parent_id: 'root' }),
  task({ id: 't-1', title: '任务 A', parent_id: 'phase-1' }),
  task({ id: 't-2', title: '任务 B', parent_id: 'phase-1' }),
]

describe('buildTaskTree', () => {
  it('nests children under their parents', () => {
    const tree = buildTaskTree(flat)
    expect(tree).toHaveLength(1)
    expect(tree[0].id).toBe('root')
    expect(tree[0].children.map((node) => node.id)).toEqual(['phase-1', 'phase-2'])
    expect(tree[0].children[0].children.map((node) => node.id)).toEqual(['t-1', 't-2'])
  })

  it('promotes orphans to roots instead of dropping them', () => {
    const orphan = task({ id: 'orphan', title: '孤儿任务', parent_id: 'missing' })
    const tree = buildTaskTree([...flat, orphan])
    expect(tree.map((node) => node.id).sort()).toEqual(['orphan', 'root'])
  })

  it('treats a self-referencing parent as a root', () => {
    const tree = buildTaskTree([task({ id: 'loop', title: '自引用', parent_id: 'loop' })])
    expect(tree).toHaveLength(1)
    expect(tree[0].children).toHaveLength(0)
  })

  it('sorts pinned siblings first', () => {
    const tree = buildTaskTree([
      task({ id: 'a', title: 'A' }),
      task({ id: 'b', title: 'B', is_pinned: true }),
    ])
    expect(tree.map((node) => node.id)).toEqual(['b', 'a'])
  })
})

describe('flattenTaskTree', () => {
  it('emits depth-first rows with depth', () => {
    const tree = buildTaskTree(flat)
    const rows = flattenTaskTree(tree)
    expect(rows.map((row) => [row.node.id, row.depth])).toEqual([
      ['root', 0],
      ['phase-1', 1],
      ['t-1', 2],
      ['t-2', 2],
      ['phase-2', 1],
    ])
  })

  it('omits the descendants of collapsed nodes', () => {
    const tree = buildTaskTree(flat)
    const rows = flattenTaskTree(tree, new Set(['phase-1']))
    expect(rows.map((row) => row.node.id)).toEqual(['root', 'phase-1', 'phase-2'])
  })
})

describe('countTreeNodes', () => {
  it('counts every node recursively', () => {
    expect(countTreeNodes(buildTaskTree(flat))).toBe(5)
  })
})

describe('treeStats', () => {
  it('counts done, open, blocked and in-progress nodes', () => {
    const tree = buildTaskTree([
      task({ id: 'a', title: 'A', status: 'DONE' }),
      task({ id: 'b', title: 'B', status: 'IN_PROGRESS', parent_id: 'a' }),
      task({ id: 'c', title: 'C', status: 'BLOCKED', parent_id: 'a' }),
      task({ id: 'd', title: 'D', status: 'CANCELLED' }),
    ])
    expect(treeStats(tree)).toEqual({
      total: 4,
      done: 1,
      open: 2,
      blocked: 1,
      inProgress: 1,
    })
  })
})

describe('filterTaskTree', () => {
  it('keeps ancestors of matching descendants', () => {
    const tree = buildTaskTree(flat)
    const filtered = filterTaskTree(tree, (node) => node.title === '任务 B')
    expect(filtered).toHaveLength(1)
    expect(filtered[0].id).toBe('root')
    expect(filtered[0].children).toHaveLength(1)
    expect(filtered[0].children[0].id).toBe('phase-1')
    expect(filtered[0].children[0].children.map((node) => node.id)).toEqual(['t-2'])
  })

  it('returns an empty forest when nothing matches', () => {
    expect(filterTaskTree(buildTaskTree(flat), () => false)).toEqual([])
  })
})

describe('ancestorPath', () => {
  it('returns the chain from root to parent', () => {
    const tree = buildTaskTree(flat)
    expect(ancestorPath(tree, 't-2').map((node) => node.id)).toEqual(['root', 'phase-1'])
  })

  it('returns an empty array for a root node or unknown id', () => {
    const tree = buildTaskTree(flat)
    expect(ancestorPath(tree, 'root')).toEqual([])
    expect(ancestorPath(tree, 'nope')).toEqual([])
  })
})
