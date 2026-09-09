import { describe, expect, it } from 'vitest'
import { DEFAULT_POLICY, parsePlayerIds, parseTradeValues, setupIssues, validatePolicy, validTimezone } from './validation'
import type { AppState } from './types'

describe('owner policy validation', () => {
  it('parses and deduplicates protected player IDs', () => {
    expect(parsePlayerIds('12, 34\n12 -16')).toEqual([12, 34, -16])
    expect(parsePlayerIds('')).toEqual([])
    for (const value of ['abc', '0', '-0', '1.5', '9007199254740992']) {
      expect(() => parsePlayerIds(value)).toThrow()
    }
  })
  it('accepts only finite non-negative player value maps', () => {
    expect(parseTradeValues('{"12":25,"-16":0}')).toEqual({ '12': 25, '-16': 0 })
    expect(parseTradeValues('{}')).toEqual({})
    for (const value of ['null', '[]', '"string"', '{"12":-1}', '{"12":"5"}', '{"name":5}', '{"12":1e999}', '{"0":1}', '{']) {
      expect(() => parseTradeValues(value)).toThrow()
    }
  })
  it('validates budget, percentages, integer caps, and required agent limits', () => {
    expect(validatePolicy(DEFAULT_POLICY)).toBeNull()
    expect(validatePolicy({ ...DEFAULT_POLICY, max_trade_players: 0 })).toBeNull()
    expect(validatePolicy({ ...DEFAULT_POLICY, waiver_budget: 10, waiver_reserve: 11 })).toMatch(/reserve/)
    expect(validatePolicy({ ...DEFAULT_POLICY, waiver_budget: 10, waiver_reserve: 5, max_bid: 6 })).toMatch(/bid/)
    expect(validatePolicy({ ...DEFAULT_POLICY, max_trade_value_loss_pct: 101 })).toMatch(/percent/)
    expect(validatePolicy({ ...DEFAULT_POLICY, max_adds_per_week: 1.5 })).toMatch(/whole/)
    expect(validatePolicy({ ...DEFAULT_POLICY, max_tool_calls: 0 })).toMatch(/at least 1/)
    expect(validatePolicy({ ...DEFAULT_POLICY, max_output_tokens: 127 })).toMatch(/at least 128/)
    expect(validatePolicy({ ...DEFAULT_POLICY, max_adds_per_week: 101 })).toMatch(/no more than 100/)
    expect(validatePolicy({ ...DEFAULT_POLICY, max_run_seconds: NaN })).toMatch(/non-negative/)
    expect(validatePolicy({ ...DEFAULT_POLICY, allowed_actions: ['unsafe_action'] })).toMatch(/unknown/)
  })
})

describe('schedule preflight', () => {
  it('requires saved policy, the selected provider, model, and both ESPN credentials', () => {
    const state = {
      settings: { provider: 'grok', model: '', mode: 'dry_run', deep_research: false, paused: false },
      credentials: { grok: false, gemini: true, espn_s2: true, swid: false },
      policy: null,
    } as AppState
    expect(setupIssues(state)).toHaveLength(4)
    state.settings.model = 'test-model'
    state.credentials.grok = true
    state.credentials.swid = true
    state.policy = DEFAULT_POLICY
    expect(setupIssues(state)).toEqual([])
  })
  it('validates IANA timezones', () => {
    expect(validTimezone('America/New_York')).toBe(true)
    expect(validTimezone('UTC')).toBe(true)
    expect(validTimezone('Not/A_Zone')).toBe(false)
    expect(validTimezone('')).toBe(false)
  })
})
