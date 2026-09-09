export type Provider = 'grok' | 'gemini'
export type CredentialName = Provider | 'espn_s2' | 'swid'
export type Mode = 'dry_run' | 'live'
export interface Settings { provider: Provider; model: string; mode: Mode; deep_research: boolean; paused: boolean }
export interface Policy {
  allowed_actions: string[]
  protected_player_ids: number[]
  max_adds_per_week: number
  max_drops_per_week: number
  max_trades_per_week: number
  max_bid: number
  waiver_budget: number
  waiver_reserve: number
  max_trade_players: number
  max_trade_value_loss_pct: number
  trade_values: Record<string, number>
  max_tool_calls: number
  max_output_tokens: number
  max_run_seconds: number
  max_runs_per_day: number
}
export interface Schedule {
  enabled: boolean
  timezone: string
  interval_minutes: number
  next_run_at: string | null
}
export interface Run {
  id: string
  status: string
  provider: string
  model: string
  mode: Mode
  started_at: string
  finished_at?: string | null
  summary?: string | null
  error?: string | null
  usage?: unknown
}
export interface RunEvent { id: string; created_at: string; kind: string; data: unknown }
export interface ActivityEvent extends RunEvent { run_id: string | null }
export interface RunDetail extends Run { events: RunEvent[] }
export interface ActionIntent {
  id: string
  action?: string
  status?: string
  created_at?: string
  result?: unknown
  [key: string]: unknown
}
export interface EspnLoginState {
  status: 'idle' | 'opening' | 'waiting' | 'verifying' | 'connected' | 'cancelled' | 'expired' | 'failed'
  detail: string
  active: boolean
}
export interface ManagerProfile {
  team_id: number
  team_name: string
  league_id: number
  season: number
}
export interface OnboardingState {
  onboarding_required: true
  manager: null
  league: { league_id: number; season: number }
}
export interface AppState {
  onboarding_required?: false
  manager?: ManagerProfile
  settings: Settings
  credentials: Record<CredentialName, boolean>
  browser_automation_ready?: boolean
  browser_login_available?: boolean
  policy: Policy | null
  schedule: Schedule
  capabilities: { action: string; enabled: boolean; reason: string }[]
  active_run: Run | null
  espn_login?: EspnLoginState
}
