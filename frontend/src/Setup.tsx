import { useEffect, useState } from 'react'
import { request } from './api'
import type { AppState, CredentialName, Provider, Settings } from './types'
import { Badge, Notice, OperationStatus, SectionHeading, useOperation } from './ui'
import EspnSignIn from './EspnSignIn'
import EspnConnection from './EspnConnection'

const CREDENTIALS: { name: CredentialName; title: string; subtitle: string; placeholder: string }[] = [
  { name: 'grok', title: 'Grok · xAI', subtitle: 'Use an xAI API key. A Grok chat subscription is not an API key.', placeholder: 'Enter xAI API key' },
  { name: 'gemini', title: 'Gemini · Google', subtitle: 'Use a Google Gemini API key with access to your selected model.', placeholder: 'Enter Gemini API key' },
  { name: 'espn_s2', title: 'ESPN · espn_s2', subtitle: 'Your ESPN session cookie. Copy it privately from your own signed-in browser.', placeholder: 'Enter espn_s2 cookie' },
  { name: 'swid', title: 'ESPN · SWID', subtitle: 'Your ESPN SWID cookie. Preserve the braces if they are included.', placeholder: 'Enter SWID cookie' },
]

function CredentialCard({ credential, saved, locked, onSaved }: {
  credential: typeof CREDENTIALS[number]; saved: boolean; locked: boolean; onSaved: () => Promise<void>
}) {
  const [value, setValue] = useState('')
  const [confirmRemove, setConfirmRemove] = useState(false)
  const op = useOperation()
  const providerUrl = credential.name === 'grok'
    ? 'https://console.x.ai/'
    : credential.name === 'gemini' ? 'https://aistudio.google.com/app/apikey' : null
  return <article className="card credential-card">
    <div className="card-title"><h3>{credential.title}</h3><Badge tone={saved ? 'green' : 'neutral'}>{saved ? 'Saved' : 'Not connected'}</Badge></div>
    <p className="muted">{credential.subtitle}</p>
    {providerUrl && <a className="button secondary" href={providerUrl} target="_blank" rel="noopener noreferrer">
      Connect to {credential.name === 'grok' ? 'Grok' : 'Gemini'} ↗
    </a>}
    <form onSubmit={event => {
      event.preventDefault()
      const submitted = value.trim()
      setValue('')
      if (!submitted) { op.setError('Enter a credential before saving.'); return }
      void op.perform(async () => {
        await request('/credentials', 'POST', { name: credential.name, value: submitted })
        await onSaved()
      }, 'Credential saved. The value is never returned to this screen.')
    }}>
      <label htmlFor={`credential-${credential.name}`}>{saved ? 'Replace credential' : 'Credential'}</label>
      <input id={`credential-${credential.name}`} type="password" autoComplete="new-password" spellCheck={false}
        value={value} onChange={event => setValue(event.target.value)} placeholder={credential.placeholder} disabled={locked || op.pending} required />
      <div className="button-row">
        <button className="button secondary" disabled={locked || op.pending || !value.trim()}>{op.pending ? 'Saving…' : saved ? 'Replace securely' : 'Save securely'}</button>
        {saved && <button type="button" className="button text danger-text" onClick={() => setConfirmRemove(true)} disabled={locked || op.pending}>Remove</button>}
      </div>
    </form>
    {confirmRemove && <div className="inline-confirm" role="group" aria-label={`Remove ${credential.title}`}>
      <p>Remove this saved credential? Runs using it will be unavailable.</p>
      <div className="button-row"><button className="button danger" disabled={op.pending || locked} onClick={() => {
        setConfirmRemove(false)
        void op.perform(async () => { await request(`/credentials/${credential.name}`, 'DELETE'); await onSaved() }, 'Credential removed.')
      }}>Remove credential</button><button className="button secondary" onClick={() => setConfirmRemove(false)}>Keep it</button></div>
    </div>}
    <OperationStatus {...op} />
  </article>
}

function ConnectionTest({ target, disabled }: { target: Provider | 'espn'; disabled: boolean }) {
  const op = useOperation()
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null)
  const title = target === 'espn' ? 'ESPN' : target === 'grok' ? 'Grok' : 'Gemini'
  return <div className="connection-test">
    <button className="button secondary" disabled={disabled || op.pending} onClick={() => {
      setResult(null)
      void op.perform(async () => { setResult(await request('/connections/test', 'POST', { target })) })
    }}>{op.pending ? `Testing ${title}…` : `Test ${title}`}</button>
    {op.error && <Notice message={op.error} />}
    {result && <Notice tone={result.ok ? 'success' : 'error'} message={`${title}: ${result.detail}`} />}
  </div>
}

export default function Setup({ state, onSaved }: { state: AppState; onSaved: () => Promise<void> }) {
  const [settings, setSettings] = useState<Settings>(state.settings)
  const op = useOperation()
  useEffect(() => { setSettings(state.settings) }, [state.settings.provider, state.settings.model, state.settings.mode, state.settings.deep_research, state.settings.paused])
  const locked = Boolean(state.active_run) || Boolean(state.espn_login?.active)
  const canLive = Boolean(state.policy?.allowed_actions.some(action =>
    state.capabilities.some(capability => capability.action === action && capability.enabled)))
  return <div className="page-stack">
    <SectionHeading eyebrow="Workspace configuration" title="Connections & setup">Keep credentials local. Choose one provider for each run.</SectionHeading>
    <div className="notice info"><strong>API access is separate from chat subscriptions.</strong> Provider requests may incur usage charges. Keys and ESPN cookies are not stored in browser storage and are never shown after saving.</div>
    {state.active_run && <Notice tone="info" message="A manager is running. Stop it before changing providers or credentials." />}
    {state.espn_login?.active && <Notice tone="info" message="Finish or cancel ESPN sign-in before changing other settings or starting the manager." />}
    {state.browser_login_available === false
      ? <EspnConnection connected onConnected={onSaved} />
      : <EspnSignIn state={state.espn_login} runLocked={Boolean(state.active_run)}
          available onSaved={onSaved} />}
    <div className="notice info"><strong>Full ESPN controls:</strong> {state.browser_automation_ready
      ? 'Encrypted browser authorization is ready for scoped ESPN workflows.'
      : 'Sign in to ESPN again to authorize scoped browser workflows for features without a verified API.'}</div>
    <section className="card">
      <SectionHeading title="Active manager"><Badge tone="blue">One agent at a time</Badge></SectionHeading>
      <form onSubmit={event => {
        event.preventDefault()
        void op.perform(async () => {
          await request('/settings', 'POST', { ...settings, model: settings.model.trim() })
          await onSaved()
        }, 'Manager settings saved.')
      }}>
        <fieldset disabled={locked || op.pending}><legend className="sr-only">Manager settings</legend>
          <div className="form-grid">
            <div><label htmlFor="provider">Model provider</label><select id="provider" value={settings.provider} onChange={event => setSettings({ ...settings, provider: event.target.value as Provider, model: '' })}>
              <option value="grok">Grok · xAI</option><option value="gemini">Gemini · Google</option>
            </select></div>
            <div><label htmlFor="model">Model ID</label><input id="model" value={settings.model} onChange={event => setSettings({ ...settings, model: event.target.value })}
              placeholder={settings.provider === 'grok' ? 'Model ID from your xAI account' : 'Model ID from your Gemini account'} required maxLength={120} />
              <p className="field-hint">Enter an explicit model your API account can access. No silent provider fallback.</p></div>
            <div><label htmlFor="mode">Execution mode</label><select id="mode" value={settings.mode} onChange={event => setSettings({ ...settings, mode: event.target.value as Settings['mode'] })}>
              <option value="dry_run">Dry run · proposals only</option>
              <option value="live" disabled={!canLive}>Live · {canLive ? 'verified actions only' : 'unavailable'}</option>
            </select><p className="field-hint">Live mode applies only to capabilities marked verified by the server. Other suggestions stay blocked.</p></div>
            <div className="check-panel"><label className="checkbox-label"><input type="checkbox"
              checked={settings.deep_research} onChange={event => setSettings({ ...settings, deep_research: event.target.checked })} />
              Deep player research</label><p className="field-hint">Before every review, check ESPN news, injury reports, practice notes, and current NFL stats for every rostered player. This takes longer and sends more football context to your selected coach provider.</p></div>
            <div className="check-panel"><label className="checkbox-label"><input type="checkbox" checked={settings.paused} onChange={event => setSettings({ ...settings, paused: event.target.checked })} />Pause the manager</label><p className="field-hint">Paused managers cannot start manual or scheduled runs.</p></div>
          </div>
          <button className="button primary" disabled={!settings.model.trim()}>{op.pending ? 'Saving…' : 'Save manager settings'}</button>
        </fieldset>
        <OperationStatus {...op} />
      </form>
    </section>
    <div className="grid two">    {CREDENTIALS.filter(credential => state.browser_login_available !== false
      || !['espn_s2', 'swid'].includes(credential.name)).map(credential => <CredentialCard key={credential.name} credential={credential} saved={state.credentials[credential.name]} locked={locked} onSaved={onSaved} />)}</div>
    <section className="card">
      <SectionHeading title="Verify connections">Tests contact the selected service. Model connection tests may consume API usage.</SectionHeading>
      <div className="connection-tests">
        <ConnectionTest target="grok" disabled={locked || !state.credentials.grok} />
        <ConnectionTest target="gemini" disabled={locked || !state.credentials.gemini} />
        <ConnectionTest target="espn" disabled={locked || !state.credentials.espn_s2 || !state.credentials.swid} />
      </div>
      <p className="field-hint">A successful connection confirms access, not verified ESPN write capability.</p>
    </section>
  </div>
}
