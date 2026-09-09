import { useState } from 'react'
import { errorMessage, request } from './api'
import Modal from './Modal'
import { healthLabel, normalizePlayers, points, type Player } from './players'
import { Notice } from './ui'

interface RosterChangePreview {
  operation_id: string
  status: string
  scoring_period_id: number
  operation: 'add' | 'drop'
  add?: { player_id: number; name: string }
  drop?: { player_id: number; name: string }
}

export default function RosterChange({ players, protectedPlayerIds, enabled, onComplete }: {
  players: Player[]
  protectedPlayerIds: number[]
  enabled: boolean
  onComplete: () => void
}) {
  const [available, setAvailable] = useState<Player[]>([])
  const [operation, setOperation] = useState<'add' | 'drop'>('add')
  const [playerId, setPlayerId] = useState<number | null>(null)
  const [preview, setPreview] = useState<RosterChangePreview | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  async function loadAvailable() {
    setPending(true); setError('')
    try {
      const result = await request<unknown>('/players/available?limit=50')
      setAvailable(normalizePlayers({ roster: (result as { players?: unknown }).players }))
    } catch (err) { setError(errorMessage(err)) }
    finally { setPending(false) }
  }

  async function prepare() {
    if (playerId === null) return
    setPending(true); setError(''); setSuccess('')
    try {
      setPreview(await request<RosterChangePreview>('/roster/change/preview', 'POST',
        operation === 'add' ? { add_player_id: playerId } : { drop_player_id: playerId }))
    } catch (err) { setError(errorMessage(err)) }
    finally { setPending(false) }
  }

  async function confirm() {
    if (!preview) return
    setPending(true); setError('')
    try {
      const result = await request<{ status: string; detail?: string }>('/roster/change/confirm', 'POST', {
        operation_id: preview.operation_id,
      })
      setPreview(null)
      if (result.status === 'completed') {
        setSuccess(`ESPN confirmed the ${operation}. Your roster has been refreshed.`)
        setPlayerId(null); onComplete()
      } else {
        setError(result.detail ?? 'The result is unknown. Check ESPN before trying another roster change.')
      }
    } catch (err) { setError(errorMessage(err)) }
    finally { setPending(false) }
  }

  const choices = operation === 'add'
    ? available.filter(player => player.availability_status === 'FREEAGENT')
    : players.filter(player => player.droppable === true
      && !protectedPlayerIds.includes(player.player_id))
  const selected = choices.find(player => player.player_id === playerId)

  return <div className="lineup-manager">
    <div><p className="eyebrow">Direct roster control</p><h4>Add or drop one player</h4>
      <p className="muted">Fieldhouse checks fresh ESPN data, asks you to confirm the named player, submits once, and verifies the roster afterward.</p></div>
    {!enabled && <Notice tone="info" message="Enable adds and drops in Coach settings before preparing a live roster change." />}
    <div className="lineup-swap-form">
      <label>Roster action<select aria-label="Roster action" value={operation} disabled={!enabled || pending}
        onChange={event => { setOperation(event.target.value as 'add' | 'drop'); setPlayerId(null); setError(''); setSuccess('') }}>
        <option value="add">Add a free agent</option>
        <option value="drop">Drop a roster player</option>
      </select></label>
      <label>Player<select aria-label="Roster change player" value={playerId ?? ''} disabled={!enabled || pending}
        onChange={event => setPlayerId(event.target.value ? Number(event.target.value) : null)}>
        <option value="">{operation === 'add' ? 'Choose a loaded free agent' : 'Choose a player to drop'}</option>
        {choices.map(player => <option key={player.player_id} value={player.player_id}>
          {player.name} · {player.position} · Proj {points(player.projected_points)} · {healthLabel(player.injury_status)}
          {typeof player.percent_owned === 'number' ? ` · ${player.percent_owned.toFixed(1)}% rostered` : ''}
        </option>)}
      </select></label>
      {operation === 'add' && <button className="button secondary" disabled={!enabled || pending}
        onClick={() => { void loadAvailable() }}>{pending ? 'Checking ESPN...' : 'Load free agents'}</button>}
      <button className="button primary" disabled={!enabled || pending || playerId === null}
        onClick={() => { void prepare() }}>{pending ? 'Checking ESPN...' : `Review live ${operation}`}</button>
    </div>
    {operation === 'add' && choices.length === 0 && <p className="field-hint">Load ESPN's current top 50 available players before choosing an add. Players on waivers are excluded here.</p>}
    {selected && <dl className="player-stat-strip compact">
      <div><dt>Projected</dt><dd>{points(selected.projected_points)} <small>pts</small></dd></div>
      <div><dt>Health</dt><dd>{healthLabel(selected.injury_status)}</dd></div>
      <div><dt>Started</dt><dd>{points(selected.percent_started)}<small>%</small></dd></div>
      <div><dt>Rostered</dt><dd>{points(selected.percent_owned)}<small>%</small></dd></div>
    </dl>}
    {error && <Notice message={error} />}
    {success && <Notice tone="success" message={success} />}
    {preview && selected && <Modal title={`Confirm this ESPN ${preview.operation}`} onClose={() => { if (!pending) setPreview(null) }}
      footer={<><button className="button secondary" disabled={pending} onClick={() => setPreview(null)}>Go back</button>
        <button className="button primary" disabled={pending} onClick={() => { void confirm() }}>
          {pending ? 'Submitting once...' : `Confirm and ${preview.operation} ${selected.name}`}
        </button></>}>
      <p>This will make a real roster change for Week {preview.scoring_period_id}.</p>
      <div className="swap-preview"><div><span>{preview.operation === 'add' ? 'Add free agent' : 'Drop from roster'}</span>
        <strong>{selected.name}</strong><small>{selected.position}</small></div></div>
      <Notice tone="info" message="Only this named player will be added or dropped. Fieldhouse will not retry automatically if ESPN's result is uncertain." />
    </Modal>}
  </div>
}
