import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PolicyEditor from './PolicyEditor'
import { request } from './api'
import { useRoster, type Player } from './players'
import { DEFAULT_POLICY } from './validation'
import type { AppState, Policy } from './types'

vi.mock('./api', () => ({ request: vi.fn() }))
vi.mock('./players', async importOriginal => {
  const actual = await importOriginal<typeof import('./players')>()
  return { ...actual, useRoster: vi.fn() }
})

const players: Player[] = [
  { player_id: 101, name: 'Justin Jefferson', position: 'WR', jersey: '18', slot_id: 4, eligible_slots: [4],
    injury_status: 'ACTIVE', pro_team_id: 16, pro_team_abbreviation: 'MIN', projected_points: null, actual_points: null },
  { player_id: -16001, name: 'Falcons D/ST', position: 'D/ST', slot_id: 16, eligible_slots: [16],
    injury_status: 'UNKNOWN', pro_team_id: 1, projected_points: null, actual_points: null },
]
const saved: Policy = {
  ...DEFAULT_POLICY, allowed_actions: ['trade_reject', 'waiver_cancel'], protected_player_ids: [999, -16001],
  trade_values: { '999': 27.5, '888': 0, '101': 50 }, max_adds_per_week: 7, max_drops_per_week: 6,
  max_trades_per_week: 2, waiver_budget: 120, waiver_reserve: 20, max_bid: 35,
  max_trade_players: 3, max_trade_value_loss_pct: 7.5,
  max_tool_calls: 17, max_output_tokens: 4096, max_run_seconds: 180, max_runs_per_day: 3,
}
const state: AppState = {
  settings: { provider: 'grok', model: 'test', mode: 'dry_run', deep_research: false, paused: true },
  credentials: { grok: true, gemini: false, espn_s2: true, swid: true },
  policy: saved, schedule: { enabled: false, timezone: 'UTC', interval_minutes: 60, next_run_at: null },
  capabilities: [], active_run: null,
}
function renderEditor(policy: Policy | null = saved) {
  const onSaved = vi.fn().mockResolvedValue(undefined)
  return { ...render(<PolicyEditor state={{ ...state, policy }} onSaved={onSaved} />), onSaved }
}
function review() {
  fireEvent.click(screen.getByRole('button', { name: 'Review & save settings' }))
  return screen.getByRole('dialog', { name: 'Save these coach settings?' })
}
async function save() {
  const modal = review()
  fireEvent.click(within(modal).getByRole('checkbox', { name: 'I reviewed these settings and want to save them.' }))
  fireEvent.click(within(modal).getByRole('button', { name: 'Save coach settings' }))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(1))
  return vi.mocked(request).mock.calls[0][2] as Policy
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(request).mockResolvedValue({ ok: true })
  vi.mocked(useRoster).mockReturnValue({
    snapshot: { settings: { acquisitionSettings: { isUsingAcquisitionBudget: true, acquisitionBudget: 999 } } },
    players, loading: false, error: '', refresh: vi.fn(),
  })
})

describe('Coach settings decisions', () => {
  it('uses roster names, real jerseys and independent protected-player checkboxes', async () => {
    renderEditor()
    expect(screen.getAllByText(/#18/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('textbox')).toBeNull()
    expect((screen.getByRole('checkbox', { name: 'Protect Falcons D/ST' }) as HTMLInputElement).checked).toBe(true)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Protect Justin Jefferson' }))
    expect(request).not.toHaveBeenCalled()
    const next = await save()
    expect(next).toEqual({ ...saved, protected_player_ids: [999, -16001, 101] })
  })

  it('retains every saved field, unknown IDs and unknown trade values on an unchanged save', async () => {
    const { onSaved } = renderEditor()
    const modal = review()
    expect((within(modal).getByRole('button', { name: 'Save coach settings' }) as HTMLButtonElement).disabled).toBe(true)
    expect(request).not.toHaveBeenCalled()
    fireEvent.click(within(modal).getByRole('button', { name: 'Go back' }))
    const next = await save()
    expect(next).toEqual(saved)
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(vi.mocked(request).mock.calls[0].slice(0, 2)).toEqual(['/policy', 'POST'])
  })

  it('cancels suggested settings without editing the draft or saved settings', () => {
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Review suggested settings' }))
    expect(screen.getByRole('dialog', { name: 'Try these cautious settings?' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Keep my draft' }))
    expect((screen.getByRole('slider', { name: /Weekly pickups/ }) as HTMLInputElement).value).toBe('7')
    expect((screen.getByRole('checkbox', { name: 'Suggest lineup changes' }) as HTMLInputElement).checked).toBe(false)
    expect(request).not.toHaveBeenCalled()
  })

  it('applies suggested changes only to the draft and requires another review and explicit save', async () => {
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Review suggested settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Apply to draft only' }))
    expect(request).not.toHaveBeenCalled()
    expect((screen.getByRole('slider', { name: /Weekly pickups/ }) as HTMLInputElement).value).toBe('0')
    expect((screen.getByRole('checkbox', { name: 'Suggest lineup changes' }) as HTMLInputElement).checked).toBe(true)
    const next = await save()
    expect(next).toEqual({ ...saved, allowed_actions: ['set_lineup'], max_adds_per_week: 0,
      max_drops_per_week: 0, max_trades_per_week: 0, max_bid: 0 })
  })

  it('requires consent again after cancelling the final review, including Escape', () => {
    renderEditor()
    let modal = review()
    fireEvent.click(within(modal).getByRole('checkbox'))
    fireEvent(modal, new Event('cancel', { bubbles: false, cancelable: true }))
    expect(screen.queryByRole('dialog')).toBeNull()
    modal = review()
    expect((within(modal).getByRole('checkbox') as HTMLInputElement).checked).toBe(false)
    expect((within(modal).getByRole('button', { name: 'Save coach settings' }) as HTMLButtonElement).disabled).toBe(true)
    expect(request).not.toHaveBeenCalled()
  })

  it('retains all eight granular permissions rather than broadening related actions', async () => {
    renderEditor()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Suggest accepting trades' }))
    const next = await save()
    expect(next.allowed_actions).toEqual(['trade_reject', 'waiver_cancel', 'trade_accept'])
    expect(next.allowed_actions).not.toContain('trade_propose')
  })

  it('updates weekly sliders, budgets, and named trade values without adjusting other limits', async () => {
    renderEditor()
    fireEvent.change(screen.getByRole('slider', { name: /Weekly pickups/ }), { target: { value: '4' } })
    fireEvent.change(screen.getByRole('slider', { name: /Weekly drops/ }), { target: { value: '3' } })
    fireEvent.change(screen.getByRole('slider', { name: /Weekly trades/ }), { target: { value: '1' } })
    fireEvent.change(screen.getByRole('slider', { name: /Keep in reserve/ }), { target: { value: '30' } })
    fireEvent.change(screen.getByRole('slider', { name: /Maximum single bid/ }), { target: { value: '25' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Trade value for Justin Jefferson' }), { target: { value: '62.5' } })
    const next = await save()
    expect(next).toEqual({ ...saved, max_adds_per_week: 4, max_drops_per_week: 3, max_trades_per_week: 1,
      waiver_reserve: 30, max_bid: 25, trade_values: { ...saved.trade_values, '101': 62.5 } })
  })

  it('blocks inconsistent budgets instead of silently lowering a saved bid', () => {
    renderEditor()
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Waiver budget' }), { target: { value: '40' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review & save settings' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('Maximum bid cannot exceed budget minus the reserve')
    expect((screen.getByRole('slider', { name: /Maximum single bid/ }) as HTMLInputElement).value).toBe('35')
    expect(request).not.toHaveBeenCalled()
  })

  it('respects a false league FAAB flag without clearing stored amounts', async () => {
    vi.mocked(useRoster).mockReturnValue({ snapshot: { settings: { acquisitionSettings: { isUsingAcquisitionBudget: false, acquisitionBudget: 500 } } },
      players, loading: false, error: '', refresh: vi.fn() })
    renderEditor()
    expect(screen.getByText(/This league does not use FAAB/)).toBeTruthy()
    expect((screen.getByRole('spinbutton', { name: 'Waiver budget' }).closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true)
    expect(await save()).toEqual(saved)
  })

  it('requires confirmation before removing unavailable protections or values, keeping the other intact', async () => {
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Remove protection for Unavailable saved player 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep saved player' }))
    expect(screen.getByRole('button', { name: 'Remove protection for Unavailable saved player 1' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove protection for Unavailable saved player 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove from draft' }))
    expect(request).not.toHaveBeenCalled()
    const modal = review()
    expect(within(modal).getByText('Unavailable saved player 1: protection removed')).toBeTruthy()
    fireEvent.click(within(modal).getByRole('button', { name: 'Go back' }))
    expect(await save()).toEqual({ ...saved, protected_player_ids: [-16001] })
  })

  it('removes an unavailable trade value only after confirmation and retains its protection', async () => {
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Remove value for Unavailable saved player 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep saved player' }))
    expect(screen.getByLabelText('Unavailable saved player 1 · saved trade value')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove value for Unavailable saved player 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove from draft' }))
    const { '999': removed, ...trade_values } = saved.trade_values
    expect(removed).toBe(27.5)
    expect(await save()).toEqual({ ...saved, trade_values })
  })

  it('keeps saved protections and values when roster loading fails, without invented names', async () => {
    const refresh = vi.fn()
    vi.mocked(useRoster).mockReturnValue({ snapshot: null, players: [], loading: false, error: 'Unavailable', refresh })
    renderEditor()
    expect(screen.getByText(/Your roster could not be loaded/)).toBeTruthy()
    expect(screen.queryByText('Justin Jefferson')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try roster again' }))
    expect(refresh).toHaveBeenCalledTimes(1)
    expect(await save()).toEqual(saved)
  })

  it('leaves missing league bidding data unknown and never automatically applies defaults', () => {
    vi.mocked(useRoster).mockReturnValue({ snapshot: null, players: [], loading: true, error: '', refresh: vi.fn() })
    renderEditor(null)
    expect(screen.getByText(/We could not confirm whether your league uses FAAB/)).toBeTruthy()
    expect(screen.getByText(/Loading your roster/)).toBeTruthy()
    expect(request).not.toHaveBeenCalled()
  })

  it('keeps the review open after a failed save and never calls the saved callback', async () => {
    vi.mocked(request).mockRejectedValue(new Error('Could not save settings'))
    const { onSaved } = renderEditor()
    const modal = review()
    fireEvent.click(within(modal).getByRole('checkbox'))
    fireEvent.click(within(modal).getByRole('button', { name: 'Save coach settings' }))
    await waitFor(() => expect(within(modal).getByRole('alert').textContent).toBe('Could not save settings'))
    expect(onSaved).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
  })

  it('reports empty technical inputs even when the advanced section is collapsed', () => {
    renderEditor()
    fireEvent.change(screen.getByLabelText('Response token limit'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review & save settings' }))
    expect(screen.getByRole('alert').textContent).toContain('must be a non-negative number')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(request).not.toHaveBeenCalled()
  })

  it('does not overwrite changed server settings with an older unsaved draft', () => {
    const onSaved = vi.fn()
    const { rerender } = render(<PolicyEditor state={state} onSaved={onSaved} />)
    fireEvent.change(screen.getByRole('slider', { name: /Weekly pickups/ }), { target: { value: '4' } })
    rerender(<PolicyEditor state={{ ...state, policy: { ...saved, max_adds_per_week: 9 } }} onSaved={onSaved} />)
    expect((screen.getByRole('slider', { name: /Weekly pickups/ }) as HTMLInputElement).value).toBe('4')
    expect((screen.getByRole('button', { name: 'Review & save settings' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Load latest saved settings' }))
    fireEvent.click(screen.getByRole('button', { name: 'Replace with saved settings' }))
    expect((screen.getByRole('slider', { name: /Weekly pickups/ }) as HTMLInputElement).value).toBe('9')
    expect(request).not.toHaveBeenCalled()
  })
})
