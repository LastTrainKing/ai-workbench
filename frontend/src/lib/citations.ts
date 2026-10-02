/**
 * Citation + keyword-highlight parsing for RAG answers and search results.
 *
 * The backend emits `[n]` markers inline in `answer` (see `docs/api.md` → RAG chat),
 * where `n` is `citations[].index`. These helpers turn that string into renderable
 * tokens and keep the mapping logic unit-testable.
 */

export interface CitationMarker {
  /** The literal text, e.g. "[1]" or "[1, 2]". */
  raw: string
  /** One-based citation indices, in the order they appear. */
  indices: number[]
}

export type AnswerToken =
  | { kind: 'text'; value: string }
  | { kind: 'citation'; value: string; indices: number[] }

// Matches "[1]", "[12]", "[1,2]" and "[1, 2]" — the shapes the prompt allows.
const CITATION_PATTERN = /\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]/g

/** Extract every `[n]` marker with its parsed indices. */
export function extractCitations(text: string): CitationMarker[] {
  if (!text) return []
  const markers: CitationMarker[] = []
  for (const match of text.matchAll(CITATION_PATTERN)) {
    const indices = match[1]
      .split(',')
      .map((part) => Number.parseInt(part.trim(), 10))
      .filter((n) => Number.isFinite(n) && n > 0)
    if (indices.length > 0) markers.push({ raw: match[0], indices })
  }
  return markers
}

/** Unique, ascending citation indices referenced anywhere in the answer. */
export function referencedCitationIndices(text: string): number[] {
  const seen = new Set<number>()
  for (const marker of extractCitations(text)) {
    for (const index of marker.indices) seen.add(index)
  }
  return [...seen].sort((a, b) => a - b)
}

/**
 * Split an answer into plain-text runs and citation runs so the UI can render
 * the markers as clickable chips.
 */
export function parseAnswer(text: string): AnswerToken[] {
  if (!text) return []
  const tokens: AnswerToken[] = []
  let cursor = 0
  CITATION_PATTERN.lastIndex = 0
  for (const match of text.matchAll(CITATION_PATTERN)) {
    const start = match.index ?? 0
    const indices = match[1]
      .split(',')
      .map((part) => Number.parseInt(part.trim(), 10))
      .filter((n) => Number.isFinite(n) && n > 0)
    if (indices.length === 0) continue
    if (start > cursor) tokens.push({ kind: 'text', value: text.slice(cursor, start) })
    tokens.push({ kind: 'citation', value: match[0], indices })
    cursor = start + match[0].length
  }
  if (cursor < text.length) tokens.push({ kind: 'text', value: text.slice(cursor) })
  return tokens
}

/* ------------------------------------------------------------- highlighting */

export interface HighlightSegment {
  text: string
  match: boolean
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Split `text` into segments flagged as matching any of `terms`.
 * Latin terms match case-insensitively on word boundaries; CJK terms match literally.
 */
export function highlightTerms(text: string, terms: string[]): HighlightSegment[] {
  if (!text) return []
  const cleaned = terms.map((t) => t.trim()).filter((t) => t.length > 1)
  if (cleaned.length === 0) return [{ text, match: false }]

  const pattern = cleaned
    .sort((a, b) => b.length - a.length)
    .map((term) => {
      const escaped = escapeRegExp(term)
      const isAscii = /^[\x20-\x7e]+$/.test(term)
      if (!isAscii) return escaped
      // Only anchor on word boundaries when the term itself starts/ends with a
      // word character, so terms like "(y)" still match.
      const start = /^\w/.test(term) ? '\\b' : ''
      const end = /\w$/.test(term) ? '\\b' : ''
      return `${start}${escaped}${end}`
    })
    .join('|')

  let regex: RegExp
  try {
    regex = new RegExp(`(${pattern})`, 'gi')
  } catch {
    return [{ text, match: false }]
  }

  const segments: HighlightSegment[] = []
  let cursor = 0
  for (const match of text.matchAll(regex)) {
    const start = match.index ?? 0
    if (start > cursor) segments.push({ text: text.slice(cursor, start), match: false })
    segments.push({ text: match[0], match: true })
    cursor = start + match[0].length
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), match: false })
  return segments.length > 0 ? segments : [{ text, match: false }]
}

/** Derive the terms used to highlight search hits: the words of the query. */
export function queryTerms(query: string): string[] {
  return query
    .split(/[\s,，、。；;：:!！?？"“”'’()（）[\]【】]+/)
    .map((term) => term.trim())
    .filter((term) => term.length > 1)
}

export const CITATION_HREF_PREFIX = '#citation-'

/**
 * Rewrite `[n]` markers into markdown links (`[n](#citation-n)`) so the answer can
 * still be parsed as regular Markdown — headings, lists and tables keep working —
 * while the renderer swaps the links for citation chips.
 *
 * Fenced code blocks are left untouched: a `[0]` inside a code sample is code.
 */
export function linkifyCitations(text: string): string {
  if (!text) return ''
  const segments = text.split(/(```[\s\S]*?```|`[^`\n]*`)/g)
  return segments
    .map((segment, index) => {
      // Odd indices are code spans / fenced blocks captured by the split.
      if (index % 2 === 1) return segment
      return segment.replace(CITATION_PATTERN, (_match, group: string) =>
        group
          .split(',')
          .map((part) => part.trim())
          .filter((part) => part.length > 0)
          .map((part) => `[${part}](${CITATION_HREF_PREFIX}${part})`)
          .join(''),
      )
    })
    .join('')
}

/** Parse the citation index out of a linkified href, or return null. */
export function citationIndexFromHref(href: string | undefined): number | null {
  if (!href || !href.startsWith(CITATION_HREF_PREFIX)) return null
  const value = Number.parseInt(href.slice(CITATION_HREF_PREFIX.length), 10)
  return Number.isFinite(value) && value > 0 ? value : null
}
