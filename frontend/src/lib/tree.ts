/**
 * Task-tree helpers.
 *
 * `/tasks/tree` already returns a nested structure, but the flat `/tasks` listing
 * (with filters applied server-side) needs to be re-assembled on the client so the
 * tree view can respect the active filters. These functions are pure and tested.
 */
import type { Task, TaskTreeNode } from './types'

export interface FlatTask extends Task {
  children?: FlatTask[]
}

function sortSiblings<T extends Task>(nodes: T[]): T[] {
  return [...nodes].sort((a, b) => {
    if (a.is_pinned !== b.is_pinned) return a.is_pinned ? -1 : 1
    const aCreated = a.created_at ? Date.parse(a.created_at) : 0
    const bCreated = b.created_at ? Date.parse(b.created_at) : 0
    if (aCreated !== bCreated) return aCreated - bCreated
    // Plain code-point comparison rather than localeCompare: sibling order must be
    // identical regardless of the machine's locale settings.
    if (a.title === b.title) return 0
    return a.title < b.title ? -1 : 1
  })
}

/**
 * Assemble a forest from a flat task list using `parent_id`.
 * Tasks whose parent is missing from the list are promoted to roots so nothing is
 * silently dropped when a filter hides the parent.
 */
export function buildTaskTree(tasks: Task[]): TaskTreeNode[] {
  const byId = new Map<string, TaskTreeNode>()
  for (const task of tasks) {
    byId.set(task.id, { ...task, children: [] })
  }

  const roots: TaskTreeNode[] = []
  for (const task of tasks) {
    const node = byId.get(task.id)
    if (!node) continue
    const parent = task.parent_id ? byId.get(task.parent_id) : undefined
    if (parent && parent.id !== node.id) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }

  const sortRecursive = (nodes: TaskTreeNode[]): TaskTreeNode[] => {
    const sorted = sortSiblings(nodes)
    for (const node of sorted) node.children = sortRecursive(node.children)
    return sorted
  }

  return sortRecursive(roots)
}

export interface FlatTreeRow {
  node: TaskTreeNode
  depth: number
}

/** Depth-first flattening used by the tree view renderer. */
export function flattenTaskTree(
  nodes: TaskTreeNode[],
  collapsed: ReadonlySet<string> = new Set(),
  depth = 0,
): FlatTreeRow[] {
  const rows: FlatTreeRow[] = []
  for (const node of sortSiblings(nodes)) {
    rows.push({ node, depth })
    if (node.children.length > 0 && !collapsed.has(node.id)) {
      rows.push(...flattenTaskTree(node.children, collapsed, depth + 1))
    }
  }
  return rows
}

export function countTreeNodes(nodes: TaskTreeNode[]): number {
  let total = 0
  for (const node of nodes) {
    total += 1 + countTreeNodes(node.children)
  }
  return total
}

export interface TreeStats {
  total: number
  done: number
  open: number
  blocked: number
  inProgress: number
}

export function treeStats(nodes: TaskTreeNode[]): TreeStats {
  const stats: TreeStats = { total: 0, done: 0, open: 0, blocked: 0, inProgress: 0 }
  const walk = (list: TaskTreeNode[]) => {
    for (const node of list) {
      stats.total += 1
      if (node.status === 'DONE') stats.done += 1
      else if (node.status === 'CANCELLED') {
        /* not counted as open work */
      } else stats.open += 1
      if (node.status === 'BLOCKED') stats.blocked += 1
      if (node.status === 'IN_PROGRESS') stats.inProgress += 1
      walk(node.children)
    }
  }
  walk(nodes)
  return stats
}

/**
 * Keep only nodes matching `predicate`, but retain the ancestors of any match so
 * the hierarchy stays navigable. Returns a new forest.
 */
export function filterTaskTree(
  nodes: TaskTreeNode[],
  predicate: (node: TaskTreeNode) => boolean,
): TaskTreeNode[] {
  const walk = (list: TaskTreeNode[]): TaskTreeNode[] => {
    const result: TaskTreeNode[] = []
    for (const node of list) {
      const children = walk(node.children)
      if (predicate(node) || children.length > 0) {
        result.push({ ...node, children })
      }
    }
    return result
  }
  return walk(nodes)
}

/** Ancestor chain (root → parent) for a given task id, excluding the task itself. */
export function ancestorPath(nodes: TaskTreeNode[], taskId: string): TaskTreeNode[] {
  const path: TaskTreeNode[] = []
  const walk = (list: TaskTreeNode[], trail: TaskTreeNode[]): boolean => {
    for (const node of list) {
      if (node.id === taskId) {
        path.push(...trail)
        return true
      }
      if (walk(node.children, [...trail, node])) return true
    }
    return false
  }
  walk(nodes, [])
  return path
}
