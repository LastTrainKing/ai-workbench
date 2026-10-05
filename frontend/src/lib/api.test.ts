import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  API_BASE_URL,
  getAccessToken,
  request,
  requestResult,
  setAccessToken,
  setUnauthorizedHandler,
  toApiError,
} from './api'

interface FakeResponseInit {
  status?: number
  body?: unknown
  headers?: Record<string, string>
  raw?: string
}

function makeResponse({ status = 200, body, headers = {}, raw }: FakeResponseInit): Response {
  const lowered = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]))
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (key: string) => lowered[key.toLowerCase()] ?? null },
    text: async () => (raw !== undefined ? raw : body === undefined ? '' : JSON.stringify(body)),
  } as unknown as Response
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  setAccessToken(null)
  setUnauthorizedHandler(null)
})

afterEach(() => {
  setAccessToken(null)
  setUnauthorizedHandler(null)
  vi.unstubAllGlobals()
})

describe('request envelope handling', () => {
  it('unwraps the { success, data } envelope', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        body: { success: true, data: { id: 'abc', title: '文档' }, message: 'ok' },
        headers: { 'X-Request-ID': 'req-1' },
      }),
    )

    const result = await requestResult<{ id: string; title: string }>('/documents/abc')

    expect(result.data).toEqual({ id: 'abc', title: '文档' })
    expect(result.message).toBe('ok')
    expect(result.requestId).toBe('req-1')
  })

  it('returns the raw payload when the response is not enveloped', async () => {
    fetchMock.mockResolvedValue(makeResponse({ body: { plain: true } }))
    await expect(request<{ plain: boolean }>('/health')).resolves.toEqual({ plain: true })
  })

  it('attaches the bearer token and skips empty query params', async () => {
    setAccessToken('token-123')
    fetchMock.mockResolvedValue(makeResponse({ body: { success: true, data: [] } }))

    await request('/documents', { query: { page: 2, q: '', project_id: undefined } })

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe(`${API_BASE_URL}/documents?page=2`)
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-123')
    expect(getAccessToken()).toBe('token-123')
  })

  it('serialises a JSON body with the right content type', async () => {
    fetchMock.mockResolvedValue(makeResponse({ body: { success: true, data: {} } }))
    await request('/tasks', { method: 'POST', body: { title: 't' } })
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ title: 't' }))
  })
})

describe('error envelope → ApiError', () => {
  it('carries code, message, details, requestId and status', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        status: 409,
        headers: { 'X-Request-ID': 'req-409' },
        body: {
          success: false,
          error: {
            code: 'conflict',
            message: '任务存在未完成的依赖',
            details: { blockers: ['a', 'b'] },
          },
          request_id: 'req-409',
        },
      }),
    )

    const error = await request('/tasks/x/complete', { method: 'POST' }).catch((e) => e)

    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe('conflict')
    expect(apiError.message).toBe('任务存在未完成的依赖')
    expect(apiError.details).toEqual({ blockers: ['a', 'b'] })
    expect(apiError.requestId).toBe('req-409')
    expect(apiError.status).toBe(409)
    expect(apiError.displayMessage).toContain('req-409')
    expect(apiError.isRetryable).toBe(false)
  })

  it('falls back to an HTTP-derived code when the body has no error object', async () => {
    fetchMock.mockResolvedValue(makeResponse({ status: 404, raw: 'not json' }))
    const error = (await request('/nope').catch((e) => e)) as ApiError
    expect(error.code).toBe('not_found')
    expect(error.status).toBe(404)
  })

  it('marks 5xx and rate limits as retryable', async () => {
    fetchMock.mockResolvedValue(
      makeResponse({
        status: 503,
        body: { success: false, error: { code: 'infrastructure_error', message: 'down' } },
      }),
    )
    const error = (await request('/x').catch((e) => e)) as ApiError
    expect(error.isRetryable).toBe(true)
  })

  it('turns a transport failure into a network_error ApiError', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    const error = (await request('/x').catch((e) => e)) as ApiError
    expect(error.code).toBe('network_error')
    expect(error.status).toBe(0)
    expect(error.message).toContain('后台服务')
  })
})

describe('401 handling', () => {
  it('clears the token and invokes the unauthorized handler', async () => {
    setAccessToken('expired')
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)

    fetchMock.mockResolvedValue(
      makeResponse({
        status: 401,
        body: { success: false, error: { code: 'authentication_error', message: 'token expired' } },
      }),
    )

    await request('/auth/me').catch(() => undefined)

    expect(getAccessToken()).toBeNull()
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('does not fire the handler for the login call itself', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    fetchMock.mockResolvedValue(
      makeResponse({
        status: 401,
        body: { success: false, error: { code: 'authentication_error', message: 'bad credentials' } },
      }),
    )

    await request('/auth/login', { method: 'POST', body: {}, skipAuthRedirect: true }).catch(
      () => undefined,
    )

    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})

describe('toApiError', () => {
  it('passes ApiError through untouched', () => {
    const original = new ApiError({ code: 'x', message: 'y', status: 400 })
    expect(toApiError(original)).toBe(original)
  })

  it('normalises unknown throwables', () => {
    expect(toApiError(new Error('boom')).message).toBe('boom')
    expect(toApiError('nope').code).toBe('unknown_error')
  })
})
