# Getting started

Two tracks, matched to why you're here:

- **Run / evaluate / self-host** → pull the prebuilt images (below). Recommended.
- **Develop / contribute** → build from source with `scripts/setup.sh` ([further down](#develop-from-source)).

## Run from prebuilt images (recommended)

**Prerequisite:** Docker (Compose v2). No source checkout, no conda, no Node, no Azure
tenant.

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/main/docker-compose.ghcr.yml
export OPENBAO_TOKEN=$(openssl rand -hex 16)   # root token for the bundled vault
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
  OpenTofu stack (`deploy/terraform/azure`, ADR 0024).
- **Pin a release** instead of the moving stable tags:
  `DATAQ_BACKEND_TAG=vX.Y.Z DATAQ_FRONTEND_TAG=vX.Y.Z docker compose -f docker-compose.ghcr.yml up`.
- **Reset:** `docker compose -f docker-compose.ghcr.yml down -v` (drops the seeded DB).

## Choosing an auth mode

DataQ ships **three** ways to log a human in. They are a ladder — pick the lowest rung
that fits, because each one up costs you a piece of infrastructure:

| Mode | For | You must bring | Sign-in looks like |
|---|---|---|---|
| **`bypass`** | Solo evaluation on your own machine | nothing | no sign-in at all — every request resolves to one fixed demo user |
| **`otp`** | A small team with no identity provider | an **SMTP relay** (a mailbox to send from) | you type your address, DataQ emails a 6-digit code, you type it back |
| **`oidc`** | An organisation that already has an IdP | an **OIDC app registration** (Azure AD, Okta, Keycloak, Cognito, …) | your normal SSO redirect |

The mode is **never inferred**. Anything unrecognised or half-configured renders an
"authentication not configured" banner rather than quietly falling back to something
permissive — and the backend refuses to boot on a half-configured OTP block rather than
coming up unable to log anybody in.

**Two selectors, set them together.** The frontend's `DATAQ_AUTH_MODE` (injected at
runtime by nginx — ADR 0028) and the backend's own mode (inferred from its `AZURE_*` or
`AUTH_EMAIL_*` settings) are separate contracts, and **neither can derive the other**.
Backend OTP on with the frontend on `oidc` shows an SSO flow against an IdP that isn't
configured; the reverse shows a code form whose endpoints 503.

### `otp` — email one-time codes (ADR [0032](adr/0032-email-otp-signin.md))

The middle rung, for teams that have email but no IdP. Frontend:

```
DATAQ_AUTH_MODE=otp
```

Backend — all four mailer values plus **at least one allowlist entry** (there is no open
registration; DataQ holds failing-row samples, which are PII):

```
AUTH_EMAIL_SMTP_HOST=smtp.example.com
AUTH_EMAIL_SMTP_PORT=587
AUTH_EMAIL_USERNAME=dataq@example.com
AUTH_EMAIL_FROM=dataq@example.com
AUTH_EMAIL_PASSWORD_SECRET_NAME=dataq-smtp     # the VALUE lives in your secret store
AUTH_OTP_ALLOWED_DOMAINS=example.com           # and/or AUTH_OTP_ALLOWED_EMAILS=...
WORKSPACE_ADMIN_EMAILS=you@example.com         # bootstrap: your own address
```

First sign-in: put your own address in **both** the allowlist and `WORKSPACE_ADMIN_EMAILS`,
then sign in to your own mailbox. There is no seeded password to rotate.

Read [Security & data handling](security.md) before enabling it — under OTP **the mailbox
is the credential**, so mailbox compromise is account compromise. The session is an
HttpOnly cookie with a fixed 24 h life and no refresh token; signing in again is the
refresh. Codes expire in 10 minutes, are single-use, and allow 5 attempts.

Note that the mailer requires **SMTP + STARTTLS on a publicly-trusted certificate**. An
internal relay signed by a private CA needs that CA in the container's whole-process
trust store (`SSL_CERT_FILE`) today; a per-mailer CA bundle and an implicit-TLS (:465)
option are tracked in
[#1146](https://github.com/TheurgicDuke771/DataQ/issues/1146). There is no
plaintext-SMTP option.

### `oidc` — self-hosting with your own identity provider

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

The ~450 DB-backed tests need a real Postgres (`gen_random_uuid()`/jsonb, which SQLite
can't host); notification tests also need Redis. There are three ways to run:

```bash
# 1. One command — brings up the compose Postgres + Redis, provisions a dedicated
#    `dataq_test` DB, runs the whole suite (this is what CI runs):
scripts/test-backend.sh                    # → 1099 passed, 1 skipped (opt-in E2E)
scripts/test-backend.sh -k notifications   # extra pytest args pass through

# 2. Plain pytest — incl. the VS Code / PyCharm test runner. With the compose
#    services up, conftest AUTO-DETECTS the local Postgres (from .env, on dataq_test)
#    so the DB tests run — no env vars, no wrapper:
docker compose up -d postgres redis
conda run -n dataq python -m pytest backend/tests           # → 1099 passed, 1 skipped

# 3. No services at all — the DB tests skip, the pure-unit suite still runs green:
conda run -n dataq python -m pytest backend/tests           # → 652 passed, 448 skipped

# Frontend:
cd frontend && pnpm test
```

> The auto-detect is safe: it only kicks in when `TEST_DATABASE_URL` is unset, targets a
> **separate `dataq_test` database** (your dev DB + seed data are untouched), and is a
> no-op in CI (which sets `TEST_DATABASE_URL` explicitly). The **one** always-skipped test
> is the real-infra E2E (`test_probe_e2e`) — it spins up a live Celery worker + broker and
> is deliberately opt-in via `DATABASE_URL` + `REDIS_URL`.

Before pushing, run the same gate CI does: Ruff, Black `--check`, mypy, Bandit, pytest
(backend) and ESLint, Prettier `--check`, Vitest (frontend). See the
**[Contributing guide](contributing.md)**.
