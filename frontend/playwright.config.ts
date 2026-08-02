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
// Opt-in LIVE-SMOKE lane (never in CI): set E2E_LIVE_BASE_URL to the deployed
// frontend and the config flips to the read-only specs in ./e2e-live — no
// webServer, real OIDC (a headed global-setup captures your login's
// sessionStorage once; oidc-client-ts stores the user there, which is why
// Playwright's cookie/localStorage `storageState` can't do this). CI never
// sets the variable, so the CI matrix is untouched. See e2e/README.md.
// Email-OTP lane (ADR 0032, #736), opt-in via E2E_OTP=1 — set by CI's
// `frontend-e2e` job. It needs a SECOND backend, because the auth seam picks its
// mode at import time and OTP wins over dev-bypass: one process cannot serve both
// lanes. So the lane is a second api on :8100 (OTP-configured, talking to the
// local SMTP sink) behind a second Vite dev server on :3100. Both are started by
// `scripts/e2e-otp-stack.sh`; Playwright starts only the Vite half.
//
// Auth mode is injected per page as `window.__DATAQ_CONFIG__` — the EXACT
// production contract (nginx renders that global from DATAQ_AUTH_* env), so the
// lane exercises the shipped runtime-config path rather than a build-time flag.
// See frontend/e2e-otp/README.md.
const liveBaseURL = process.env.E2E_LIVE_BASE_URL;
const otpEnabled = !liveBaseURL && process.env.E2E_OTP === '1';
const otpBaseURL = process.env.E2E_OTP_BASE_URL || 'http://localhost:3100';
const baseURL = liveBaseURL || process.env.E2E_BASE_URL || 'http://localhost:3000';

export default defineConfig({
  testDir: liveBaseURL ? './e2e-live' : './e2e',
  fullyParallel: true,
  // Fail the build if a `test.only` is committed; flaky-retry only in CI.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // Live smoke serializes (workers 1): one captured session, remote target.
  workers: liveBaseURL ? 1 : process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: liveBaseURL ? 60_000 : 30_000,
  expect: { timeout: liveBaseURL ? 15_000 : 10_000 },
  globalSetup: liveBaseURL ? './e2e-live/global-setup.ts' : undefined,
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    liveBaseURL
      ? { name: 'live-smoke', use: { ...devices['Desktop Chrome'] } }
      : { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    // Own testDir + baseURL, so the dev-bypass specs above are untouched by it.
    ...(otpEnabled
      ? [
          {
            name: 'otp',
            testDir: './e2e-otp',
            use: { ...devices['Desktop Chrome'], baseURL: otpBaseURL },
          },
        ]
      : []),
  ],
  webServer: liveBaseURL
    ? undefined
    : [
        {
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
        ...(otpEnabled
          ? [
              {
                command: 'pnpm dev --host --port 3100',
                url: otpBaseURL,
                reuseExistingServer: !process.env.CI,
                timeout: 120_000,
                // Deliberately NO auth env: the mode is injected per page as
                // window.__DATAQ_CONFIG__, which is what production does.
                env: {
                  // 127.0.0.1, not `localhost`: the stack script binds uvicorn to
                  // the v4 loopback, and `localhost` resolves to ::1 first on
                  // hosts with IPv6 in /etc/hosts. Node's happy-eyeballs would
                  // usually recover, but "usually" is not what a CI lane wants.
                  VITE_API_PROXY_TARGET: process.env.E2E_OTP_API_TARGET || 'http://127.0.0.1:8100',
                },
              },
            ]
          : []),
      ],
});
