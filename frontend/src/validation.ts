import type { AppState, Policy } from './types'

export const ACTIONS = [
  ['set_lineup', 'Set lineup'],
  ['add_drop', 'Add / drop'],
  ['waiver_submit', 'Submit waiver'],
  ['waiver_cancel', 'Cancel waiver'],
  ['trade_propose', 'Propose trade'],
  ['trade_accept', 'Accept trade'],
  ['trade_reject', 'Reject trade'],
  ['trade_cancel', 'Cancel trade'],
] as const

export const DEFAULT_POLICY: Policy = {
  allowed_actions: ['set_lineup'],
  protected_player_ids: [],
  max_adds_per_week: 0,
  max_drops_per_week: 0,
  max_trades_per_week: 0,
  max_bid: 0,
  waiver_budget: 0,
  waiver_reserve: 0,
  max_trade_players: 2,
  max_trade_value_loss_pct: 0,
  trade_values: {},
  max_tool_calls: 12,
  max_output_tokens: 2048,
  max_run_seconds: 120,
  max_runs_per_day: 4,
}

export function parsePlayerIds(value: string): number[] {
  if (!value.trim()) return []
  const parts = value.trim().split(/[\s,]+/)
  if (parts.some(part => !/^-?\d+$/.test(part) || !Number.isSafeInteger(Number(part)) || Number(part) === 0)) {
    throw new Error('Protected players must be nonzero ESPN player IDs, separated by commas or spaces. D/ST IDs may be negative.')
  }
  return [...new Set(parts.map(Number))]
}

export function parseTradeValues(value: string): Record<string, number> {
  let parsed: unknown
  try { parsed = JSON.parse(value) } catch { throw new Error('Trade values must be valid JSON, for example {"12345": 25}.') }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Trade values must be a JSON object mapping player IDs to non-negative numbers.')
  }
  for (const [key, amount] of Object.entries(parsed)) {
    if (!/^-?[1-9]\d*$/.test(key) || !Number.isSafeInteger(Number(key)) ||
        typeof amount !== 'number' || !Number.isFinite(amount) || amount < 0) {
      throw new Error('Each trade value must have a nonzero ESPN player ID and a finite, non-negative numeric value. D/ST IDs may be negative.')
    }
  }
  return parsed as Record<string, number>
}

export function validatePolicy(policy: Policy): string | null {
  for (const [key, value] of Object.entries(policy)) {
    if (typeof value !== 'number') continue
    if (!Number.isFinite(value) || value < 0) return `${key.replaceAll('_', ' ')} must be a non-negative number.`
    if (key !== 'max_trade_value_loss_pct' && !Number.isSafeInteger(value)) {
      return `${key.replaceAll('_', ' ')} must be a whole number.`
    }
  }
  if (policy.max_trade_value_loss_pct > 100) return 'Maximum trade value loss must be between 0 and 100 percent.'
  if (policy.waiver_reserve > policy.waiver_budget) return 'Waiver reserve cannot exceed the waiver budget.'
  if (policy.max_bid > policy.waiver_budget - policy.waiver_reserve) return 'Maximum bid cannot exceed budget minus the reserve.'
  const bounds: { key: keyof Policy; label: string; min: number; max: number }[] = [
    { key: 'max_adds_per_week', label: 'Weekly pickups', min: 0, max: 100 },
    { key: 'max_drops_per_week', label: 'Weekly drops', min: 0, max: 100 },
    { key: 'max_trades_per_week', label: 'Weekly trades', min: 0, max: 50 },
    { key: 'waiver_budget', label: 'Waiver budget', min: 0, max: 100000 },
    { key: 'waiver_reserve', label: 'Waiver reserve', min: 0, max: 100000 },
    { key: 'max_bid', label: 'Maximum waiver bid', min: 0, max: 100000 },
    { key: 'max_trade_players', label: 'Players in a trade', min: 0, max: 20 },
    { key: 'max_tool_calls', label: 'Checks per review', min: 1, max: 100 },
    { key: 'max_output_tokens', label: 'Response token limit', min: 128, max: 32768 },
    { key: 'max_run_seconds', label: 'Review time limit', min: 10, max: 1800 },
    { key: 'max_runs_per_day', label: 'Reviews per day', min: 1, max: 100 },
  ]
  for (const { key, label, min, max } of bounds) {
    const value = policy[key]
    if (typeof value === 'number' && value < min) return `${label} must be at least ${min}.`
    if (typeof value === 'number' && value > max) return `${label} must be no more than ${max}.`
  }
  if (policy.allowed_actions.some(action => !ACTIONS.some(([known]) => action === known))) return 'Policy includes an unknown action.'
  return null
}

export function setupIssues(state: AppState): string[] {
  const issues: string[] = []
  if (!state.credentials[state.settings.provider]) issues.push(`Connect ${state.settings.provider === 'grok' ? 'Grok' : 'Gemini'} to power your coach`)
  if (!state.credentials.espn_s2 || !state.credentials.swid) issues.push('Sign in to ESPN to load your team')
  if (!state.settings.model.trim()) issues.push('Choose your coach model in Connections')
  if (!state.policy) issues.push('Choose and save your coach settings')
  if (state.espn_login?.active) issues.push('Finish signing in to ESPN')
  return issues
}

export function validTimezone(value: string): boolean {
  try { new Intl.DateTimeFormat('en', { timeZone: value }); return Boolean(value.trim()) } catch { return false }
}
