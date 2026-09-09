import { useEffect, useMemo, useState } from 'react'
import { errorMessage, request } from './api'

export interface Player {
  player_id: number
  name: string
  position: string
  slot_id: number | null
  eligible_slots: number[]
  injury_status: string
  pro_team_id: number | null
  projected_points: number | null
  actual_points: number | null
  locked?: boolean | null
  roster_locked?: boolean | null
  lock_status?: string
  droppable?: boolean | null
  availability_status?: string
  percent_owned?: number | null
  percent_started?: number | null
  average_draft_position?: number | null
  last_news_at?: string | null
  season_outlook?: string | null
  jersey?: string | null
  pro_team_name?: string | null
  pro_team_abbreviation?: string | null
  headshot_url?: string | null
  team_logo_url?: string | null
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}
function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export const SLOT_NAMES: Record<number, string> = {
  0: 'QB', 1: 'QB / RB', 2: 'RB', 3: 'RB / WR', 4: 'WR', 5: 'WR / TE', 6: 'TE', 7: 'Superflex',
  8: 'DT', 9: 'DE', 10: 'LB', 11: 'DL', 12: 'CB', 13: 'S', 14: 'DB', 15: 'IDP',
  16: 'D/ST', 17: 'K', 18: 'P', 19: 'Head coach', 20: 'Bench', 21: 'IR', 23: 'Flex',
}
export function slotName(slot: number | null): string {
  return slot === null ? 'Not available' : SLOT_NAMES[slot] ?? 'Other slot'
}
export function healthLabel(status: string): string {
  if (status === 'ACTIVE' || status === 'HEALTHY') return 'Healthy'
  if (status === 'QUESTIONABLE') return 'Questionable'
  if (status === 'OUT') return 'Out'
  if (status === 'INJURY_RESERVE') return 'Injured reserve'
  if (!status || status === 'UNKNOWN') return 'Status unavailable'
  return status.toLowerCase().replaceAll('_', ' ')
}
export function points(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}
export function safeImageUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && ['a.espncdn.com', 'a1.espncdn.com', 'a2.espncdn.com'].includes(url.hostname)
      && !url.username && !url.password && !url.port ? url.href : undefined
  } catch { return undefined }
}

export function normalizePlayers(snapshot: unknown): Player[] {
  const data = asRecord(snapshot)
  const team = asRecord(data.team)
  const entries = Array.isArray(data.roster) ? data.roster
    : Array.isArray(asRecord(data.roster).entries) ? asRecord(data.roster).entries as unknown[]
      : Array.isArray(team.roster) ? team.roster : []
  const players: Player[] = []
  for (const entry of entries) {
    const row = asRecord(entry)
    const nested = asRecord(row.player)
    const pooled = asRecord(asRecord(row.playerPoolEntry).player)
    const source = Object.keys(nested).length ? nested : Object.keys(pooled).length ? pooled : row
    const id = number(row.player_id ?? source.player_id ?? source.id)
    const name = text(source.name ?? source.full_name ?? source.fullName ?? row.player_name)
    if (id === null || !Number.isSafeInteger(id) || id === 0 || !name) continue
    const jersey = source.jersey ?? row.jersey
    const eligible = source.eligible_slots ?? source.eligibleSlots
    const rawSlot = row.slot_id ?? row.lineupSlotId ?? row.lineup_slot
    const namedSlot = typeof row.slot === 'string'
      ? Object.entries(SLOT_NAMES).find(([, label]) => label === row.slot)?.[0] : undefined
    players.push({
      player_id: id, name, position: text(source.position ?? row.position) ?? 'Unknown',
      slot_id: number(rawSlot) ?? (namedSlot ? Number(namedSlot) : null),
      eligible_slots: Array.isArray(eligible) ? eligible.filter((item): item is number => typeof item === 'number') : [],
      injury_status: text(source.injury_status ?? source.injuryStatus) ?? 'UNKNOWN',
      pro_team_id: number(source.pro_team_id ?? source.proTeamId),
      projected_points: number(source.projected_points), actual_points: number(source.actual_points),
      locked: typeof source.locked === 'boolean' ? source.locked : null,
      roster_locked: typeof source.roster_locked === 'boolean' ? source.roster_locked : null,
      lock_status: text(source.lock_status) ?? 'UNKNOWN',
      droppable: typeof source.droppable === 'boolean' ? source.droppable : null,
      availability_status: text(source.availability_status) ?? 'UNKNOWN',
      percent_owned: number(source.percent_owned),
      percent_started: number(source.percent_started),
      average_draft_position: number(source.average_draft_position),
      last_news_at: text(source.last_news_at),
      season_outlook: text(source.season_outlook),
      jersey: typeof jersey === 'number' ? String(jersey) : text(jersey),
      pro_team_name: text(source.pro_team_name ?? source.pro_team),
      pro_team_abbreviation: text(source.pro_team_abbreviation ?? source.pro_team),
      headshot_url: safeImageUrl(text(source.headshot_url)),
      team_logo_url: safeImageUrl(text(source.team_logo_url)),
    })
  }
  return players
}

export function useRoster(connected: boolean) {
  const [snapshot, setSnapshot] = useState<unknown>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (!connected) { setSnapshot(null); return }
    let current = true
    setLoading(true)
    setError('')
    void request('/team').then(result => { if (current) setSnapshot(result) })
      .catch(err => { if (current) setError(errorMessage(err)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [connected, version])
  const players = useMemo(() => normalizePlayers(snapshot), [snapshot])
  return { snapshot, players, loading, error, refresh: () => setVersion(value => value + 1) }
}
