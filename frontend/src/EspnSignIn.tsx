import { request } from './api'
import type { EspnLoginState } from './types'
import { Notice, OperationStatus, SectionHeading, useOperation } from './ui'

export default function EspnSignIn({ state, runLocked, onSaved, available = true }: {
  state?: EspnLoginState; runLocked: boolean; onSaved: () => Promise<void>; available?: boolean
}) {
  const op = useOperation()
  const current = state ?? { status: 'idle', active: false, detail: '' }
  function act(action: 'start' | 'finish' | 'cancel') {
    void op.perform(async () => {
      await request<EspnLoginState>(`/espn-login/${action}`, 'POST', {})
      await onSaved()
    })
  }
  if (!available) return <section className="card">
    <SectionHeading title="Connect ESPN">Encrypted manual connection for the hosted app.</SectionHeading>
    <Notice tone="info" message="Browser-assisted sign-in is disabled on the server. Enter espn_s2 and SWID in the encrypted ESPN credential fields below. Fieldhouse never needs your ESPN password." />
  </section>
  return <section className="card">
    <SectionHeading title="Sign in to ESPN">No cookie copying required.</SectionHeading>
    <p className="muted">Open a separate Chrome window, sign in to ESPN yourself, then return here and save the session.
      The app verifies your team ownership before encrypting the session. It never asks for your ESPN password or verification code.</p>
    <p className="field-hint">Use this on the Windows computer running the app. The temporary browser is separate from your usual profile,
      closes after saving or cancelling, and expires after 10 minutes. This does not enable live team changes.</p>
    <div className="button-row">
      <button className="button primary" disabled={runLocked || op.pending || current.active} onClick={() => act('start')}>
        {current.status === 'opening' ? 'Opening ESPN...' : 'Sign in to ESPN'}
      </button>
      {current.active && <>
        <button className="button secondary" disabled={op.pending || current.status !== 'waiting'} onClick={() => act('finish')}>
          {current.status === 'verifying' ? 'Verifying session...' : 'Save ESPN session'}
        </button>
        <button className="button text" disabled={op.pending} onClick={() => act('cancel')}>Cancel sign-in</button>
      </>}
    </div>
    {current.detail && <Notice message={current.detail}
      tone={current.status === 'connected' ? 'success' : ['failed', 'expired'].includes(current.status) ? 'error' : 'info'} />}
    <OperationStatus {...op} />
  </section>
}
