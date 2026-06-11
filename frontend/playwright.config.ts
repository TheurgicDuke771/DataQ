import { defineConfig, devices } from '@playwright/test';

// Browser E2E — drives the *real* app in a browser (dev-bypass auth, the seeded
// demo dataset), the missing other half of the full-stack smoke (#128). The
// httpx API smoke (backend/scripts/e2e_smoke.py) proves HTTP→service→DB; this
// proves the React app a user actually clicks.
//
// It assumes a running stack reachable at E2E_BASE_URL (default the compose
// frontend on :3000, whose Vite proxy forwards /api → the api service). Bring it
// up first: `docker compose up` + `python -m backend.scripts.seed_dev`.
//
// Locally the `webServer` block reuses that already-running :3000 dev server. In
// CI it starts its own `pnpm dev` (the backend is launched by the workflow step
// before Playwright runs). See frontend/e2e/README.md.
const baseURL = process.env.E2E_BASE_URL || 'http://localhost:3000';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  // Fail the build if a `test.only` is committed; flaky-retry only in CI.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'pnpm dev --host --port 3000',
    url: baseURL,
    // Locally: reuse the compose/`pnpm dev` server already on :3000. In CI:
    // start a fresh one (the api is already up on :8000 from a prior step).
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      VITE_AUTH_DEV_BYPASS: 'true',
      VITE_API_PROXY_TARGET: process.env.VITE_API_PROXY_TARGET || 'http://localhost:8000',
    },
  },
});
