import { useEffect, useMemo, useState } from 'react'
import { errorMessage, request } from './api'
import { asRecord, points } from './players'
import { Badge, Empty, Notice, SectionHeading } from './ui'

interface Standing {
  team_id: number
  rank: number
  name: string
  abbrev: string | null
  is_my_team: boolean
  playoff_seed: number | null
  waiver_rank: number | null
  points: number | null
  record: {
    wins: number | null
    losses: number | null
    ties: number | null
    pointsFor: number | null
    pointsAgainst: number | null
  }
  transactions: {
    acquisitions: number | null
    drops: number | null
    trades: number | null
  }
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function normalizeStanding(value: unknown): Standing | null {
  const row = asRecord(value)
  const record = asRecord(row.record)
  const transactions = asRecord(row.transactions)
  const teamId = number(row.team_id)
  const rank = number(row.rank)
  if (teamId === null || rank === null || typeof row.name !== 'string') return null
  return {
    team_id: teamId, rank, name: row.name,
    abbrev: typeof row.abbrev === 'string' ? row.abbrev : null,
    is_my_team: row.is_my_team === true,
    playoff_seed: number(row.playoff_seed), waiver_rank: number(row.waiver_rank),
    points: number(row.points),
    record: {
      wins: number(record.wins), losses: number(record.losses), ties: number(record.ties),
      pointsFor: number(record.pointsFor), pointsAgainst: number(record.pointsAgainst),
    },
    transactions: {
      acquisitions: number(transactions.acquisitions), drops: number(transactions.drops),
      trades: number(transactions.trades),
    },
  }
}

function recordLabel(team: Standing) {
  const { wins, losses, ties } = team.record
  return wins === null || losses === null ? '—' : `${wins}–${losses}${ties ? `–${ties}` : ''}`
}

export default function LeagueStats({ connected }: { connected: boolean }) {
  const [data, setData] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (!connected) { setData(null); return }
    let current = true
    setLoading(true)
    setError('')
    void request('/league').then(result => { if (current) setData(result) })
      .catch(err => { if (current) setError(errorMessage(err)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [connected, version])
  const overview = asRecord(data)
  const standings = useMemo(() => (
    Array.isArray(overview.standings)
      ? overview.standings.map(normalizeStanding).filter((row): row is Standing => row !== null)
      : []
  ), [overview.standings])
  const mine = standings.find(team => team.is_my_team)
  const league = asRecord(overview.league)
  return <section className="card league-section">
    <SectionHeading eyebrow="League pulse" title="Standings & league stats"
      action={<button className="button secondary" disabled={!connected || loading}
        onClick={() => setVersion(value => value + 1)}>{loading ? 'Refreshing...' : 'Refresh league'}</button>}>
      See where your team sits and how the league is moving.
    </SectionHeading>
    {!connected ? <Empty title="Connect your ESPN team">League standings will appear after ESPN is connected.</Empty> : <>
      {error && <Notice message={`Could not refresh league stats: ${error}${data ? ' Showing the last successful refresh.' : ''}`} />}
      {mine && <div className="league-summary-grid">
        <div><span>Your rank</span><strong>#{mine.rank}</strong><small>{standings.length} teams</small></div>
        <div><span>Your record</span><strong>{recordLabel(mine)}</strong><small>Week {number(overview.scoring_period_id) ?? '—'}</small></div>
        <div><span>Points for</span><strong>{points(mine.record.pointsFor ?? mine.points)}</strong><small>Against {points(mine.record.pointsAgainst)}</small></div>
        <div><span>Waiver priority</span><strong>{mine.waiver_rank === null ? '—' : `#${mine.waiver_rank}`}</strong><small>{number(league.final_scoring_period) ?? '—'} regular scoring weeks</small></div>
      </div>}
      {standings.length > 0 ? <div className="table-scroll"><table>
        <thead><tr><th>Rank</th><th>Team</th><th>Record</th><th>PF</th><th>PA</th><th>Waiver</th><th>Moves</th></tr></thead>
        <tbody>{standings.map(team => <tr key={team.team_id} className={team.is_my_team ? 'selected' : undefined}>
          <td><strong>#{team.rank}</strong></td>
          <td><strong>{team.name}</strong>{team.is_my_team && <Badge tone="blue">Your team</Badge>}</td>
          <td>{recordLabel(team)}</td><td>{points(team.record.pointsFor ?? team.points)}</td>
          <td>{points(team.record.pointsAgainst)}</td><td>{team.waiver_rank === null ? '—' : `#${team.waiver_rank}`}</td>
          <td>{team.transactions.acquisitions ?? 0}<small>{team.transactions.trades ?? 0} trades</small></td>
        </tr>)}</tbody>
      </table></div> : !loading && !error && <Empty title="No standings available">ESPN did not return league standings yet.</Empty>}
      {loading && !data && <p role="status" className="muted">Getting league standings from ESPN...</p>}
    </>}
  </section>
}
