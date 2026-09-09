import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.HARNESS_SMOKE_URL ?? 'http://127.0.0.1:8765'
const target = new URL(baseURL)
if (!['127.0.0.1', 'localhost'].includes(target.hostname) || target.protocol !== 'http:') {
  throw new Error('Browser smoke tests must target an isolated local HTTP backend.')
}

export default defineConfig({
  testDir: './e2e',
  outputDir: './playwright-results',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  use: {
    baseURL,
    channel: 'chrome',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'off',
  },
  projects: [
    { name: 'desktop-chrome', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } },
    { name: 'mobile-chrome', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
})
