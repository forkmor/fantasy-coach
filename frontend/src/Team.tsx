import { useEffect, useState } from 'react'
import PlayerAvatar from './PlayerAvatar'
import PlayerProfile from './PlayerProfile'
import LineupSwap from './LineupSwap'
import RosterChange from './RosterChange'
import { asRecord, healthLabel, normalizePlayers, points, safeImageUrl, slotName, useRoster, type Player } from './players'
import type { Policy } from './types'
import { Badge, Empty, JsonDetails, Notice, SectionHeading } from './ui'

export function TeamSnapshot({ snapshot, policy, onSaved, onLineupChanged }: {
  snapshot: unknown; policy?: Policy | null; onSaved?: () => Promise<void>; onLineupChanged?: () => void
}) {
  const data = asRecord(snapshot)
  const team = asRecord(data.team)
  const roster = normalizePlayers(snapshot)
  const [selected, setSelected] = useState<Player | null>(null)
  const [filter, setFilter] = useState('all')
  const name = typeof team.name === 'string' ? team.name : 'Your team'
  const record = asRecord(team.record)
  const recordLabel = typeof record.wins === 'number' && typeof record.losses === 'number'
    ? `${record.wins}–${record.losses}${record.ties ? `–${record.ties}` : ''}` : null
  const positions = [...new Set(roster.map(player => player.position))]
  const visible = roster.filter(player => filter === 'all' || (filter === 'bench'
    ? [20, 21].includes(player.slot_id ?? -1) : filter === 'starters' ? player.slot_id !== null && ![20, 21].includes(player.slot_id) : player.position === filter))
  const warnings = Array.isArray(data.presentation_warnings) ? data.presentation_warnings.filter((value): value is string => typeof value === 'string') : []
  return <div className="team-snapshot">
    <div className="roster-team-heading"><div><h3 className="team-name">{name}</h3><p className="muted">{recordLabel ? `${recordLabel} record · ` : ''}{roster.length} players
      {typeof data.scoring_period_id === 'number' ? ` · Week ${data.scoring_period_id}` : ''}</p></div>
      <Badge tone="blue">Read-only roster</Badge></div>
    {roster.length ? <>
      <div className="roster-filters" role="group" aria-label="Filter your roster">
        {[['all', 'All players'], ['starters', 'Starters'], ['bench', 'Bench / IR'], ...positions.map(position => [position, position])].map(([value, label]) =>
          <button className={`filter-chip ${filter === value ? 'selected' : ''}`} key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}
      </div>
      <div className="player-card-grid">{visible.map(player => <button className="football-player-card" key={player.player_id}
        onClick={() => setSelected(player)} aria-label={`View ${player.name}`}>
        <div className="player-card-art">
          {safeImageUrl(player.team_logo_url) && <img className="card-team-logo" src={safeImageUrl(player.team_logo_url)} alt=""
            referrerPolicy="no-referrer" loading="lazy" onError={event => { event.currentTarget.hidden = true }} />}
          <span className="card-jersey">{player.jersey ? `#${player.jersey}` : player.position}</span>
          <PlayerAvatar player={player} large />
          <span className="lineup-chip">{slotName(player.slot_id)}</span>
        </div>
        <div className="player-card-copy"><h4>{player.name}</h4>
          <p>{player.position} · {player.pro_team_abbreviation || player.pro_team_name || 'Team unavailable'}</p>
          <div className="player-card-status"><span>{healthLabel(player.injury_status)}</span>
            {policy?.protected_player_ids.includes(player.player_id) && <span className="keeper-label">Keeper</span>}</div>
          <div className="player-card-points"><span>Projected <strong>{points(player.projected_points)}</strong></span><span>Scored <strong>{points(player.actual_points)}</strong></span></div>
          <div className="player-card-points"><span>Started <strong>{points(player.percent_started)}%</strong></span>
            <span>Rostered <strong>{points(player.percent_owned)}%</strong></span></div>
        </div>
      </button>)}</div>
      {!visible.length && <Empty title="No players in this view">Choose another position or show all players.</Empty>}
      <p className="field-hint">Tap a player for details. Headshots and team logos supplied by ESPN.</p>
    </> : <Empty title="No roster rows available">Connect ESPN or refresh your team. We will not invent players when roster data is unavailable.</Empty>}
    {warnings.length > 0 && <Notice tone="info" message={`Some player details could not be loaded: ${warnings.join(' ')}`} />}
    {data.roster_truncated === true && <Notice tone="info" message={`ESPN returned more players than this screen can safely show. The ${roster.length} visible players are not the complete roster response.`} />}
    {roster.length > 0 && <LineupSwap players={roster} enabled={Boolean(policy?.allowed_actions.includes('set_lineup'))}
      onComplete={() => { onLineupChanged?.(); void onSaved?.() }} />}
    {roster.length > 0 && <RosterChange players={roster} protectedPlayerIds={policy?.protected_player_ids ?? []}
      enabled={Boolean(policy?.allowed_actions.includes('add_drop'))}
      onComplete={() => { onLineupChanged?.(); void onSaved?.() }} />}
    <details className="technical-details"><summary>Technical details</summary>
      {data.settings !== undefined && <JsonDetails data={data.settings} label="League settings" />}
      <JsonDetails data={snapshot} label="Full normalized ESPN snapshot" />
    </details>
    {selected && <PlayerProfile player={roster.find(player => player.player_id === selected.player_id) ?? selected}
      policy={policy} onSaved={onSaved} onClose={() => setSelected(null)} />}
  </div>
}

export default function Team({ connected, policy, onSaved, onPlayers }: {
  connected: boolean; policy?: Policy | null; onSaved?: () => Promise<void>; onPlayers?: (players: Player[]) => void
}) {
  const { snapshot, players, error, loading, refresh } = useRoster(connected)
  useEffect(() => { onPlayers?.(players) }, [players, onPlayers])
  return <section className="card roster-section">
    <SectionHeading eyebrow="Your squad" title="The players you're counting on"
      action={<button className="button secondary" disabled={!connected || loading} onClick={refresh}>{loading ? 'Refreshing...' : 'Refresh team'}</button>}>
      Your lineup, familiar faces, and the numbers that matter.
    </SectionHeading>
    {!connected ? <Empty title="Connect your ESPN team">Use Sign in to ESPN in Connections. No cookie copying needed.</Empty>
      : <>{error && <Notice message={`Could not refresh your roster: ${error}${snapshot ? ' The players below are from the last successful refresh.' : ''}`} />}
        {loading && <p role="status" className="muted">Getting your team from ESPN...</p>}
        {snapshot !== null && <TeamSnapshot snapshot={snapshot} policy={policy} onSaved={onSaved} onLineupChanged={refresh} />}
        {!loading && !error && snapshot === null && <Empty title="Your roster is not available yet">Try refreshing or reconnect to ESPN.</Empty>}
      </>}
  </section>
}
