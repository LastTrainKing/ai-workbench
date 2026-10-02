import { describe, expect, it } from 'vitest'
import {
  citationIndexFromHref,
  extractCitations,
  highlightTerms,
  linkifyCitations,
  parseAnswer,
  queryTerms,
  referencedCitationIndices,
} from './citations'

describe('parseAnswer', () => {
  it('splits plain text and citation markers', () => {
    const tokens = parseAnswer('RAG 使用向量检索[1]，并结合关键词[2]。')
    expect(tokens).toEqual([
      { kind: 'text', value: 'RAG 使用向量检索' },
      { kind: 'citation', value: '[1]', indices: [1] },
      { kind: 'text', value: '，并结合关键词' },
      { kind: 'citation', value: '[2]', indices: [2] },
      { kind: 'text', value: '。' },
    ])
  })

  it('handles adjacent markers and grouped indices', () => {
    const tokens = parseAnswer('结论[1][2]与[3, 4]一致')
    const citations = tokens.filter((token) => token.kind === 'citation')
    expect(citations).toHaveLength(3)
    expect(citations[2]).toEqual({ kind: 'citation', value: '[3, 4]', indices: [3, 4] })
  })

  it('returns a single text token when there are no markers', () => {
    expect(parseAnswer('没有引用')).toEqual([{ kind: 'text', value: '没有引用' }])
  })

  it('ignores zero and non-numeric brackets', () => {
    expect(parseAnswer('a[0]b')).toEqual([{ kind: 'text', value: 'a[0]b' }])
  })

  it('returns an empty array for empty input', () => {
    expect(parseAnswer('')).toEqual([])
  })
})

describe('extractCitations / referencedCitationIndices', () => {
  it('collects every marker', () => {
    const markers = extractCitations('x[2] y[1] z[2]')
    expect(markers.map((marker) => marker.indices)).toEqual([[2], [1], [2]])
  })

  it('deduplicates and sorts referenced indices', () => {
    expect(referencedCitationIndices('a[3] b[1] c[3] d[2]')).toEqual([1, 2, 3])
  })
})

describe('linkifyCitations', () => {
  it('rewrites markers into markdown links', () => {
    expect(linkifyCitations('答案[1]与[2]')).toBe(
      '答案[1](#citation-1)与[2](#citation-2)',
    )
  })

  it('leaves fenced code blocks untouched', () => {
    const source = 'text[1]\n```\narr[0]\n```\nmore[2]'
    const output = linkifyCitations(source)
    expect(output).toContain('text[1](#citation-1)')
    expect(output).toContain('arr[0]')
    expect(output).not.toContain('arr[0](#citation-0)')
    expect(output).toContain('more[2](#citation-2)')
  })

  it('leaves inline code untouched', () => {
    const output = linkifyCitations('use `list[0]` here[1]')
    expect(output).toContain('`list[0]`')
    expect(output).toContain('here[1](#citation-1)')
  })
})

describe('citationIndexFromHref', () => {
  it('parses a citation href', () => {
    expect(citationIndexFromHref('#citation-7')).toBe(7)
  })

  it('rejects non-citation and malformed hrefs', () => {
    expect(citationIndexFromHref('https://example.com')).toBeNull()
    expect(citationIndexFromHref(undefined)).toBeNull()
    expect(citationIndexFromHref('#citation-0')).toBeNull()
    expect(citationIndexFromHref('#citation-abc')).toBeNull()
  })
})

describe('highlightTerms', () => {
  it('marks matches and keeps surrounding text', () => {
    const segments = highlightTerms('RAG 检索与向量检索', ['检索'])
    expect(segments.filter((segment) => segment.match).map((segment) => segment.text)).toEqual([
      '检索',
      '检索',
    ])
    expect(segments.map((segment) => segment.text).join('')).toBe('RAG 检索与向量检索')
  })

  it('matches latin terms case-insensitively', () => {
    const segments = highlightTerms('Hybrid retrieval', ['hybrid'])
    expect(segments[0]).toEqual({ text: 'Hybrid', match: true })
  })

  it('ignores single-character terms and empty term lists', () => {
    expect(highlightTerms('abc', ['a'])).toEqual([{ text: 'abc', match: false }])
    expect(highlightTerms('abc', [])).toEqual([{ text: 'abc', match: false }])
  })

  it('escapes regex metacharacters in terms', () => {
    expect(() => highlightTerms('a.b', ['a.b'])).not.toThrow()
    const segments = highlightTerms('x (y) z', ['(y)'])
    expect(segments.some((segment) => segment.match)).toBe(true)
  })
})

describe('queryTerms', () => {
  it('splits on punctuation and whitespace', () => {
    expect(queryTerms('RAG 检索、向量；hybrid')).toEqual(['RAG', '检索', '向量', 'hybrid'])
  })

  it('drops single-character fragments', () => {
    expect(queryTerms('a b cd')).toEqual(['cd'])
  })
})
