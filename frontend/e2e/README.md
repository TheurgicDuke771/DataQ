# Browser E2E (Playwright)

Drives the **real app in a browser** under dev-bypass auth against the seeded
demo dataset — the other half of the full-stack smoke (#128). The httpx API
smoke (`backend/scripts/e2e_smoke.py`) proves `HTTP → service → DB`; this proves
the React UI a user actually clicks: `browser → Vite proxy → api → DB`.

> Live `test()`/runs against real Snowflake/S3/etc. stay out of scope (no creds
> locally — the documented deferred smoke). The connectivity tests here
> **fail-soft**; the specs assert the health/error path renders, not that a real
> datasource is reachable.

## Specs

| Spec                       | Covers                                                                                       |
| -------------------------- | -------------------------------------------------------------------------------------------- |
| `smoke.spec.ts`            | dev-bypass auth, app shell, sider nav                                                        |
| `connections.spec.ts`      | seeded connections grouped by type, "Test all" health path                                   |
| `suites.spec.ts`           | seeded suite → checks; check + suite authoring round-trips                                   |
| `results.spec.ts`          | seeded runs, run-detail drill-down, pipeline-runs feed                                       |
| `schedules.spec.ts`        | SchedulesPanel: add / pause / delete + invalid-cron 422 path                                 |
| `trigger-bindings.spec.ts` | TriggersPanel: bind pipeline / disable / remove (seeded ADF connection)                      |
| `notifications.spec.ts`    | NotificationsPanel: threshold routing persisted across reload; write-only webhook affordance |

antd Select gotchas (learned per spec, reuse these): rc-select pre-highlights
option 0 when nothing is selected, so `Enter` alone takes the first option and
`ArrowDown`+`Enter` takes the **second**; option nodes live in an off-viewport
rc-virtual-list measurement container, so `getByRole('option').click()` is
flaky — prefer keyboard navigation with the arrow-delta computed from the
currently rendered value (see `notifications.spec.ts`).

## Run it locally

1. Bring up the stack and seed the demo data:

   ```bash
   docker compose up -d            # postgres + redis + api + worker + frontend
   conda run -n dataq python -m backend.scripts.seed_dev
   ```

2. Install browsers once, then run:

   ```bash
   cd frontend
   pnpm install
   pnpm exec playwright install --with-deps chromium
   pnpm e2e            # headless
   pnpm e2e:ui         # Playwright UI mode (watch/debug)
   ```

The config (`playwright.config.ts`) targets `E2E_BASE_URL` (default
`http://localhost:3000`) and **reuses** the already-running compose/`pnpm dev`
server on :3000. Point it elsewhere with `E2E_BASE_URL=… pnpm e2e`.

> On a stack that predates a compose-env or seed change, recreate + reseed
> first — container env is a create-time snapshot: `docker compose up -d api`
> then re-run the seed (it's idempotent and backfills what it can).

## In CI

The `frontend-e2e` job in `.github/workflows/ci.yml` spins up Postgres + Redis,
pip-installs the backend, runs migrations, seeds the demo data, launches
`uvicorn` on :8000, then lets Playwright start its own Vite dev server on :3000
(`VITE_API_PROXY_TARGET=http://localhost:8000`) and run the specs. No Azure /
real datasource credentials are involved — dev-bypass only.

## Live smoke (opt-in — never in CI)

Read-only specs in `e2e-live/` run against the **deployed** app with **real
OIDC**, activated only when `E2E_LIVE_BASE_URL` is set (CI never sets it):

```bash
E2E_LIVE_BASE_URL=https://<your-dataq-frontend-host> pnpm e2e
# optional: E2E_LIVE_SUITE="Retail Orders" (the suite the specs open)
```

First run opens a **headed** browser — complete the sign-in (MFA included);
the session's `sessionStorage` is captured to the gitignored
`e2e-live/.auth/session.json` and reused while fresh (~40 min; oidc-client-ts
keeps the user in sessionStorage, which Playwright's `storageState` cannot
capture — hence the custom setup). The specs then run headless and read-only:
dashboard KPIs, the live suite + its checks, a run detail.

Mutating live verification (trigger runs, alert delivery, MCP queries) is the
manual checklist in the docs-site **Runbook**. The API-level counterpart is
`backend/scripts/e2e_smoke.py` with `DATAQ_API` + `DATAQ_BEARER`.
