import { useEffect, useState } from 'react'
import { errorMessage, request } from './api'
import Modal from './Modal'
import PlayerAvatar from './PlayerAvatar'
import { asRecord, healthLabel, points, safeImageUrl, slotName, type Player } from './players'
import type { Policy } from './types'
import { Badge, Notice, OperationStatus, useOperation } from './ui'

export default function PlayerProfile({ player, policy, onSaved, onClose }: {
  player: Player; policy?: Policy | null; onSaved?: () => Promise<void>; onClose: () => void
}) {
  const [extra, setExtra] = useState<Record<string, unknown>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [confirmProtection, setConfirmProtection] = useState(false)
  const op = useOperation()
  useEffect(() => {
    let current = true
    setExtra({})
    setLoading(true)
    setError('')
    void request(`/players/${player.player_id}`).then(value => {
      const data = asRecord(value)
      if (data.player_id !== player.player_id) throw new Error('Player details did not match the selected player.')
      if (current) setExtra(data)
    }).catch(err => { if (current) setError(errorMessage(err)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [player.player_id])
  const metadata = (field: string, existing?: string | null) =>
    field in extra ? typeof extra[field] === 'string' ? extra[field] as string : null : existing
  const displayPlayer: Player = {
    ...player,
    jersey: metadata('jersey', player.jersey),
    pro_team_name: metadata('pro_team_name', player.pro_team_name),
    pro_team_abbreviation: metadata('pro_team_abbreviation', player.pro_team_abbreviation),
    headshot_url: safeImageUrl(metadata('headshot_url', player.headshot_url)),
    team_logo_url: safeImageUrl(metadata('team_logo_url', player.team_logo_url)),
  }
  const protectedPlayer = Boolean(policy?.protected_player_ids.includes(player.player_id))
  const facts = Array.isArray(extra.facts) ? extra.facts.map(asRecord)
    .filter(fact => typeof fact.label === 'string' && ['string', 'number'].includes(typeof fact.value)) : []
  const warnings = Array.isArray(extra.presentation_warnings)
    ? extra.presentation_warnings.filter((value): value is string => typeof value === 'string') : []
  const stats = asRecord(extra.stats)
  const regularSeason = asRecord(stats.regular_season)
  const news = Array.isArray(extra.news) ? extra.news.map(asRecord)
    .filter(item => typeof item.headline === 'string') : []
  const injuries = Array.isArray(extra.injuries) ? extra.injuries.map(asRecord)
    .filter(item => typeof item.status === 'string' || typeof item.detail === 'string') : []
  const sourceUrl = player.player_id > 0 ? `https://www.espn.com/nfl/player/_/id/${player.player_id}`
    : player.pro_team_id !== null && player.pro_team_id > 0 ? `https://www.espn.com/nfl/team/_/id/${player.pro_team_id}`
      : 'https://www.espn.com/nfl/teams'
  return <Modal title={player.name} onClose={() => { if (!op.pending) onClose() }} footer={
    <><a className="button secondary" href={sourceUrl} target="_blank" rel="noopener noreferrer">More on ESPN ↗</a>
      <button className="button primary" disabled={op.pending} onClick={onClose}>Done</button></>
  }>
    <div className="player-profile-hero">
      <div><p className="eyebrow">{displayPlayer.pro_team_name || displayPlayer.pro_team_abbreviation || 'NFL player'}</p>
        <div className="player-profile-number">{displayPlayer.jersey ? `#${displayPlayer.jersey}` : player.position}</div>
        <div className="button-row"><Badge tone="blue">{player.position}</Badge><Badge>{healthLabel(player.injury_status)}</Badge>
          {protectedPlayer && <Badge tone="green">Keep on my team</Badge>}</div>
      </div>
      <PlayerAvatar player={displayPlayer} large />
    </div>
    <dl className="player-stat-strip">
      <div><dt>This week projected</dt><dd>{points(player.projected_points)} <small>pts</small></dd></div>
      <div><dt>This week scored</dt><dd>{points(player.actual_points)} <small>pts</small></dd></div>
      <div><dt>Your lineup</dt><dd>{slotName(player.slot_id)}</dd></div>
    </dl>
    <p className="field-hint">Projections are estimates, not guarantees. A dash means ESPN has not supplied that value.</p>
    {loading && <p role="status" className="muted">Loading player details...</p>}
    {error && <Notice message={`Extra player details are unavailable: ${error}. Your roster information is still shown above.`} />}
    {warnings.length > 0 && <Notice tone="info" message={warnings.join(' ')} />}
    {facts.length > 0 && <dl className="team-facts">{facts.map((fact, index) =>
      <div key={index}><dt>{String(fact.label)}</dt><dd>{String(fact.value)}</dd></div>)}</dl>}
    {Object.keys(regularSeason).length > 0 && <section className="player-research-section">
      <h3>{typeof stats.label === 'string' ? stats.label : 'Current NFL stats'}</h3>
      <dl className="team-facts">{Object.entries(regularSeason).map(([label, value]) =>
        <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
    </section>}
    {injuries.length > 0 && <section className="player-research-section"><h3>Injury and practice report</h3>
      {injuries.map((injury, index) => <article className="player-news-item" key={index}>
        <strong>{String(injury.status ?? injury.type ?? 'Update')}</strong>
        {typeof injury.practice_status === 'string' && <span>{injury.practice_status}</span>}
        <p>{String(injury.detail ?? injury.short_comment ?? 'ESPN did not include a detailed note.')}</p>
        {typeof injury.date === 'string' && <small>{new Date(injury.date).toLocaleString()}</small>}
      </article>)}</section>}
    {news.length > 0 && <section className="player-research-section"><h3>Latest ESPN news</h3>
      {news.map((item, index) => <article className="player-news-item" key={index}>
        {typeof item.url === 'string' ? <a href={item.url} target="_blank" rel="noopener noreferrer"><strong>{String(item.headline)}</strong></a>
          : <strong>{String(item.headline)}</strong>}
        {typeof item.description === 'string' && <p>{item.description}</p>}
        {typeof item.published_at === 'string' && <small>{new Date(item.published_at).toLocaleString()}</small>}
      </article>)}</section>}
    {!loading && !error && news.length === 0 && injuries.length === 0
      && <p className="field-hint">ESPN has no current news or injury report for this player.</p>}
    <div className="player-protection-box">
      <h3>{protectedPlayer ? 'A keeper on your roster' : 'Make this player a keeper'}</h3>
      <p>{protectedPlayer ? 'Your coach is not allowed to suggest dropping or trading this player away.'
        : 'Keep this player out of drop and outgoing trade suggestions. This changes your coach settings, not your ESPN roster.'}</p>
      {!policy || !onSaved ? <p className="field-hint">Save your coach settings first to choose which players to keep.</p>
        : !confirmProtection ? <button className="button secondary" onClick={() => setConfirmProtection(true)}>
          {protectedPlayer ? 'Change keeper preference' : 'Keep on my team'}
        </button> : <div className="inline-confirm">
          <p><strong>{protectedPlayer ? `Allow suggestions to drop or trade ${player.name}?` : `Keep ${player.name} out of drop and trade suggestions?`}</strong></p>
          <p className="field-hint">Saving stops any current team review so the updated rules take effect.</p>
          <div className="button-row"><button className="button primary" disabled={op.pending} onClick={() => {
            void op.perform(async () => {
              const ids = protectedPlayer ? policy.protected_player_ids.filter(id => id !== player.player_id)
                : [...policy.protected_player_ids, player.player_id]
              await request('/policy', 'POST', { ...policy, protected_player_ids: ids })
              await onSaved()
              setConfirmProtection(false)
            }, 'Keeper preference saved.')
          }}>{op.pending ? 'Saving...' : 'Save preference'}</button>
            <button className="button secondary" disabled={op.pending} onClick={() => setConfirmProtection(false)}>Go back</button></div>
        </div>}
      <OperationStatus {...op} />
    </div>
    <p className="field-hint">Player facts and images: ESPN. Jersey numbers are shown only when supplied by the player source.</p>
    <details className="technical-details"><summary>Saved player reference</summary><p>ESPN player ID: {player.player_id}</p></details>
  </Modal>
}
