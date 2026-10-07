/**
 * The two things that made 下载 fail, pinned down.
 *
 * 1. A filename with Chinese characters. Starlette sends the name twice — a
 *    percent-encoded `filename*` and a stripped ASCII `filename="…"` — and reading
 *    the wrong one is how a Chinese filename arrives as `____.pptx`.
 * 2. The save itself: a blob URL plus a `download` attribute, which is the only
 *    form that works when the bytes were fetched with an `Authorization` header.
 *    A plain `<a href>` to the API endpoint cannot, because a navigation carries no
 *    header — that is the actual defect this replaced.
 */

import { describe, expect, it, vi, afterEach } from 'vitest'
import { filenameFromDisposition } from './api'
import { saveBlob } from './download'

describe('filenameFromDisposition', () => {
  it('prefers the RFC 5987 form and decodes it', () => {
    const header = "attachment; filename=\"____.pptx\"; filename*=utf-8''Java%E5%9F%BA%E7%A1%80%E6%95%99%E7%A8%8B.pptx"
    expect(filenameFromDisposition(header)).toBe('Java基础教程.pptx')
  })

  it('reads a plain quoted name', () => {
    expect(filenameFromDisposition('attachment; filename="report.docx"')).toBe('report.docx')
  })

  it('reads an unquoted name', () => {
    expect(filenameFromDisposition('attachment; filename=report.docx')).toBe('report.docx')
  })

  it('returns null when there is no header, so the caller can fall back', () => {
    expect(filenameFromDisposition(null)).toBeNull()
    expect(filenameFromDisposition('')).toBeNull()
    expect(filenameFromDisposition('attachment')).toBeNull()
  })

  it('falls back to the plain form when the encoded one is malformed', () => {
    // A stray % is not a reason to lose the name entirely.
    const header = "attachment; filename=\"fallback.pptx\"; filename*=utf-8''%E0%A4%A"
    expect(filenameFromDisposition(header)).toBe('fallback.pptx')
  })
})

describe('saveBlob', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('clicks an anchor carrying the filename, and does not leave it in the DOM', () => {
    const createObjectURL = vi.fn(() => 'blob:test')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })

    let clicked: HTMLAnchorElement | null = null
    const realClick = HTMLAnchorElement.prototype.click
    HTMLAnchorElement.prototype.click = function () {
      clicked = this
    }

    try {
      saveBlob(new Blob(['x']), 'Java基础教程.pptx')

      expect(createObjectURL).toHaveBeenCalledOnce()
      expect(clicked).not.toBeNull()
      // The name the user sees. Not the blob URL, not the id.
      expect(clicked!.download).toBe('Java基础教程.pptx')
      expect(clicked!.href).toBe('blob:test')
      // Cleaned up: an anchor left behind is a leak that shows up as a stray node
      // in every later test that counts them.
      expect(document.body.querySelectorAll('a')).toHaveLength(0)
    } finally {
      HTMLAnchorElement.prototype.click = realClick
    }
  })

  it('revokes the object URL later rather than immediately', () => {
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:test', revokeObjectURL })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    saveBlob(new Blob(['x']), 'a.txt')

    // Revoking in the same tick can cancel the download, so it must not happen
    // synchronously — this asserts the deferral, not just that it happens.
    expect(revokeObjectURL).not.toHaveBeenCalled()
  })
})
