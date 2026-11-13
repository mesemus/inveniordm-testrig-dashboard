import { defineConfig, devices } from '@playwright/test'

const port = Number(process.env.E2E_PORT ?? 8000)
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  fullyParallel: false,
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  // Starts the API (which serves frontend/dist) with the environment of the test run.
  webServer: {
    command: `uv run invenio-testrig-dashboard web --host 127.0.0.1 --port ${port}`,
    url: `${baseURL}/api/health`,
    cwd: '..',
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
