import type { ActionIntent } from './types'
import { asRecord, slotName, type Player } from './players'
import PlayerAvatar from './PlayerAvatar'
import Modal from './Modal'
import { JsonDetails, Notice } from './ui'
import AdviceText, { withPlayerNames } from './AdviceText'

const TITLES: Record<string, string> = {
  set_lineup: 'A lineup idea', add_drop: 'A pickup idea', waiver_submit: 'A waiver idea',
  waiver_cancel: 'Reconsider a waiver claim', trade_propose: 'A trade idea',
  trade_accept: 'Review a trade offer', trade_reject: 'Pass on a trade offer', trade_cancel: 'Withdraw a trade idea',
}
export function suggestionTitle(intent: ActionIntent) {
  return TITLES[intent.action ?? ''] ?? 'A suggestion for your team'
}
export function suggestionSummary(intent: ActionIntent, players: Player[]) {
  const payload = asRecord(intent.payload)
  const names = new Map(players.map(player => [player.player_id, player.name]))
  const name = (id: unknown) => typeof id === 'number' ? names.get(id) ?? 'a player outside this roster' : 'a player'
  if (intent.action === 'add_drop' || intent.action === 'waiver_submit') {
    return `${payload.add_player_id ? `Add ${name(payload.add_player_id)}` : ''}${payload.add_player_id && payload.drop_player_id ? '; ' : ''}${payload.drop_player_id ? `drop ${name(payload.drop_player_id)}` : ''}`
  }
  if (intent.action === 'set_lineup' && Array.isArray(payload.moves)) return payload.moves.map(value => {
    const move = asRecord(value)
    return `${name(move.player_id)} at ${slotName(typeof move.slot_id === 'number' ? move.slot_id : null)}`
  }).join(' · ')
  return typeof payload.reason === 'string' ? withPlayerNames(payload.reason, players) : 'Review the details before making any changes.'
}

export default function SuggestionReview({ intent, players, onClose, onNext }: {
  intent: ActionIntent; players: Player[]; onClose: () => void; onNext?: () => void
}) {
  const payload = asRecord(intent.payload)
  const result = asRecord(intent.result)
  const ids = new Set<number>()
  for (const value of [payload.add_player_id, payload.drop_player_id]) if (typeof value === 'number') ids.add(value)
  for (const key of ['outgoing_player_ids', 'incoming_player_ids']) {
    if (Array.isArray(payload[key])) for (const id of payload[key]) if (typeof id === 'number') ids.add(id)
  }
  if (Array.isArray(payload.moves)) for (const value of payload.moves) {
    const move = asRecord(value)
    if (typeof move.player_id === 'number') ids.add(move.player_id)
  }
  const involved = players.filter(player => ids.has(player.player_id))
  const denials = Array.isArray(result.denials) ? result.denials.filter((value): value is string => typeof value === 'string') : []
  return <Modal title={suggestionTitle(intent)} onClose={onClose} footer={<>
    <button className="button secondary" onClick={onClose}>Done for now</button>
    {onNext && <button className="button primary" onClick={onNext}>Next suggestion</button>}
  </>}>
    {involved.length > 0 && <div className="suggested-player-list">{involved.map(player => <div key={player.player_id}>
      <PlayerAvatar player={player} /><span><strong>{player.name}</strong><small>{player.position}{player.jersey ? ` · #${player.jersey}` : ''} · {player.pro_team_abbreviation || 'NFL'}</small></span>
    </div>)}</div>}
    <h3>{suggestionSummary(intent, players)}</h3>
    {typeof payload.reason === 'string' && <AdviceText text={payload.reason} players={players} />}
    {typeof payload.bid === 'number' && <p><strong>Suggested waiver bid:</strong> {payload.bid} budget dollars</p>}
    {denials.length > 0 && <Notice message={`This idea is outside your current rules: ${denials.join(' ')}`} />}
    <div className="recommendation-reminder"><strong>{intent.status === 'completed' ? 'This move was verified on ESPN.'
      : intent.status === 'unknown' ? 'Check ESPN before taking another action.'
        : 'Nothing has changed on ESPN.'}</strong>
      <p>{intent.status === 'completed' ? 'Fieldhouse submitted the permitted move and verified the resulting roster.'
        : intent.status === 'unknown' ? 'ESPN did not provide a definitive result. Fieldhouse did not retry the mutation.'
          : 'This is an idea to consider, not an approved or completed move. Player availability and league rules still need checking.'}</p>
      <a className="button secondary" href="https://fantasy.espn.com/football/team?leagueId=656212638&teamId=4&seasonId=2026"
        target="_blank" rel="noopener noreferrer">Check my team on ESPN ↗</a></div>
    <details className="technical-details"><summary>Checks and technical details</summary><JsonDetails data={intent} label="Saved suggestion data" /></details>
  </Modal>
}
