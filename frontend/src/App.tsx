import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, consumeLaunchToken, errorMessage, establishSession, onUnauthorized, request, resetSession } from './api'
import type { AppState, OnboardingState } from './types'
import Dashboard from './Dashboard'
import Setup from './Setup'
import PolicyEditor from './PolicyEditor'
import ScheduleEditor from './ScheduleEditor'
import History from './History'
import { Badge, Notice, OperationStatus, useOperation } from './ui'
import EspnConnection from './EspnConnection'

const NAV = [
  { id: 'dashboard', label: 'My team', glyph: '◫', caption: 'Your fantasy clubhouse' },
  { id: 'setup', label: 'Connections', glyph: '⌘', caption: 'ESPN and your AI coach' },
  { id: 'policy', label: 'Coach settings', glyph: '◇', caption: 'Permissions and keepers' },
  { id: 'history', label: 'Decisions', glyph: '≡', caption: 'Advice and suggested moves' },
  { id: 'schedule', label: 'Check-in schedule', glyph: '◷', caption: 'When your coach checks in' },
] as const
type Page = typeof NAV[number]['id']

function Login({ onLogin, initialError }: { onLogin: (token?: string) => Promise<void>; initialError: string }) {
  const [token, setToken] = useState('')
  const op = useOperation()
  return <main className="login-screen"><section className="login-card">
    <div className="brand"><span className="brand-mark" aria-hidden="true">F</span><span>FIELDHOUSE<small>Fantasy football harness</small></span></div>
    <p className="eyebrow">Your league. Your team.</p><h1>Welcome to Fieldhouse.</h1>
    <p className="muted">Enter your league’s shared Fieldhouse password. You will connect your own ESPN team privately after entering.</p>
    {initialError && !op.error && <Notice message={initialError} />}
    <form onSubmit={event => {
      event.preventDefault()
      const submitted = token.trim()
      setToken('')
      void op.perform(async () => { await onLogin(submitted) })
    }}><label htmlFor="access-token">League password or local token</label><input id="access-token" type="password" autoComplete="current-password" spellCheck={false} value={token}
      onChange={event => setToken(event.target.value)} required disabled={op.pending} placeholder="Enter your league password" />
      <button className="button primary full-width" disabled={!token.trim() || op.pending}>{op.pending ? 'Connecting…' : 'Open my clubhouse →'}</button>
      <OperationStatus {...op} />
    </form>
    <p className="field-hint">This is not an ESPN cookie or provider API key. Passwords, tokens, and credentials are never saved in browser storage.</p>
    <button className="button text" disabled={op.pending} onClick={() => { void op.perform(() => onLogin()) }}>Retry existing browser session</button>
  </section></main>
}

export default function App() {
  const [state, setState] = useState<AppState | OnboardingState | null>(null)
  const [auth, setAuth] = useState<'loading' | 'required' | 'ready'>('loading')
  const [initialError, setInitialError] = useState('')
  const [refreshError, setRefreshError] = useState('')
  const [page, setPage] = useState<Page>('dashboard')
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const pauseOp = useOperation()
  const generation = useRef(0)
  const mainRef = useRef<HTMLElement>(null)
  const refresh = useCallback(async () => {
    const version = generation.current
    const next = await request<AppState | OnboardingState>('/state')
    if (version === generation.current) {
      setState(next)
      setLastUpdated(new Date())
      setRefreshError('')
    }
  }, [])
  const login = useCallback(async (token?: string) => {
    await establishSession(token)
    await refresh()
    setInitialError('')
    setAuth('ready')
  }, [refresh])
  useEffect(() => {
    let mounted = true
    onUnauthorized(() => {
      generation.current += 1
      setState(null)
      setAuth('required')
      setInitialError('Your Fieldhouse session has expired. Sign in again.')
    })
    const token = consumeLaunchToken()
    void login(token).catch(error => {
      if (!mounted) return
      setAuth('required')
      setInitialError(error instanceof ApiError && error.status === 401 ? 'Your league password or local launch token is required.' : errorMessage(error))
    })
    return () => { mounted = false; onUnauthorized(); resetSession() }
  }, [login])
  useEffect(() => {
    if (auth !== 'ready') return
    let running = false
    const timer = window.setInterval(() => {
      if (running || document.hidden) return
      running = true
      void refresh().catch(error => setRefreshError(errorMessage(error))).finally(() => { running = false })
    }, 5000)
    return () => window.clearInterval(timer)
  }, [auth, refresh])
  function navigate(next: Page) {
    setPage(next)
    window.setTimeout(() => mainRef.current?.focus(), 0)
  }
  function openRun(id: string) {
    setSelectedRun(id)
    navigate('history')
  }
  if (auth === 'loading') return <main className="loading-screen"><div className="brand-mark" aria-hidden="true">F</div><p role="status">Opening your clubhouse…</p></main>
  if (auth !== 'ready' || !state) return <Login onLogin={login} initialError={initialError} />
  const signOut = async () => {
    await request('/logout', 'POST', {})
    generation.current += 1
    resetSession()
    setState(null)
    setInitialError('You have signed out of Fieldhouse.')
    setAuth('required')
  }
  if ('onboarding_required' in state && state.onboarding_required) {
    return <main className="login-screen"><section className="login-card wide">
      <div className="brand"><span className="brand-mark" aria-hidden="true">F</span><span>FIELDHOUSE<small>League {state.league.league_id}</small></span></div>
      <EspnConnection onConnected={refresh} />
      <button className="button text" onClick={() => { void signOut() }}>Sign out</button>
    </section></main>
  }
  return <div className="app-shell">
    <a className="skip-link" href="#main">Skip to main content</a>
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark" aria-hidden="true">F</span><span>FIELDHOUSE<small>Fantasy football harness</small></span></div>
      <p className="sidebar-label">{state.manager?.team_name ?? 'YOUR CLUBHOUSE'}</p>
      <nav aria-label="Main navigation">{NAV.map(item => <button key={item.id} className={`nav-item ${page === item.id ? 'active' : ''}`} aria-label={item.label} title={item.label} aria-current={page === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}>
        <span className="nav-glyph" aria-hidden="true">{item.glyph}</span><span>{item.label}<small>{item.caption}</small></span>
      </button>)}</nav>
      <div className="sidebar-bottom"><div className="local-status"><span className="status-dot" />Your private clubhouse</div><p>Your team. Your rules.<br />You make the final call.</p><button className="button text compact" onClick={() => { void signOut() }}>Sign out</button><div className="sidebar-rule" /><small>Live lineup swaps require<br />your final confirmation.</small></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div><p className="breadcrumb">FIELDHOUSE <span>/</span> {NAV.find(item => item.id === page)?.label}</p><h1>{page === 'dashboard' ? 'My fantasy team' : NAV.find(item => item.id === page)?.label}</h1></div>
        <div className="topbar-actions"><Badge tone={state.settings.paused ? 'amber' : 'green'}>{state.settings.paused ? 'Paused' : 'Unpaused'}</Badge>
          <button className="button emergency" disabled={pauseOp.pending} onClick={() => { void pauseOp.perform(async () => {
            await request('/pause', 'POST', {})
            await refresh()
          }, 'Emergency pause applied. Subsequent activity is blocked. External requests already received cannot be undone.') }}><span aria-hidden="true">Ⅱ</span>{pauseOp.pending ? 'Pausing…' : 'Emergency pause'}</button></div>
      </header>
      <main id="main" tabIndex={-1} ref={mainRef} className="main-content">
        <div className="notice live-limit" role="note" aria-label="Limited live execution">
          <strong>Verified lineup and roster control is available.</strong> You can confirm an exact starter/bench swap or a single free-agent add/drop, or permit the coach to execute those actions in Live mode. Waivers and trades remain disabled.
        </div>
        <OperationStatus {...pauseOp} />
        {refreshError && <Notice message={`Could not refresh your clubhouse. Information may be out of date: ${refreshError}`} />}
        <div className="sync-line"><span>{refreshError ? 'Connection interrupted' : lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Getting ready'}</span>
          <button className="button text compact" disabled={refreshing} onClick={() => {
            setRefreshing(true)
            void refresh().catch(error => setRefreshError(errorMessage(error))).finally(() => setRefreshing(false))
          }}>{refreshing ? 'Refreshing…' : 'Refresh'}</button>
        </div>
        {page === 'dashboard' && <Dashboard state={state} onSaved={refresh} onRun={openRun} onSetup={() => navigate('setup')} onPolicy={() => navigate('policy')} />}
        {page === 'setup' && <Setup state={state} onSaved={refresh} />}
        {page === 'policy' && <PolicyEditor state={state} onSaved={refresh} />}
        {page === 'schedule' && <ScheduleEditor state={state} onSaved={refresh} />}
        {page === 'history' && <History active={Boolean(state.active_run)} initialRunId={selectedRun} onSelectRun={setSelectedRun} connected={state.credentials.espn_s2 && state.credentials.swid} />}
        <footer className="footer"><span>Fieldhouse · your fantasy football clubhouse</span><span>Only verified, owner-permitted capabilities can change ESPN.</span></footer>
      </main>
    </div>
  </div>
}
