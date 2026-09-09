import { useState } from 'react'
import { request } from './api'
import { Notice, OperationStatus, useOperation } from './ui'

export default function EspnConnection({ onConnected, connected = false }: {
  onConnected: () => Promise<void>
  connected?: boolean
}) {
  const [espnS2, setEspnS2] = useState('')
  const [swid, setSwid] = useState('')
  const op = useOperation()
  return <section className="card">
    <p className="eyebrow">Your ESPN team</p>
    <h2>{connected ? 'Reconnect ESPN' : 'Connect ESPN to continue'}</h2>
    <p className="muted">Fieldhouse uses ESPN session cookies—not your ESPN password—to verify which team you own in this league. Your cookies are encrypted and isolated from every other manager.</p>
    <Notice tone="info" message="In a browser where you are signed in to ESPN, copy the espn_s2 and SWID cookie values from the ESPN site’s developer tools. Never send them to another league member." />
    <form onSubmit={event => {
      event.preventDefault()
      const submitted = { espn_s2: espnS2.trim(), swid: swid.trim() }
      setEspnS2('')
      setSwid('')
      void op.perform(async () => {
        await request('/espn/connect', 'POST', submitted)
        await onConnected()
      }, 'ESPN ownership verified. Your clubhouse is ready.')
    }}>
      <label htmlFor="espn-connect-s2">ESPN espn_s2 cookie</label>
      <input id="espn-connect-s2" type="password" autoComplete="off" spellCheck={false}
        value={espnS2} onChange={event => setEspnS2(event.target.value)}
        placeholder="Paste your espn_s2 cookie" disabled={op.pending} required />
      <label htmlFor="espn-connect-swid">ESPN SWID cookie</label>
      <input id="espn-connect-swid" type="password" autoComplete="off" spellCheck={false}
        value={swid} onChange={event => setSwid(event.target.value)}
        placeholder="{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}" disabled={op.pending} required />
      <button className="button primary full-width" disabled={op.pending || !espnS2.trim() || !swid.trim()}>
        {op.pending ? 'Verifying team ownership…' : connected ? 'Verify and replace ESPN connection' : 'Verify and open my team'}
      </button>
      <OperationStatus {...op} />
    </form>
  </section>
}
