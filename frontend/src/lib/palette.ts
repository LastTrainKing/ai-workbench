/**
 * Fuzzy matching used by the command palette.
 *
 * Scoring is a small subsequence matcher: consecutive characters and matches at
 * word boundaries score higher, unmatched leading characters are penalised. It is
 * intentionally dependency-free and pure so it can be unit-tested.
 */

export interface PaletteItem {
  id: string
  label: string
  hint?: string
  group: string
  keywords?: string[]
  /** Route to navigate to when the item is executed. */
  to?: string
  /** Named side-effect action handled by the palette. */
  action?: string
  icon?: string
}

export const NO_MATCH = -1

export function fuzzyScore(query: string, text: string): number {
  const needle = query.trim().toLowerCase()
  const haystack = text.toLowerCase()
  if (!needle) return 0
  if (haystack === needle) return 1000
  if (haystack.startsWith(needle)) return 800 - (haystack.length - needle.length)

  let score = 0
  let haystackIndex = 0
  let previousMatchIndex = -1
  let matched = 0

  for (const char of needle) {
    const found = haystack.indexOf(char, haystackIndex)
    if (found === -1) return NO_MATCH
    matched += 1
    score += 10
    if (found === previousMatchIndex + 1) score += 15 // consecutive run
    if (found === 0 || /[\s\-_/.]/.test(haystack[found - 1] ?? '')) score += 12 // word start
    previousMatchIndex = found
    haystackIndex = found + 1
  }

  if (matched !== needle.length) return NO_MATCH
  // Prefer tighter matches: penalise how spread out the hit is.
  const spread = previousMatchIndex + 1 - (haystackIndex - matched)
  score -= Math.max(0, spread - needle.length) * 0.5
  score -= Math.min(haystack.length, 60) * 0.05
  return score
}

/** Best score across the item's label, hint and keywords. */
export function scorePaletteItem(query: string, item: PaletteItem): number {
  const candidates = [item.label, item.hint ?? '', ...(item.keywords ?? [])]
  let best = NO_MATCH
  for (const candidate of candidates) {
    if (!candidate) continue
    const score = fuzzyScore(query, candidate)
    if (score > best) best = score
  }
  return best
}

/**
 * Filter + rank items for a query. An empty query returns the items unchanged
 * (so the palette shows a useful default list) capped at `limit`.
 */
export function filterPaletteItems(query: string, items: PaletteItem[], limit = 40): PaletteItem[] {
  const trimmed = query.trim()
  if (!trimmed) return items.slice(0, limit)

  return items
    .map((item) => ({ item, score: scorePaletteItem(trimmed, item) }))
    .filter((entry) => entry.score !== NO_MATCH)
    .sort((a, b) => b.score - a.score || a.item.label.localeCompare(b.item.label))
    .slice(0, limit)
    .map((entry) => entry.item)
}

/** Group ranked items while preserving the ranking order within each group. */
export function groupPaletteItems(items: PaletteItem[]): { group: string; items: PaletteItem[] }[] {
  const groups = new Map<string, PaletteItem[]>()
  for (const item of items) {
    const bucket = groups.get(item.group)
    if (bucket) bucket.push(item)
    else groups.set(item.group, [item])
  }
  return [...groups.entries()].map(([group, groupItems]) => ({ group, items: groupItems }))
}
