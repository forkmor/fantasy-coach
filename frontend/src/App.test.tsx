import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import type { AppState } from './types'

const state: AppState = {
  settings: { provider: 'grok', model: 'test-model', mode: 'dry_run', deep_research: false, paused: true },
  credentials: { grok: false, gemini: false, espn_s2: false, swid: false },
  policy: null,
  schedule: { enabled: false, timezone: 'UTC', interval_minutes: 60, next_run_at: null },
  capabilities: [],
  active_run: null,
}
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })

describe('application session flow', () => {
  it('shows a token form on 401 instead of retrying authentication', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ detail: 'Unauthorized' }, 401))
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByLabelText('League password or local token')).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/session')
    expect(screen.getByText(/league password or local launch token is required/i)).toBeTruthy()
  })
  it('consumes a fragment token, renders the dashboard, and keeps navigation accessible', async () => {
    window.history.replaceState(null, '', '/#token=launch-token')
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url === '/api/login') return json({ ok: true })
      if (url === '/api/session') return json({ csrf_token: 'test-csrf' })
      if (url === '/api/state') return json(state)
      if (url === '/api/runs') return json([])
      throw new Error(`Unexpected request ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'My fantasy team' })).toBeTruthy()
    expect(screen.getByRole('note', { name: 'Limited live execution' }).textContent).toContain('Verified lineup and roster control is available.')
    expect(window.location.hash).toBe('')
    expect(fetchMock.mock.calls[0][1].body).toBe('{"token":"launch-token"}')
    fireEvent.click(screen.getByRole('button', { name: 'Coach settings' }))
    expect(screen.getByRole('note', { name: 'Limited live execution' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Coach settings', level: 1 })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Emergency pause' })).toBeTruthy()
    expect(window.localStorage.length).toBe(0)
  })
  it('requires verified ESPN ownership after the shared league password', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      if (url === '/api/session') return json({ csrf_token: 'test-csrf', espn_connected: false })
      if (url === '/api/state') return json({
        onboarding_required: true,
        manager: null,
        league: { league_id: 656212638, season: 2026 },
      })
      throw new Error(`Unexpected request ${url}`)
    }))
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Connect ESPN to continue' })).toBeTruthy()
    expect(screen.getByLabelText('ESPN espn_s2 cookie')).toBeTruthy()
    expect(screen.getByLabelText('ESPN SWID cookie')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'My fantasy team' })).toBeNull()
  })
  it('returns to manual login when an authenticated state refresh expires', async () => {
    let stateCalls = 0
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) => {
      if (url === '/api/session') return json({ csrf_token: 'test-csrf' })
      if (url === '/api/state') return ++stateCalls === 1 ? json(state) : json({ detail: 'Session expired' }, 401)
      if (url === '/api/runs') return json([])
      throw new Error(`Unexpected request ${url}`)
    }))
    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(screen.getByLabelText('League password or local token')).toBeTruthy())
    expect(stateCalls).toBe(2)
    expect(screen.queryByRole('heading', { name: 'My fantasy team' })).toBeNull()
  })
})
