import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import History from './History'

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })

describe('workspace activity', () => {
  it('surfaces scheduler errors, skips, and audit events without an active run', async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (url === '/api/activity') return json([
        { id: 'error-1', run_id: null, created_at: '2026-09-09T12:00:00Z', kind: 'scheduler_error', data: { detail: 'Provider connection failed before run start.' } },
        { id: 'skip-1', run_id: null, created_at: '2026-09-09T12:01:00Z', kind: 'scheduler_skip', data: { message: 'Manager is paused.' } },
        { id: 'audit-1', run_id: null, created_at: '2026-09-09T12:02:00Z', kind: 'settings_updated', data: { mode: 'dry_run' } },
      ])
      return json([])
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<History active={false} initialRunId={null} onSelectRun={vi.fn()} />)
    fireEvent.click(screen.getByText('Behind the scenes: activity and troubleshooting'))
    const activity = within(screen.getByRole('region', { name: 'Workspace and scheduler activity' }))
    expect(await activity.findByText('Provider connection failed before run start.')).toBeTruthy()
    expect(activity.getByText('Manager is paused.')).toBeTruthy()
    expect(activity.getByText('scheduler error')).toBeTruthy()
    expect(activity.getByText('scheduler skip')).toBeTruthy()
    expect(activity.getByText('settings updated')).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledWith('/api/activity', expect.objectContaining({ credentials: 'same-origin' }))
  })
  it('reports activity-fetch errors independently of run history', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) =>
      url === '/api/activity' ? json({ detail: 'Audit store unavailable' }, 503) : json([])))
    render(<History active={false} initialRunId={null} onSelectRun={vi.fn()} />)
    expect(await screen.findByText('Could not load workspace activity: Audit store unavailable')).toBeTruthy()
    expect(screen.getByText('Your first run starts here')).toBeTruthy()
    expect(screen.queryByText('No workspace events yet')).toBeNull()
  })
  it('keeps run errors visible and distinguishes model summaries from transaction confirmations', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (url: string) =>
      url === '/api/runs/failed-run' ? json({
        id: 'failed-run', status: 'failed', provider: 'grok', model: 'test-model', mode: 'dry_run',
        started_at: '2026-09-09T12:00:00Z', summary: 'A lineup proposal was prepared.',
        error: 'Provider request failed before completion.', events: [],
      }) : json([])))
    render(<History active={false} initialRunId="failed-run" onSelectRun={vi.fn()} />)
    expect(await screen.findByRole('heading', { name: 'Your game plan' })).toBeTruthy()
    expect(screen.getByText(/not confirmation of an ESPN transaction/)).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toBe('Provider request failed before completion.')
  })
})
