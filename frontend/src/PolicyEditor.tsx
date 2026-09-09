import { useEffect, useState } from 'react'
import { request } from './api'
import Modal from './Modal'
import PlayerAvatar from './PlayerAvatar'
import { useRoster } from './players'
import type { AppState, Policy } from './types'
import { ACTIONS, DEFAULT_POLICY, validatePolicy } from './validation'
import { Badge, Notice, OperationStatus, SectionHeading, useOperation } from './ui'
import './policy.css'

type NumberKey = { [K in keyof Policy]: Policy[K] extends number ? K : never }[keyof Policy]
const LABELS: Record<NumberKey, string> = {
  max_adds_per_week: 'Weekly pickups', max_drops_per_week: 'Weekly drops', max_trades_per_week: 'Weekly trades',
  waiver_budget: 'Waiver budget', waiver_reserve: 'Keep in reserve', max_bid: 'Maximum single bid',
  max_trade_players: 'Players in a trade', max_trade_value_loss_pct: 'Maximum value loss (%)',
  max_tool_calls: 'Checks per review', max_output_tokens: 'Response token limit',
  max_run_seconds: 'Review time limit (seconds)', max_runs_per_day: 'Reviews per day (UTC)',
}
const PERMISSIONS = [
  { title: 'Your lineup', actions: ['set_lineup'] },
  { title: 'Pickups & waivers', actions: ['add_drop', 'waiver_submit', 'waiver_cancel'] },
  { title: 'Trade decisions', actions: ['trade_propose', 'trade_accept', 'trade_reject', 'trade_cancel'] },
]
const ACTION_LABELS: Record<string, string> = {
  set_lineup: 'Suggest lineup changes', add_drop: 'Suggest pickups and drops',
  waiver_submit: 'Suggest waiver claims', waiver_cancel: 'Suggest cancelling claims',
  trade_propose: 'Suggest new trades', trade_accept: 'Suggest accepting trades',
  trade_reject: 'Suggest declining trades', trade_cancel: 'Suggest cancelling trades',
}
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : {}
}
function conservativeDraft(policy: Policy): Policy {
  return { ...policy, allowed_actions: ['set_lineup'], max_adds_per_week: 0,
    max_drops_per_week: 0, max_trades_per_week: 0, max_bid: 0 }
}

export default function PolicyEditor({ state, onSaved }: { state: AppState; onSaved: () => Promise<void> }) {
  const [policy, setPolicy] = useState<Policy>(state.policy ?? DEFAULT_POLICY)
  const [baseline, setBaseline] = useState(JSON.stringify(state.policy))
  const [dirty, setDirty] = useState(false)
  const [modal, setModal] = useState<'preset' | 'save' | 'reload' | null>(null)
  const [removal, setRemoval] = useState<{ key: string; kind: 'protection' | 'value' } | null>(null)
  const [consent, setConsent] = useState(false)
  const op = useOperation()
  const connected = state.credentials.espn_s2 && state.credentials.swid
  const roster = useRoster(connected)
  const players = roster.players
  const serverPolicy = JSON.stringify(state.policy)
  const stale = baseline !== serverPolicy
  useEffect(() => {
    if (!dirty && baseline !== serverPolicy) {
      setPolicy(state.policy ?? DEFAULT_POLICY)
      setBaseline(serverPolicy)
      setModal(null)
      setConsent(false)
    }
  }, [serverPolicy, baseline, dirty, state.policy])

  const acquisition = record(record(record(roster.snapshot).settings).acquisitionSettings)
  const faab = typeof acquisition.isUsingAcquisitionBudget === 'boolean' ? acquisition.isUsingAcquisitionBudget : null
  const previous = state.policy ?? DEFAULT_POLICY
  const savedKeys = [...new Set([...previous.protected_player_ids.map(String), ...Object.keys(previous.trade_values),
    ...policy.protected_player_ids.map(String), ...Object.keys(policy.trade_values)])]
  const unavailableKeys = savedKeys.filter(key => !players.some(player => String(player.player_id) === key))
  function nameFor(key: string) {
    return players.find(player => String(player.player_id) === key)?.name ??
      `Unavailable saved player ${unavailableKeys.indexOf(key) + 1}`
  }
  function update(next: Policy) {
    setPolicy(next)
    setDirty(true)
    setConsent(false)
    op.setError('')
  }
  function numberField(key: NumberKey, min = 0, max = 100000, step = 1) {
    return <div className="policy-number" key={key}>
      <label htmlFor={`policy-${key}`}>{LABELS[key]}</label>
      <input id={`policy-${key}`} type="number" min={min} max={max} step={step} required
        value={Number.isNaN(policy[key]) ? '' : policy[key]}
        onChange={event => update({ ...policy, [key]: event.target.value === '' ? NaN : Number(event.target.value) })} />
    </div>
  }
  function slider(key: NumberKey, max: number, suffix = '') {
    return <div className="policy-slider" key={key}>
      <label htmlFor={`policy-${key}`}>{LABELS[key]} <output>{policy[key]}{suffix}</output></label>
      <input id={`policy-${key}`} type="range" min={0} max={Math.max(max, policy[key]) || 1} step={1}
        value={policy[key]} onChange={event => update({ ...policy, [key]: Number(event.target.value) })} />
      <div className="policy-range-hints" aria-hidden="true"><span>0</span><span>{Math.max(max, policy[key]) || 1}{suffix}</span></div>
    </div>
  }
  function validationError() {
    const error = validatePolicy(policy)
    if (error) return error
    if (Object.values(policy.trade_values).some(value => !Number.isFinite(value) || value < 0)) {
      return 'Player trade values must be non-negative numbers. Enter a value or explicitly remove it.'
    }
    if (stale) return 'Saved settings changed elsewhere. Load the latest settings before reviewing.'
    return null
  }
  function changesFromSaved() {
    const changes: string[] = []
    for (const [action] of ACTIONS) {
      if (previous.allowed_actions.includes(action) !== policy.allowed_actions.includes(action)) {
        changes.push(`${ACTION_LABELS[action]}: ${previous.allowed_actions.includes(action) ? 'Yes' : 'No'} → ${policy.allowed_actions.includes(action) ? 'Yes' : 'No'}`)
      }
    }
    for (const key of Object.keys(LABELS) as NumberKey[]) {
      if (previous[key] !== policy[key]) changes.push(`${LABELS[key]}: ${previous[key]} → ${policy[key]}`)
    }
    for (const key of savedKeys) {
      const wasProtected = previous.protected_player_ids.includes(Number(key))
      const protectedNow = policy.protected_player_ids.includes(Number(key))
      if (wasProtected !== protectedNow) changes.push(`${nameFor(key)}: protection ${protectedNow ? 'added' : 'removed'}`)
      if (previous.trade_values[key] !== policy.trade_values[key]) {
        changes.push(`${nameFor(key)} trade value: ${previous.trade_values[key] ?? 'Not rated'} → ${policy.trade_values[key] ?? 'Not rated'}`)
      }
    }
    return <section className="policy-changes"><h3>{state.policy ? 'Changes from saved settings' : 'Your first saved settings'}</h3>
      {changes.length ? <ul>{changes.map(change => <li key={change}>{change}</li>)}</ul>
        : <p>{state.policy ? 'No changes to your saved settings.' : 'You are choosing the cautious starting draft, with the complete limits below.'}</p>}
    </section>
  }
  function summary(next: Policy) {
    return <div className="policy-review">
      <h3>What your coach may suggest</h3>
      <ul>{ACTIONS.map(([action]) => <li key={action}>{ACTION_LABELS[action]}: <strong>{next.allowed_actions.includes(action) ? 'Yes' : 'No'}</strong></li>)}</ul>
      <h3>Move and budget limits</h3>
      <dl>{(Object.keys(LABELS) as NumberKey[]).map(key => <div key={key}><dt>{LABELS[key]}</dt><dd>{next[key]}</dd></div>)}</dl>
      <h3>Keep on my team</h3>
      {next.protected_player_ids.length ? <ul>{next.protected_player_ids.map(id => <li key={id}>{nameFor(String(id))}</li>)}</ul> : <p>No protected players.</p>}
      <h3>Your trade values</h3>
      {Object.keys(next.trade_values).length ? <ul>{Object.entries(next.trade_values).map(([key, value]) =>
        <li key={key}>{nameFor(key)}: {value}</li>)}</ul> : <p>No reference values supplied.</p>}
    </div>
  }
  const suggested = conservativeDraft(policy)
  return <div className="page-stack policy-editor">
    <SectionHeading eyebrow="You make the calls" title="Coach settings" action={<Badge tone={state.policy ? 'green' : 'amber'}>{state.policy ? 'Settings saved' : 'Draft · not saved'}</Badge>}>
      These control suggestions for now. Real ESPN moves are still off.
    </SectionHeading>
    {!state.policy && <Notice tone="info" message="Start with a cautious draft. Nothing is saved until you review and confirm." />}
    {dirty && <p className="policy-draft-status" role="status">You have unsaved draft changes.</p>}
    {stale && dirty && <Notice message="Saved settings changed elsewhere. Your draft has not overwritten them." />}
    {stale && dirty && <button type="button" className="button secondary" onClick={() => setModal('reload')}>Load latest saved settings</button>}
    <section className="card policy-suggestion">
      <div><h3>Want a cautious starting point?</h3><p>Lineup suggestions only. No pickups, drops, trades, or bids. Your player protections, values, and other limits stay as they are.</p></div>
      <button type="button" className="button secondary" disabled={op.pending} onClick={() => setModal('preset')}>Review suggested settings</button>
    </section>
    <form className="page-stack" noValidate onSubmit={event => {
      event.preventDefault()
      const error = validationError()
      if (error) { op.setError(error); return }
      setConsent(false)
      setModal('save')
    }}>
      <fieldset disabled={op.pending} className="page-stack"><legend className="sr-only">Coach settings draft</legend>
        <section className="card">
          <SectionHeading title="What may your coach suggest?">Choose each decision separately. Checking a box never enables real transactions.</SectionHeading>
          <div className="policy-permissions">{PERMISSIONS.map(group => <fieldset key={group.title}>
            <legend>{group.title}</legend>
            {group.actions.map(action => <label className="policy-check" key={action}>
              <input type="checkbox" checked={policy.allowed_actions.includes(action)} onChange={event =>
                update({ ...policy, allowed_actions: event.target.checked ? [...policy.allowed_actions, action] : policy.allowed_actions.filter(value => value !== action) })} />
              <span>{ACTION_LABELS[action]}</span>
            </label>)}
          </fieldset>)}</div>
        </section>
        <section className="card">
          <SectionHeading title="How busy should your coach be?">Set weekly suggestion limits. Zero means none; these are not a count of moves already made in your league.</SectionHeading>
          <div className="policy-limits">{slider('max_adds_per_week', 100)}{slider('max_drops_per_week', 100)}{slider('max_trades_per_week', 50)}</div>
        </section>
        <section className="card">
          <SectionHeading title="Keep these players on my team">Checked players must not be dropped or traded. These choices stay in your draft until you save.</SectionHeading>
          {roster.loading && <p role="status">Loading your roster… Saved player choices are kept.</p>}
          {roster.error && <Notice message="Your roster could not be loaded. Saved protections and trade values are unchanged; no player details have been guessed." />}
          {roster.error && connected && <button type="button" className="button secondary" onClick={roster.refresh}>Try roster again</button>}
          {!connected && <p>Connect ESPN to choose players by name. Any saved player choices are kept.</p>}
          {!roster.loading && !roster.error && connected && !players.length && <p>No roster players are available. Saved player choices are kept.</p>}
          <div className="policy-player-grid">{players.map(player => <label className="policy-player" key={player.player_id}>
            <input type="checkbox" aria-label={`Protect ${player.name}`} checked={policy.protected_player_ids.includes(player.player_id)}
              onChange={event => update({ ...policy, protected_player_ids: event.target.checked
                ? [...policy.protected_player_ids, player.player_id] : policy.protected_player_ids.filter(id => id !== player.player_id) })} />
            <PlayerAvatar player={player} />
            <span><strong>{player.name}</strong><small>{player.position}{player.jersey ? ` · #${player.jersey}` : ''}{player.pro_team_abbreviation ? ` · ${player.pro_team_abbreviation}` : ''}</small></span>
          </label>)}</div>
          {policy.protected_player_ids.filter(id => unavailableKeys.includes(String(id))).map(id => <div className="policy-unavailable" key={id}>
            <div><strong>{nameFor(String(id))}</strong><p>Still protected · not in the loaded roster</p>
              <details><summary>Saved player reference</summary><p>ESPN identifier: {id}</p></details></div>
            <button type="button" className="button secondary" onClick={() => setRemoval({ key: String(id), kind: 'protection' })}>Remove protection for {nameFor(String(id))}</button>
          </div>)}
        </section>
        <section className="card">
          <SectionHeading title="Waiver spending">FAAB is your league's free-agent bidding budget, not a payment.</SectionHeading>
          {faab === false ? <p>This league does not use FAAB. Saved budget limits are retained, but bidding controls do not apply.</p>
            : faab === true ? <p>Your league uses FAAB. Enter the amount you want your coach to work within; league balances are not applied automatically.</p>
              : <p>We could not confirm whether your league uses FAAB. These limits only apply to bidding leagues; saved amounts have not changed.</p>}
          <fieldset disabled={faab === false} className="policy-budget"><legend className="sr-only">FAAB limits</legend>
            {numberField('waiver_budget')}
            {slider('waiver_reserve', Number.isFinite(policy.waiver_budget) ? policy.waiver_budget : 0)}
            {slider('max_bid', Number.isFinite(policy.waiver_budget) ? Math.max(0, policy.waiver_budget - policy.waiver_reserve) : 0)}
          </fieldset>
          <p className="field-hint">A bid must fit within the budget after your reserve. Changing one limit never silently adjusts another.</p>
        </section>
        <section className="card">
          <SectionHeading title="Trade comfort zone">Your reference values help the coach compare players. They are your ratings, not estimated market prices.</SectionHeading>
          <div className="policy-limits">{numberField('max_trade_players', 0, 20)}{numberField('max_trade_value_loss_pct', 0, 100, 0.1)}</div>
          <p className="field-hint">Use any consistent points scale. Blank means you have not rated that player; missing values may block trade suggestions.</p>
          <div className="policy-trade-values">{players.map(player => <div className="policy-value-row" key={player.player_id}>
            <PlayerAvatar player={player} /><label htmlFor={`trade-value-${player.player_id}`}>{player.name}<small>{player.position}{player.jersey ? ` · #${player.jersey}` : ''}</small></label>
            <input id={`trade-value-${player.player_id}`} aria-label={`Trade value for ${player.name}`} type="number" min={0} step="any"
              value={policy.trade_values[String(player.player_id)] ?? ''} placeholder="Not rated" onChange={event => {
                const trade_values = { ...policy.trade_values }
                if (event.target.value === '') delete trade_values[String(player.player_id)]
                else trade_values[String(player.player_id)] = Number(event.target.value)
                update({ ...policy, trade_values })
              }} />
          </div>)}</div>
          {Object.entries(policy.trade_values).filter(([key]) => unavailableKeys.includes(key)).map(([key, value]) => <div className="policy-unavailable" key={key}>
            <div><label htmlFor={`saved-value-${key}`}>{nameFor(key)} · saved trade value</label>
              <input id={`saved-value-${key}`} type="number" min={0} step="any" required value={Number.isNaN(value) ? '' : value}
                onChange={event => update({ ...policy, trade_values: { ...policy.trade_values, [key]: event.target.value === '' ? NaN : Number(event.target.value) } })} />
              <details><summary>Saved player reference</summary><p>ESPN identifier: {key}</p></details></div>
            <button type="button" className="button secondary" onClick={() => setRemoval({ key, kind: 'value' })}>Remove value for {nameFor(key)}</button>
          </div>)}
        </section>
        <details className="card policy-advanced">
          <summary>Advanced · coach usage limits</summary>
          <p>Technical limits bound API activity, not dollar costs. Daily review limits use UTC. Your saved values are retained.</p>
          <div className="policy-limits">{numberField('max_tool_calls', 1, 100)}{numberField('max_output_tokens', 128, 32768)}
            {numberField('max_run_seconds', 10, 1800)}{numberField('max_runs_per_day', 1, 100)}</div>
        </details>
        <div className="card policy-save"><p>Review your complete draft before saving. This will not start your coach or enable real ESPN moves.</p>
          <button className="button primary" type="submit" disabled={stale}>{op.pending ? 'Saving settings…' : 'Review & save settings'}</button>
        </div>
      </fieldset>
      <OperationStatus {...op} />
    </form>
    {modal === 'preset' && <Modal title="Try these cautious settings?" onClose={() => setModal(null)} footer={<>
      <button type="button" className="button secondary" onClick={() => setModal(null)}>Keep my draft</button>
      <button type="button" className="button primary" onClick={() => { update(suggested); setModal(null) }}>Apply to draft only</button>
    </>}>
      <p>Only lineup suggestions will be allowed. All seven pickup, waiver, and trade permissions will be off. Applying this changes your draft, not your saved settings.</p>
      <ul>{ACTIONS.map(([action]) => <li key={action}>{ACTION_LABELS[action]}: {policy.allowed_actions.includes(action) ? 'Yes' : 'No'} → <strong>{suggested.allowed_actions.includes(action) ? 'Yes' : 'No'}</strong></li>)}</ul>
      <ul>{(['max_adds_per_week', 'max_drops_per_week', 'max_trades_per_week', 'max_bid'] as NumberKey[]).map(key =>
        <li key={key}>{LABELS[key]}: {policy[key]} → <strong>0</strong></li>)}</ul>
      <p>Player protections, trade values, budget, reserve, trade guardrails, and technical limits stay unchanged. You will still need to review and save.</p>
    </Modal>}
    {modal === 'save' && <Modal title="Save these coach settings?" onClose={() => { if (!op.pending) setModal(null) }} footer={<>
      <button type="button" className="button secondary" disabled={op.pending} onClick={() => setModal(null)}>Go back</button>
      <button type="button" className="button primary" disabled={!consent || op.pending || stale} onClick={() => {
        if (!consent || op.pending) return
        void op.perform(async () => {
          const error = validationError()
          if (error) throw new Error(error)
          await request('/policy', 'POST', policy)
          setModal(null)
          setConsent(false)
          setDirty(false)
          await onSaved()
        }, 'Coach settings saved.')
      }}>{op.pending ? 'Saving settings…' : 'Save coach settings'}</button>
    </>}>
      <p>These control suggestions for now. Real ESPN moves are still off. No review will start automatically.</p>
      {state.active_run && <Notice tone="info" message="Saving will cancel the active coach review so these settings can take effect." />}
      {changesFromSaved()}
      {summary(policy)}
      <label className="policy-check policy-consent"><input type="checkbox" checked={consent} disabled={op.pending} onChange={event => setConsent(event.target.checked)} />
        <span>I reviewed these settings and want to save them.</span></label>
      {op.error && <Notice message={op.error} />}
    </Modal>}
    {removal && <Modal title={removal.kind === 'protection' ? 'Remove this player’s protection?' : 'Remove this saved trade value?'} onClose={() => setRemoval(null)} footer={<>
      <button type="button" className="button secondary" onClick={() => setRemoval(null)}>Keep saved player</button>
      <button type="button" className="button primary" onClick={() => {
        if (removal.kind === 'protection') update({ ...policy, protected_player_ids: policy.protected_player_ids.filter(id => String(id) !== removal.key) })
        else { const trade_values = { ...policy.trade_values }; delete trade_values[removal.key]; update({ ...policy, trade_values }) }
        setRemoval(null)
      }}>Remove from draft</button>
    </>}>
      <p>{nameFor(removal.key)} is not in the loaded roster. We cannot confirm their identity. This removes only their {removal.kind === 'protection' ? 'protection' : 'trade value'}, and only from your draft until you save.</p>
      <details><summary>Saved player reference</summary><p>ESPN identifier: {removal.key}</p></details>
    </Modal>}
    {modal === 'reload' && <Modal title="Replace your unsaved draft?" onClose={() => setModal(null)} footer={<>
      <button type="button" className="button secondary" onClick={() => setModal(null)}>Keep my draft</button>
      <button type="button" className="button primary" onClick={() => {
        setPolicy(state.policy ?? DEFAULT_POLICY); setBaseline(serverPolicy); setDirty(false); setConsent(false); setModal(null)
      }}>Replace with saved settings</button>
    </>}><p>Your unsaved choices will be replaced by the latest saved settings. Nothing will be posted or enabled.</p></Modal>}
  </div>
}
