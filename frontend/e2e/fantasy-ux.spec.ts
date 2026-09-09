import { expect, test } from '@playwright/test'

const policy = {
  allowed_actions: ['set_lineup'], protected_player_ids: [], max_adds_per_week: 0, max_drops_per_week: 0,
  max_trades_per_week: 0, max_bid: 0, waiver_budget: 0, waiver_reserve: 0, max_trade_players: 2,
  max_trade_value_loss_pct: 0, trade_values: { '-16001': 8 }, max_tool_calls: 12, max_output_tokens: 2048,
  max_run_seconds: 120, max_runs_per_day: 4,
}
const roster = {
  league_id: 656212638, team_id: 4, season: 2026, scoring_period_id: 1,
  fetched_at: '2026-09-09T15:00:00Z', team: { name: 'Sunday Squad', record: { wins: 0, losses: 0, ties: 0 } },
  roster: [
    { player_id: 4038941, name: 'Justin Herbert', jersey: '10', position: 'QB', slot_id: 0,
      eligible_slots: [0, 20], pro_team_id: 24, pro_team_name: 'Los Angeles Chargers', pro_team_abbreviation: 'LAC',
      injury_status: 'ACTIVE', projected_points: 19.25, actual_points: null,
      headshot_url: 'https://a.espncdn.com/i/headshots/nfl/players/full/4038941.png',
      team_logo_url: 'https://a.espncdn.com/i/teamlogos/nfl/500/lac.png' },
    { player_id: -16001, name: 'Falcons D/ST', jersey: null, position: 'D/ST', slot_id: 20,
      eligible_slots: [16, 20], pro_team_id: 1, pro_team_name: 'Atlanta Falcons', pro_team_abbreviation: 'ATL',
      injury_status: 'UNKNOWN', projected_points: 6.5, actual_points: 0, team_logo_url: 'https://a.espncdn.com/i/teamlogos/nfl/500/atl.png' },
  ],
  settings: { acquisitionSettings: { isUsingAcquisitionBudget: false } },
}

test('named roster, player preference confirmation, guided reviews and dialog focus work', async ({ page, baseURL }, testInfo) => {
  const token = process.env.HARNESS_SMOKE_TOKEN ?? 'browser-smoke-token'
  const errors: string[] = []
  const writes: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  const review = { id: 'mock-review', status: 'completed', provider: 'grok', model: 'mock-model', mode: 'dry_run',
    started_at: '2026-09-09T15:00:00Z', summary: 'Keep **Justin Herbert** in your lineup. No ESPN moves were made.', events: [] }
  let asked = false
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== new URL(baseURL!).origin) return route.abort()
    if (url.pathname === '/api/state') {
      const response = await route.fetch()
      const state = await response.json()
      return route.fulfill({ json: { ...state, settings: { ...state.settings, model: 'mock-model' },
        credentials: { grok: true, gemini: false, espn_s2: true, swid: true } } })
    }
    if (url.pathname === '/api/team') return route.fulfill({ json: roster })
    if (url.pathname === '/api/players/4038941') return route.fulfill({ json: { ...roster.roster[0], facts: [{ label: 'Jersey', value: '10' }] } })
    if (url.pathname === '/api/runs' && route.request().method() === 'POST') {
      writes.push(url.pathname)
      expect(route.request().postDataJSON().objective).toContain('Use player names')
      asked = true
      return route.fulfill({ json: review })
    }
    if (url.pathname === '/api/runs' && route.request().method() === 'GET') return route.fulfill({ json: asked ? [review] : [] })
    if (url.pathname === '/api/runs/mock-review') return route.fulfill({ json: review })
    if (route.request().method() === 'POST') writes.push(url.pathname)
    return route.continue()
  })
  await page.goto(`/#token=${encodeURIComponent(token)}`)
  await expect(page.getByRole('heading', { name: 'My fantasy team' })).toBeVisible()
  await page.evaluate(async config => {
    const session = await (await fetch('/api/session')).json()
    const result = await fetch('/api/policy', { method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token,
    }, body: JSON.stringify(config) })
    if (!result.ok) throw new Error('Unable to seed isolated fixture policy')
  }, policy)
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByRole('button', { name: 'View Justin Herbert', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'View Falcons D/ST', exact: true })).toBeVisible()
  await expect(page.locator('.football-player-card').first()).toContainText('#10')
  await expect(page.locator('.football-player-card').first()).toContainText('19.25')
  await expect(page.locator('.football-player-card').first()).not.toContainText('4038941')
  const before = writes.length
  await page.getByRole('button', { name: 'View Justin Herbert' }).click()
  const modal = page.getByRole('dialog', { name: 'Justin Herbert' })
  await expect(modal).toBeVisible()
  for (let index = 0; index < 10; index++) {
    await page.keyboard.press('Tab')
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest('dialog')))).toBe(true)
  }
  await modal.getByRole('button', { name: 'Keep on my team', exact: true }).click()
  await modal.getByRole('button', { name: 'Go back', exact: true }).click()
  expect(writes.length).toBe(before)
  await modal.getByRole('button', { name: 'Keep on my team', exact: true }).click()
  await modal.getByRole('button', { name: 'Save preference', exact: true }).click()
  await expect(modal.getByText('Keeper preference saved.', { exact: true })).toBeVisible()
  const saved = await page.evaluate(async () => (await (await fetch('/api/state')).json()).policy)
  expect(saved.protected_player_ids).toEqual([4038941])
  expect(saved.trade_values).toEqual(policy.trade_values)
  expect(saved.allowed_actions).toEqual(policy.allowed_actions)
  await page.screenshot({ path: testInfo.outputPath('player-dialog.png') })
  await page.keyboard.press('Escape')
  await expect(modal).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'View Justin Herbert' })).toBeFocused()
  await page.screenshot({ path: testInfo.outputPath('fantasy-clubhouse.png'), fullPage: true })
  await page.getByRole('navigation').getByRole('button', { name: 'Coach settings', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: 'Protect Justin Herbert', exact: true })).toBeChecked()
  await page.getByRole('button', { name: 'Review suggested settings', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Try these cautious settings?' })).toBeVisible()
  await page.getByRole('button', { name: 'Keep my draft', exact: true }).click()
  const pickups = page.getByRole('slider', { name: /^Weekly pickups/ })
  await pickups.focus()
  await pickups.press('ArrowRight')
  await pickups.press('ArrowRight')
  await expect(pickups).toHaveValue('2')
  await page.getByRole('button', { name: 'Review & save settings', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Save these coach settings?' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Save coach settings', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Go back', exact: true }).click()
  expect((await page.evaluate(async () => (await (await fetch('/api/state')).json()).policy)).max_adds_per_week).toBe(0)
  await page.getByRole('button', { name: 'Review & save settings', exact: true }).click()
  await page.getByRole('checkbox', { name: 'I reviewed these settings and want to save them.', exact: true }).check()
  await page.getByRole('button', { name: 'Save coach settings', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect((await page.evaluate(async () => (await (await fetch('/api/state')).json()).policy)).max_adds_per_week).toBe(2)
  await page.screenshot({ path: testInfo.outputPath('coach-settings.png'), fullPage: true })
  await page.getByRole('navigation').getByRole('button', { name: 'My team', exact: true }).click()
  await page.getByRole('button', { name: /Review my team/ }).click()
  await page.getByRole('button', { name: 'Next: review the plan' }).click()
  await page.getByRole('dialog').getByRole('checkbox').check()
  await page.getByRole('button', { name: 'Ask my coach', exact: true }).click()
  await expect(page.getByRole('dialog', { name: "Your coach's advice" })).toBeVisible()
  await expect(page.getByRole('dialog')).toContainText('Justin Herbert')
  expect(asked).toBe(true)
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Emergency pause', exact: true }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(errors).toEqual([])
})
