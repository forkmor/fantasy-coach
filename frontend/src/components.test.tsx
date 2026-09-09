import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import Setup from './Setup'
import ScheduleEditor from './ScheduleEditor'
import Dashboard from './Dashboard'
import { TeamSnapshot } from './Team'
import { Usage } from './History'
import { establishSession, resetSession } from './api'
import { DEFAULT_POLICY } from './validation'
import type { AppState } from './types'

const state: AppState = {
  settings: { provider: 'grok', model: 'test-model', mode: 'dry_run', deep_research: false, paused: true },
  credentials: { grok: false, gemini: false, espn_s2: false, swid: false },
  policy: null,
  schedule: { enabled: false, timezone: 'UTC', interval_minutes: 60, next_run_at: null },
  capabilities: [{ action: 'set_lineup', enabled: false, reason: 'ESPN writes are unverified.' }],
  active_run: null,
}
const json = (data: unknown) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })

describe('safety UI', () => {
  it('blocks schedule enablement before credentials and policy are configured', () => {
    render(<ScheduleEditor state={state} onSaved={vi.fn()} />)
    expect((screen.getByLabelText('Let my coach check in automatically') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText('Choose and save your coach settings')).toBeTruthy()
  })
  it('keeps enabled schedules disableable even after credentials are removed', () => {
    render(<ScheduleEditor state={{ ...state, schedule: { ...state.schedule, enabled: true } }} onSaved={vi.fn()} />)
    const checkbox = screen.getByLabelText('Let my coach check in automatically') as HTMLInputElement
    expect(checkbox.disabled).toBe(false)
    fireEvent.click(checkbox)
    expect(checkbox.checked).toBe(false)
  })
  it('requires an explicit resume choice before starting a paused coach review', () => {
    render(<Dashboard state={state} onSaved={vi.fn()} onRun={vi.fn()} onSetup={vi.fn()} onPolicy={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Review my team/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Next: review the plan' }))
    expect((screen.getByRole('button', { name: 'Ask my coach' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Two-player lineup swaps and single free-agent adds or roster-player drops are verified live ESPN actions/)).toBeTruthy()
  })
  it('disables live mode when capabilities are unavailable', () => {
    render(<Setup state={state} onSaved={vi.fn()} />)
    expect((screen.getByRole('option', { name: 'Live · unavailable' }) as HTMLOptionElement).disabled).toBe(true)
  })
  it('links hosted managers to official Grok and Gemini API key sites', () => {
    render(<Setup state={{ ...state, browser_login_available: false }} onSaved={vi.fn()} />)
    expect(screen.getByRole('link', { name: /Connect to Grok/ }).getAttribute('href')).toBe('https://console.x.ai/')
    expect(screen.getByRole('link', { name: /Connect to Gemini/ }).getAttribute('href')).toBe('https://aistudio.google.com/app/apikey')
    expect(screen.getByRole('heading', { name: 'Reconnect ESPN' })).toBeTruthy()
  })
  it('saves deep player research as an explicit manager setting', async () => {
    resetSession()
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ csrf_token: 'test-csrf' }))
      .mockResolvedValueOnce(json({ ...state.settings, deep_research: true }))
    vi.stubGlobal('fetch', fetchMock)
    await establishSession()
    render(<Setup state={state} onSaved={vi.fn().mockResolvedValue(undefined)} />)
    fireEvent.click(screen.getByLabelText('Deep player research'))
    fireEvent.click(screen.getByRole('button', { name: 'Save manager settings' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(JSON.parse(String(fetchMock.mock.calls[1][1].body)).deep_research).toBe(true)
  })
  it('clears credential input immediately on submission and never restores its value', async () => {
    resetSession()
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ csrf_token: 'test-csrf' })).mockResolvedValueOnce(json({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    await establishSession()
    render(<Setup state={state} onSaved={vi.fn().mockResolvedValue(undefined)} />)
    const input = screen.getAllByLabelText('Credential')[0] as HTMLInputElement
    fireEvent.change(input, { target: { value: 'test-key-not-real' } })
    fireEvent.submit(input.closest('form')!)
    expect(input.value).toBe('')
    await waitFor(() => expect(screen.getByText(/Credential saved/)).toBeTruthy())
    expect(fetchMock.mock.calls[1][1].body).toBe('{"name":"grok","value":"test-key-not-real"}')
    expect(window.localStorage.length).toBe(0)
  })
  it('allows run once setup is saved, unpaused, and no agent is active', () => {
    render(<Dashboard state={{ ...state, settings: { ...state.settings, paused: false }, policy: DEFAULT_POLICY, credentials: { ...state.credentials, grok: true, espn_s2: true, swid: true } }}
      onSaved={vi.fn()} onRun={vi.fn()} onSetup={vi.fn()} onPolicy={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Review my team/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Next: review the plan' }))
    expect((screen.getByRole('button', { name: 'Ask my coach' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('readable server data', () => {
  it('renders normalized roster and keeps full JSON available', () => {
    render(<TeamSnapshot snapshot={{ team: { name: 'Local Team', id: 4 }, roster: [{ player: { id: 12, name: 'Test Player', position: 'QB', pro_team: 'BUF' }, slot: 'QB' }], settings: { scoring: 'PPR' } }} />)
    expect(screen.getByText('Test Player')).toBeTruthy()
    expect(screen.getByText(/QB · BUF/)).toBeTruthy()
    expect(screen.queryByText('ID 12')).toBeNull()
    expect(screen.getByText('Full normalized ESPN snapshot')).toBeTruthy()
  })
  it('handles arbitrary and empty roster data without inventing a team', () => {
    render(<TeamSnapshot snapshot={{ roster: null, detail: 'No team' }} />)
    expect(screen.getByText('No roster rows available')).toBeTruthy()
  })
  it('shows actual usage without invented dollar costs', () => {
    render(<Usage usage={{ input_tokens: 400, output_tokens: 50 }} />)
    expect(screen.getByText('400')).toBeTruthy()
    expect(screen.getByText('50')).toBeTruthy()
    expect(screen.getByText(/No estimated costs/)).toBeTruthy()
  })
})
