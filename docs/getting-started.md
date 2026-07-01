# Getting started

Two tracks, matched to why you're here:

- **Run / evaluate / self-host** → pull the prebuilt images (below). Recommended.
- **Develop / contribute** → build from source with `scripts/setup.sh` ([further down](#develop-from-source)).

## Run from prebuilt images (recommended)

**Prerequisite:** Docker (Compose v2). No source checkout, no conda, no Node, no Azure
tenant.

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/main/docker-compose.ghcr.yml
docker compose -f docker-compose.ghcr.yml up
```

This pulls the published images from GHCR and brings up Postgres + Redis + the API +
Celery worker + the UI, runs migrations, and seeds demo data. Open
**`http://localhost:3000`** — you're in, on **dev-bypass auth** (every request resolves
to a fixed demo user; no sign-in). API + Swagger at `http://localhost:8000/docs`.

- **Multi-arch:** the images are `linux/amd64` + `linux/arm64`, so Apple Silicon runs
  native (not emulated).
- **Loopback-only:** every port binds to `127.0.0.1` — the stack is reachable from your
  own machine but never the LAN (it deliberately disables auth and runs a passwordless
  DB, so it must not be network-exposed). **Not for production** — a real deploy uses the
  Terraform stack (`deploy/terraform/azure`, ADR 0024).
- **Pin a release** instead of the moving stable tags:
  `DATAQ_BACKEND_TAG=vX.Y.Z DATAQ_FRONTEND_TAG=vX.Y.Z docker compose -f docker-compose.ghcr.yml up`.
- **Reset:** `docker compose -f docker-compose.ghcr.yml down -v` (drops the seeded DB).

### Self-hosting with your own Azure AD

The compose eval runs the frontend with `DATAQ_AUTH_MODE=bypass` — auth is bypassed, so
it's for evaluation, not a real multi-user deployment. The frontend is **one generic
image** whose auth config is injected at **runtime** (nginx serves `/config.js` from the
`DATAQ_AUTH_*` env), so the same image goes from eval to real SSO with **no rebuild**
(ADR 0028):

- As-pulled with no auth env it shows an "authentication not configured" banner. For real
  SSO, run the same image with `DATAQ_AUTH_MODE=oidc` + `DATAQ_AUTH_AUTHORITY`
  (e.g. `https://login.microsoftonline.com/<tenant>/v2.0`) + `DATAQ_AUTH_CLIENT_ID` (your
  SPA app registration) + `DATAQ_AUTH_API_SCOPE` (`api://<api-client-id>/<scope>`), and run
  the **backend** with `AUTH_DEV_BYPASS` off + the matching `AZURE_*` settings.
- The frontend reverse-proxies `/api` + `/mcp` to the backend at `DATAQ_API_UPSTREAM`,
  resolving it via the DNS server it **detects from the container's `/etc/resolv.conf`** at
  startup — so the one image works on Docker's embedded DNS (Compose) **and** cluster DNS
  (Kubernetes / Container Apps) without a rebuild.
- **MCP** (`/mcp`) is Azure-AD-protected and **fail-closed**, so it does not function in
  the dev-bypass eval stack — it needs real auth configured.

## Develop from source

**Prerequisites:** **conda** (the backend uses a conda env — not venv/poetry),
**Docker** + Docker Compose, and **Node 24+ / pnpm 9+** for the frontend.

```bash
git clone https://github.com/TheurgicDuke771/DataQ.git
cd DataQ
./scripts/setup.sh     # creates the `dataq` conda env, installs pre-commit, pulls
                       # images, runs DB migrations, seeds dev data, writes a local .env
conda activate dataq
docker-compose up      # Postgres + Redis + FastAPI (:8000) + React (:3000) + Celery
```

Local auth uses a **dev-bypass** (no Azure tenant needed) — every request resolves to a
fixed dev user. Real Azure AD SSO is configured via environment variables in deployed
environments.

## Configuration

All runtime config is environment variables read by the backend's `Settings`. The
**complete, commented reference** is
[`.env.app.example`](https://github.com/TheurgicDuke771/DataQ/blob/main/.env.app.example) —
copy it to `.env.app` (gitignored) and adjust. Never commit secrets; `scripts/setup.sh`
generates local-dev credentials on first run.

## Running tests

```bash
# Backend — WITHOUT services: ~450 DB-backed tests SKIP (they need real Postgres for
# gen_random_uuid()/jsonb, which SQLite can't host), the rest run:
conda run -n dataq python -m pytest backend/tests          # e.g. 652 passed, 448 skipped

# Backend — FULL suite (what CI runs, 0 skipped): the helper brings up the compose
# Postgres + Redis, uses a dedicated `dataq_test` DB, and points the fixtures at them:
scripts/test-backend.sh                    # → 1100 passed
scripts/test-backend.sh -k notifications   # extra pytest args pass through

# Frontend:
cd frontend && pnpm test
```

> The DB-backed skips are **by design** — a contributor without services still gets a
> green `pytest` on the non-DB tests. `scripts/test-backend.sh` (or CI, which spins up
> Postgres + Redis via [ci.yml](https://github.com/TheurgicDuke771/DataQ/blob/main/.github/workflows/ci.yml))
> runs the whole suite with **nothing skipped**. The helper uses a separate `dataq_test`
> database, so your dev DB + seed data are untouched.

Before pushing, run the same gate CI does: Ruff, Black `--check`, mypy, Bandit, pytest
(backend) and ESLint, Prettier `--check`, Vitest (frontend). See the
**[Contributing guide](contributing.md)**.
