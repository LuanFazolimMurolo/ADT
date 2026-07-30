import { defineConfig, devices } from '@playwright/test'
import {
  API_ORIGIN,
  E2E_PUBLISHABLE_KEY,
  SUPABASE_ORIGIN,
  WEB_ORIGIN,
} from './e2e/support/constants'

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  expect: {
    timeout: 10_000,
  },
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  use: {
    baseURL: WEB_ORIGIN,
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4173 --strictPort',
    url: WEB_ORIGIN,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      VITE_ADT_API_URL: API_ORIGIN,
      VITE_SUPABASE_URL: SUPABASE_ORIGIN,
      VITE_SUPABASE_PUBLISHABLE_KEY: E2E_PUBLISHABLE_KEY,
    },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
