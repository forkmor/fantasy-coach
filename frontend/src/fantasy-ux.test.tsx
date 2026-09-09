import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { establishSession, resetSession } from './api'
import ReviewWizard from './ReviewWizard'
import ScheduleEditor from './ScheduleEditor'
import { TeamSnapshot } from './Team'
import { normalizePlayers, points, safeImageUrl, slotName } from './players'
import { DEFAULT_POLICY } from './validation'
import { friendlyExcerpt, withPlayerNames } from './AdviceText'
import PlayerAvatar from './PlayerAvatar'
import LeagueStats from './LeagueStats'
import LineupSwap from './LineupSwap'
import RosterChange from './RosterChange'
import type { AppState } from './types'

const roster = { roster: [
  { player_id: 4038941, name: 'Justin Herbert', position: 'QB', slot_id: 0, eligible_slots: [0, 20],
    jersey: '10', pro_team_name: 'Los Angeles Chargers', pro_team_abbreviation: 'LAC',
    injury_status: 'ACTIVE', pro_team_id: 24, projected_points: 19.25, actual_points: null, droppable: true },
  { player_id: -16001, name: 'Falcons D/ST', position: 'D/ST', slot_id: 20, eligible_slots: [16, 20],
    injury_status: 'UNKNOWN', pro_team_id: 1, projected_points: 0, actual_points: 0 },
] }
const state: AppState = {
  settings: { provider: 'grok', model: 'test-model', mode: 'dry_run', deep_research: false, paused: true },
  credentials: { grok: true, gemini: false, espn_s2: true, swid: true },
  policy: DEFAULT_POLICY, schedule: { enabled: false, timezone: 'UTC', interval_minutes: 60, next_run_at: null },
  capabilities: [], active_run: null,
}
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } })

describe('player-first presentation', () => {
  it('uses names, true jersey numbers, slot names and honest missing/zero scores', () => {
    const players = normalizePlayers(roster)
    expect(players[0].jersey).toBe('10')
    expect(players[1].player_id).toBe(-16001)
    expect(players[1].jersey).toBeNull()
    expect(slotName(20)).toBe('Bench')
    expect(points(0)).toBe('0.00')
    expect(points(null)).toBe('—')
    expect(withPlayerNames('Start player ID: 4038941; keep `-16001`. Scored 19.25 in 2026.', players))
      .toBe('Start Justin Herbert; keep Falcons D/ST. Scored 19.25 in 2026.')
    expect(withPlayerNames('Week 4038941 was mentioned as a number.', players))
      .toBe('Week 4038941 was mentioned as a number.')
    expect(friendlyExcerpt('No live changes. Owner policy allows `set_lineup`.\nSources: `get_team`.', players))
      .toBe('No live changes. your coach settings allow lineup suggestions.')
  })

  it('only accepts allowlisted HTTPS player images', () => {
    expect(safeImageUrl('https://a.espncdn.com/i/test.png')).toBeTruthy()
    for (const url of ['http://a.espncdn.com/a.png', 'https://a.espncdn.com.evil.test/a.png', 'data:image/svg+xml,x',
      'https://user:password@a.espncdn.com/a.png', 'https://a.espncdn.com:999/a.png']) expect(safeImageUrl(url)).toBeUndefined()
  })

  it('never guesses a player photo from an ID when ESPN did not verify an image', () => {
    render(<PlayerAvatar player={{ ...normalizePlayers(roster)[0], headshot_url: null, team_logo_url: null }} />)
    expect(document.querySelector('img')).toBeNull()
    expect(screen.getByText('JH')).toBeTruthy()
  })

  it('opens named player details and cancels a keeper change without saving', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ player_id: 4038941, jersey: '10', facts: [] }))
    vi.stubGlobal('fetch', fetchMock)
    render(<TeamSnapshot snapshot={roster} policy={DEFAULT_POLICY} onSaved={vi.fn()} />)
    const opener = screen.getByRole('button', { name: 'View Justin Herbert' })
    opener.focus()
    fireEvent.click(opener)
    const modal = screen.getByRole('dialog', { name: 'Justin Herbert' })
    expect(within(modal).getByText('#10')).toBeTruthy()
    await waitFor(() => expect(within(modal).queryByText('Loading player details...')).toBeNull())
    fireEvent.click(within(modal).getByRole('button', { name: 'Keep on my team' }))
    fireEvent.click(within(modal).getByRole('button', { name: 'Go back' }))
    expect(fetchMock.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
    fireEvent.click(within(modal).getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(opener)
  })

  it('renders sanitized league standings and highlights the owner team', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({
      scoring_period_id: 1,
      league: { team_count: 2, final_scoring_period: 17 },
      standings: [
        { team_id: 5, rank: 1, name: 'Other Team', is_my_team: false, waiver_rank: 2,
          record: { wins: 1, losses: 0, ties: 0, pointsFor: 120, pointsAgainst: 90 },
          transactions: { acquisitions: 2, drops: 1, trades: 0 } },
        { team_id: 4, rank: 2, name: 'Overrated', is_my_team: true, waiver_rank: 3,
          record: { wins: 0, losses: 1, ties: 0, pointsFor: 90, pointsAgainst: 120 },
          transactions: { acquisitions: 0, drops: 0, trades: 0 } },
      ],
    })))
    render(<LeagueStats connected />)
    expect(await screen.findByText('Overrated')).toBeTruthy()
    expect(screen.getByText('Your team')).toBeTruthy()
    expect(screen.getAllByText('#2').length).toBeGreaterThan(0)
  })

  it('requires a named preview and final confirmation for a live lineup swap', async () => {
    resetSession()
    const fetchMock = vi.fn().mockImplementation(async (path: string) => {
      if (path === '/api/session') return json({ csrf_token: 'test' })
      if (path === '/api/lineup/swap/preview') return json({
        operation_id: '11111111-1111-1111-1111-111111111111', status: 'pending_confirmation',
        scoring_period_id: 1,
        starter: { player_id: 1, name: 'Starter QB', from_slot_id: 0, to_slot_id: 20 },
        bench: { player_id: 2, name: 'Bench QB', from_slot_id: 20, to_slot_id: 0 },
      })
      return json({ status: 'completed' })
    })
    vi.stubGlobal('fetch', fetchMock)
    await establishSession()
    const onComplete = vi.fn()
    const players = [
      { ...normalizePlayers(roster)[0], player_id: 1, name: 'Starter QB', slot_id: 0, eligible_slots: [0, 20] },
      { ...normalizePlayers(roster)[0], player_id: 2, name: 'Bench QB', slot_id: 20, eligible_slots: [0, 20] },
    ]
    render(<LineupSwap players={players} enabled onComplete={onComplete} />)
    expect(screen.getByRole('option', { name: /Starter QB · QB · Proj 19.25 · healthy/i })).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Player to move to bench'), { target: { value: '1' } })
    fireEvent.change(screen.getByLabelText('Bench player to start'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review live swap' }))
    expect(await screen.findByRole('dialog', { name: 'Confirm this ESPN lineup swap' })).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm and change ESPN lineup' }))
    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce())
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('requires a named preview and final confirmation for a live roster drop', async () => {
    resetSession()
    const fetchMock = vi.fn().mockImplementation(async (path: string) => {
      if (path === '/api/session') return json({ csrf_token: 'test' })
      if (path === '/api/roster/change/preview') return json({
        operation_id: '11111111-1111-1111-1111-111111111111',
        status: 'pending_confirmation', scoring_period_id: 1, operation: 'drop',
        drop: { player_id: 4038941, name: 'Justin Herbert' },
      })
      return json({ status: 'completed' })
    })
    vi.stubGlobal('fetch', fetchMock)
    await establishSession()
    const onComplete = vi.fn()
    render(<RosterChange players={normalizePlayers(roster)} protectedPlayerIds={[]} enabled onComplete={onComplete} />)
    fireEvent.change(screen.getByLabelText('Roster action'), { target: { value: 'drop' } })
    fireEvent.change(screen.getByLabelText('Roster change player'), { target: { value: '4038941' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review live drop' }))
    const dialog = await screen.findByRole('dialog', { name: 'Confirm this ESPN drop' })
    expect(within(dialog).getByText('Justin Herbert')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm and drop Justin Herbert' }))
    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce())
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})

describe('guided owner decisions', () => {
  it('requires explicit resume and confirmation before sending a review', async () => {
    resetSession()
    const writes: { path: string; body: Record<string, unknown> }[] = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path, options) => {
      if (path === '/api/session') return json({ csrf_token: 'test' })
      writes.push({ path, body: JSON.parse(options.body) })
      return json(path === '/api/runs' ? { id: 'new-review' } : { ok: true })
    }))
    await establishSession()
    const onRun = vi.fn()
    render(<ReviewWizard state={state} onSaved={vi.fn().mockResolvedValue(undefined)} onRun={onRun}
      onSetup={vi.fn()} onPolicy={vi.fn()} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Next: review the plan' }))
    expect(writes).toEqual([])
    expect((screen.getByRole('button', { name: 'Ask my coach' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(writes).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Ask my coach' }))
    await waitFor(() => expect(onRun).toHaveBeenCalledWith('new-review'))
    expect(writes.map(write => write.path)).toEqual(['/api/settings', '/api/runs'])
    expect(writes[0].body).toEqual({ ...state.settings, paused: false })
    expect(writes[1].body.objective).toContain('Use player names')
  })

  it('pauses the coach again when a resumed review fails to start', async () => {
    resetSession()
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (path: string) => {
      if (path === '/api/session') return json({ csrf_token: 'test' })
      writes.push(path)
      if (path === '/api/runs') return new Response(JSON.stringify({ detail: 'Daily review limit reached' }), { status: 409 })
      return json({ ok: true })
    }))
    await establishSession()
    render(<ReviewWizard state={state} onSaved={vi.fn().mockResolvedValue(undefined)} onRun={vi.fn()}
      onSetup={vi.fn()} onPolicy={vi.fn()} onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Next: review the plan' }))
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'Ask my coach' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('paused again'))
    expect(writes).toEqual(['/api/settings', '/api/runs', '/api/pause'])
  })

  it('previews a suggested schedule and never saves it when cancelled', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    render(<ScheduleEditor state={state} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('radio', { name: /Once a day/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Review schedule' }))
    expect(screen.getByRole('dialog', { name: 'Save this check-in schedule?' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Go back' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
