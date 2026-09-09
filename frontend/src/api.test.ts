import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, consumeLaunchToken, establishSession, onUnauthorized, request, resetSession } from './api'

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => { resetSession(); onUnauthorized() })

describe('local API authentication', () => {
  it('logs in without CSRF, then attaches CSRF and same-origin credentials to mutations', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ ok: true })).mockResolvedValueOnce(json({ csrf_token: 'csrf-test' })).mockResolvedValueOnce(json({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    await establishSession('launch-test')
    await request('/pause', 'POST', {})
    expect(fetchMock.mock.calls[0][0]).toBe('/api/login')
    expect(fetchMock.mock.calls[0][1].headers['X-CSRF-Token']).toBeUndefined()
    expect(fetchMock.mock.calls[0][1].body).toBe('{"token":"launch-test"}')
    expect(fetchMock.mock.calls[2][1]).toMatchObject({ credentials: 'same-origin', headers: { 'X-CSRF-Token': 'csrf-test' } })
  })
  it('refuses mutations without a session and does not fetch', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await expect(request('/pause', 'POST', {})).rejects.toBeInstanceOf(ApiError)
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it('expires the session once a request gets 401, without retrying', async () => {
    const expired = vi.fn()
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ csrf_token: 'csrf-test' })).mockResolvedValueOnce(json({ detail: 'Expired' }, 401))
    vi.stubGlobal('fetch', fetchMock)
    onUnauthorized(expired)
    await establishSession()
    await expect(request('/state')).rejects.toThrow('Expired')
    expect(expired).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await expect(request('/pause', 'POST', {})).rejects.toThrow(/expired/)
  })
  it('removes launch tokens from the URL immediately, preserving other URL state', () => {
    window.history.replaceState(null, '', '/?view=home#token=private-token&other=value')
    expect(consumeLaunchToken()).toBe('private-token')
    expect(window.location.hash).toBe('#other=value')
    expect(window.location.search).toBe('?view=home')
    expect(consumeLaunchToken()).toBeUndefined()
  })
  it('surfaces object error details and supports empty success responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({ detail: { reason: 'blocked' } }, 422)).mockResolvedValueOnce(new Response(null, { status: 204 })))
    await expect(request('/state')).rejects.toThrow('{"reason":"blocked"}')
    await expect(request('/team')).resolves.toBeNull()
  })
})
