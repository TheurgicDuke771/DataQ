# Browser E2E (Playwright)

Drives the **real app in a browser** under dev-bypass auth against the seeded
demo dataset — the other half of the full-stack smoke (#128). The httpx API
smoke (`backend/scripts/e2e_smoke.py`) proves `HTTP → service → DB`; this proves
the React UI a user actually clicks: `browser → Vite proxy → api → DB`.

> Live `test()`/runs against real Snowflake/S3/etc. stay out of scope (no creds
> locally — the documented deferred smoke). The connectivity tests here
> **fail-soft**; the specs assert the health/error path renders, not that a real
> datasource is reachable.

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

## In CI

The `frontend-e2e` job in `.github/workflows/ci.yml` spins up Postgres + Redis,
pip-installs the backend, runs migrations, seeds the demo data, launches
`uvicorn` on :8000, then lets Playwright start its own Vite dev server on :3000
(`VITE_API_PROXY_TARGET=http://localhost:8000`) and run the specs. No Azure /
real datasource credentials are involved — dev-bypass only.
