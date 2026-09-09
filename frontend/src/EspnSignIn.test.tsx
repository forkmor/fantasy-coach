import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EspnSignIn from './EspnSignIn'
import { request } from './api'

vi.mock('./api', () => ({ request: vi.fn() }))

describe('ESPN browser sign-in', () => {
  beforeEach(() => { vi.mocked(request).mockReset() })

  it('opens the browser through the guarded backend without requesting a password', async () => {
    vi.mocked(request).mockResolvedValue({ status: 'waiting', active: true, detail: 'Sign in' })
    const saved = vi.fn().mockResolvedValue(undefined)
    render(<EspnSignIn runLocked={false} onSaved={saved} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to ESPN' }))
    await waitFor(() => expect(saved).toHaveBeenCalledOnce())
    expect(request).toHaveBeenCalledWith('/espn-login/start', 'POST', {})
    expect(document.querySelector('input[type=password]')).toBeNull()
  })

  it('lets the owner capture or cancel an active session', async () => {
    vi.mocked(request).mockResolvedValue({ status: 'connected', active: false, detail: 'Saved' })
    render(<EspnSignIn state={{ status: 'waiting', active: true, detail: 'Sign in in Chrome' }}
      runLocked={false} onSaved={vi.fn().mockResolvedValue(undefined)} />)
    expect((screen.getByRole('button', { name: 'Sign in to ESPN' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Save ESPN session' }))
    await waitFor(() => expect(request).toHaveBeenCalledWith('/espn-login/finish', 'POST', {}))
  })

  it('blocks opening during an active manager run and surfaces failures', () => {
    render(<EspnSignIn state={{ status: 'failed', active: false, detail: 'Chrome could not open' }}
      runLocked={true} onSaved={vi.fn()} />)
    expect((screen.getByRole('button', { name: 'Sign in to ESPN' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('alert').textContent).toBe('Chrome could not open')
  })

  it('surfaces request errors rather than claiming credentials were saved', async () => {
    vi.mocked(request).mockRejectedValue(new Error('Local session expired'))
    render(<EspnSignIn runLocked={false} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Sign in to ESPN' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Local session expired'))
  })
})
