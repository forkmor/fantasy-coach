import { useMemo, useState } from 'react'
import { errorMessage, request } from './api'
import Modal from './Modal'
import { healthLabel, points, slotName, type Player } from './players'
import { Notice } from './ui'

interface SwapPreview {
  operation_id: string
  status: string
  scoring_period_id: number
  starter: { player_id: number; name: string; from_slot_id: number; to_slot_id: number }
  bench: { player_id: number; name: string; from_slot_id: number; to_slot_id: number }
}

export default function LineupSwap({ players, enabled, onComplete }: {
  players: Player[]; enabled: boolean; onComplete: () => void
}) {
  const starters = useMemo(() => players.filter(player => player.slot_id !== null && ![20, 21].includes(player.slot_id)), [players])
  const [starterId, setStarterId] = useState<number | null>(null)
  const [benchId, setBenchId] = useState<number | null>(null)
  const [preview, setPreview] = useState<SwapPreview | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const starter = starters.find(player => player.player_id === starterId)
  const replacements = players.filter(player => player.slot_id === 20
    && starter?.slot_id !== null && starter?.slot_id !== undefined
    && player.eligible_slots.includes(starter.slot_id))

  async function prepare() {
    if (starterId === null || benchId === null) return
    setPending(true); setError(''); setSuccess('')
    try {
      setPreview(await request<SwapPreview>('/lineup/swap/preview', 'POST', {
        starter_player_id: starterId, bench_player_id: benchId,
      }))
    } catch (err) { setError(errorMessage(err)) }
    finally { setPending(false) }
  }

  async function confirm() {
    if (!preview) return
    setPending(true); setError('')
    try {
      const result = await request<{ status: string; detail?: string }>('/lineup/swap/confirm', 'POST', {
        operation_id: preview.operation_id,
      })
      setPreview(null)
      if (result.status === 'completed') {
        setSuccess('ESPN confirmed the lineup swap. Your roster has been refreshed.')
        setStarterId(null); setBenchId(null); onComplete()
      } else {
        setError(result.detail ?? 'The result is unknown. Check ESPN before trying another lineup move.')
      }
    } catch (err) { setError(errorMessage(err)) }
    finally { setPending(false) }
  }

  return <div className="lineup-manager">
    <div><p className="eyebrow">Direct lineup control</p><h4>Swap a starter with your bench</h4>
      <p className="muted">Choose the exact two players. Nothing changes until you review and confirm the named swap.</p></div>
    {!enabled && <Notice tone="info" message="Enable lineup changes in Coach settings before preparing a live swap." />}
    <div className="lineup-swap-form">
      <label>Move to bench<select aria-label="Player to move to bench" value={starterId ?? ''}
        disabled={!enabled || pending} onChange={event => {
          setStarterId(event.target.value ? Number(event.target.value) : null)
          setBenchId(null); setError(''); setSuccess('')
        }}>
        <option value="">Choose a starter</option>
        {starters.map(player => <option key={player.player_id} value={player.player_id}>
          {player.name} · {slotName(player.slot_id)} · Proj {points(player.projected_points)} · {healthLabel(player.injury_status)}
        </option>)}
      </select></label>
      <label>Move into lineup<select aria-label="Bench player to start" value={benchId ?? ''}
        disabled={!enabled || pending || starterId === null} onChange={event => setBenchId(event.target.value ? Number(event.target.value) : null)}>
        <option value="">Choose an eligible bench player</option>
        {replacements.map(player => <option key={player.player_id} value={player.player_id}>
          {player.name} · {player.position} · Proj {points(player.projected_points)} · {healthLabel(player.injury_status)}
        </option>)}
      </select></label>
      <button className="button primary" disabled={!enabled || pending || starterId === null || benchId === null}
        onClick={() => { void prepare() }}>{pending && !preview ? 'Checking ESPN...' : 'Review live swap'}</button>
    </div>
    {starterId !== null && replacements.length === 0 && <Notice tone="info" message="No bench player is eligible for that starter's exact lineup slot." />}
    {error && <Notice message={error} />}
    {success && <Notice tone="success" message={success} />}
    {preview && <Modal title="Confirm this ESPN lineup swap" onClose={() => { if (!pending) setPreview(null) }}
      footer={<><button className="button secondary" disabled={pending} onClick={() => setPreview(null)}>Go back</button>
        <button className="button primary" disabled={pending} onClick={() => { void confirm() }}>
          {pending ? 'Submitting once...' : 'Confirm and change ESPN lineup'}
        </button></>}>
      <p>This will make a real change to your ESPN team for Week {preview.scoring_period_id}.</p>
      <div className="swap-preview">
        <div><span>Move to bench</span><strong>{preview.starter.name}</strong>
          <small>{slotName(preview.starter.from_slot_id)} → Bench · Projected {points(starter?.projected_points)} pts</small></div>
        <div><span>Move into lineup</span><strong>{preview.bench.name}</strong>
          <small>Bench → {slotName(preview.bench.to_slot_id)} · Projected {points(replacements.find(player => player.player_id === preview.bench.player_id)?.projected_points)} pts</small></div>
      </div>
      <Notice tone="info" message="Fieldhouse will submit this exact two-player swap once, then re-read ESPN to verify both slots. An uncertain result will never be retried automatically." />
    </Modal>}
  </div>
}
