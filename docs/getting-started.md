# Getting started

Two tracks, matched to why you're here:

- **Run / evaluate / self-host** → pull the prebuilt images (below). Recommended.
- **Develop / contribute** → build from source with `scripts/setup.sh` ([further down](#develop-from-source)).

## Run from prebuilt images (recommended)

**Prerequisite:** Docker (Compose v2). No source checkout, no conda, no Node, no Azure
tenant.

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/main/docker-compose.ghcr.yml
export OPENBAO_TOKEN=$(openssl rand -hex 16)     # root token for the bundled vault
export DATAQ_SIGNIN_EMAIL=you@example.com        # the address allowed to sign in
docker compose -f docker-compose.ghcr.yml up
```

This pulls the published images from GHCR and brings up Postgres + Redis + the API +
Celery worker + the UI + a local mail catcher, runs migrations, and seeds demo data.
Open **`http://localhost:3000`**, type the address you exported, and read the 6-digit
code in the bundled inbox at **`http://localhost:8025`**. API + Swagger at
`http://localhost:8000/docs`.

- **Sign-in works with no SMTP relay.** The stack bundles its own mailbox
  ([Mailpit](https://mailpit.axllent.org), MIT), so DataQ's real mailer runs its real
  SMTP path and the message lands in a web inbox on your machine instead of the
  internet. No mailbox has to exist; nothing leaves the host.
- **Multi-arch:** the images are `linux/amd64` + `linux/arm64`, so Apple Silicon runs
  native (not emulated).
- **Loopback-only:** every port binds to `127.0.0.1` — the stack is reachable from your
  own machine but never the LAN. That matters more than usual for `:8025`, which serves
  live sign-in codes to anyone who can reach it. **Not for production** — a real deploy
  uses the OpenTofu stack (`deploy/terraform/azure`, ADR 0024).
- **Pin a release** instead of the moving stable tags:
  `DATAQ_BACKEND_TAG=vX.Y.Z DATAQ_FRONTEND_TAG=vX.Y.Z docker compose -f docker-compose.ghcr.yml up`.
- **Reset:** `docker compose -f docker-compose.ghcr.yml down -v` (drops the seeded DB).
- **Downgrade to no sign-in at all** — deliberate, not a default:
  `DATAQ_SIGNIN_EMAIL= DATAQ_AUTH_MODE=bypass docker compose -f docker-compose.ghcr.yml up`.
  Every request then resolves to one fixed user and anyone who can reach the port is a
  workspace admin. Omitting `DATAQ_SIGNIN_EMAIL` entirely does **not** get you this —
  compose stops and names both options, because a permissive auth posture should be a
  decision somebody made rather than the one they got by not reading.

## Choosing an auth mode

DataQ ships **three** ways to log a human in. They are a ladder — pick the rung that
fits, but note where the default sits:

| Mode | For | You must bring | Sign-in looks like |
|---|---|---|---|
| **`otp`** ← *the default* | A small team, or anyone evaluating locally | an **SMTP relay** in production — **nothing locally**, the compose stacks bundle a mailbox | you type your address, DataQ emails a 6-digit code, you type it back |
| **`oidc`** | An organisation that already has an IdP | an **OIDC app registration** (Azure AD, Okta, Keycloak, Cognito, …) | your normal SSO redirect |
| **`bypass`** ← *an explicit downgrade* | Throwaway solo work on a machine only you can reach | nothing | no sign-in at all — every request resolves to one fixed demo user, who is a workspace admin |

**`otp` is what you boot into; `bypass` is the explicit downgrade.** Both compose stacks
used to start in `bypass`, which meant anyone who could reach the port administered a
tool that stores warehouse credentials — and an eval stack's defaults are the security
posture people actually run (the same reasoning that moved the default secret store off
the plaintext one, ADR [0039](adr/0039-openbao-self-hosted-secret-backend.md)). What made
`otp` unusable as a default was the SMTP relay it used to require; the bundled catcher
removes that, so the only thing left to supply is *who* is allowed in — one variable,
`DATAQ_SIGNIN_EMAIL`, which `scripts/setup.sh` asks for. Setting it empty is the
downgrade; leaving it unset stops the stack rather than picking for you.

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

The default, and the rung for teams that have email but no IdP.

**Locally there is nothing to configure but your address.** Both compose stacks run a
[Mailpit](https://mailpit.axllent.org) container (MIT) as the mailbox: DataQ performs a
real SMTP submission against it — the same `connect → AUTH → send` path a production
relay gets — and the message appears in a web inbox at `http://localhost:8025`. The
mailer runs with `AUTH_EMAIL_TLS_MODE=none` there, which is the plaintext downgrade
`none` exists for and is logged loudly on every send: it is correct against a container
you started on your own machine and wrong against anything else.

**In production you still bring your own relay** — bundling an outbound mailer is a
deliberate non-goal (direct-to-MX from an arbitrary self-hosted IP gets sign-in codes
spam-foldered, and a vendor relay would leak sign-in metadata to a third party). Frontend:

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

The sign-in form is deliberately credential-only — no sign-up step, no name field, per ADR
0032 — so a first-time OTP user's row has no name yet. The app offers a one-time, skippable
prompt for one right after that first sign-in (#1139). Skipping it is fine — the name is
cosmetic, never an authz input — and it (or any later change) is always available from
**Profile**, for every auth mode, not just `otp`.

Before letting anyone else in, **prove the mailer works**: as a workspace admin, `POST
/api/v1/admin/auth-email/test` sends a real message to your own address and, on failure,
names the stage that broke (`connect` / `tls` / `auth` / `send`). Far better to find a bad
relay here than at a teammate's first sign-in, when the only symptom is a code that never
arrives.

One consequence worth knowing up front: with no IdP there is no bearer token for `/mcp` to
validate, so in this mode **a PAT (`dq_live_…`) is the only credential MCP accepts**. AI
clients need [an API key](api-keys.md); a session cookie will not do.

Read [Security & data handling](security.md) before enabling it — under OTP **the mailbox
is the credential**, so mailbox compromise is account compromise. The session is an
HttpOnly cookie with a fixed 24 h life and no refresh token; signing in again is the
refresh. Codes expire in 10 minutes, are single-use, and allow 5 attempts.

The mailer defaults to **SMTP + STARTTLS on 587**, verified against the system trust
store. Two more transports are available via `AUTH_EMAIL_TLS_MODE`
([#1146](https://github.com/TheurgicDuke771/DataQ/issues/1146)): `implicit` for a
submission relay on **:465** (SMTPS), and `none` — a deliberate plaintext downgrade,
logged loudly on every send, for a loopback relay or a throwaway test rig only. An
internal relay signed by a **private CA** doesn't need the container's whole-process
trust store touched: point `AUTH_EMAIL_CA_BUNDLE` at its PEM and only the mailer's own
connection trusts it. There is no option to skip certificate verification — the bundle
is the answer to "my relay's cert isn't publicly trusted", not a way around checking it.

### `oidc` — self-hosting with your own identity provider

The compose eval runs the frontend with `DATAQ_AUTH_MODE=otp` against the bundled
mailbox, which is fine for evaluation and for a team on a trusted network, but is not an
IdP. The frontend is **one generic image** whose auth config is injected at **runtime**
(nginx serves `/config.js` from the `DATAQ_AUTH_*` env), so the same image goes from eval
to real SSO with **no rebuild** (ADR 0028):

- As-pulled with no auth env it shows an "authentication not configured" banner. For real
  SSO, run the same image with `DATAQ_AUTH_MODE=oidc` + `DATAQ_AUTH_AUTHORITY`
  (e.g. `https://login.microsoftonline.com/<tenant>/v2.0`) + `DATAQ_AUTH_CLIENT_ID` (your
  SPA app registration) + `DATAQ_AUTH_API_SCOPE` (`api://<api-client-id>/<scope>`), and run
  the **backend** with `AUTH_DEV_BYPASS` off + the matching `AZURE_*` settings.
- The frontend reverse-proxies `/api` + `/mcp` to the backend at `DATAQ_API_UPSTREAM`,
  resolving it via the DNS server it **detects from the container's `/etc/resolv.conf`** at
  startup — so the one image works on Docker's embedded DNS (Compose) **and** cluster DNS
  (Kubernetes / Container Apps) without a rebuild.
- **MCP** (`/mcp`) is **fail-closed**: it needs a working sign-in configuration. Under
  `oidc` it validates the bearer token; under the eval stack's `otp` it is served with a
  **PAT (`dq_live_…`) as its only credential** (there is no IdP to issue a token, and a
  session cookie is deliberately rejected) — see [API keys](api-keys.md).

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
                       #   + Mailpit (:8025), the local inbox for sign-in codes
```

`setup.sh` asks **which address may sign in** and writes the answer to your gitignored
`.env` as `DATAQ_SIGNIN_EMAIL`. That address is allow-listed *and* made a workspace
admin, and you sign in by reading the code at `http://localhost:8025` — no Azure tenant,
no SMTP relay. Answering blank is the explicit downgrade to **dev-bypass** (no sign-in;
every request is one fixed dev user), which you can flip either way afterwards by editing
that one variable and re-running `docker compose up`.

**Which file wins.** The compose stack sets the whole `AUTH_EMAIL_*` block in its own
`environment:`, which beats `env_file:` unconditionally — so for the api/worker
containers `.env.app` is **ignored for these keys in every state**, including when the
switch is empty. Putting a real relay in `.env.app` and clearing the switch does not
select it; it lands you in dev-bypass. To point the **compose** stack at a real relay,
keep `DATAQ_SIGNIN_EMAIL` set and override the same key names in the **root `.env`**:

```
DATAQ_SIGNIN_EMAIL=you@example.com
AUTH_EMAIL_SMTP_HOST=smtp.example.com
AUTH_EMAIL_TLS_MODE=starttls          # the bundled catcher's default is `none` — plaintext
```

`.env.app` is still the file for **host-side dev** (uvicorn on your own machine, which
reads it directly), where `AUTH_DEV_BYPASS=true` applies — point `AUTH_EMAIL_SMTP_HOST`
at `localhost` if you want the catcher there too (Mailpit publishes `127.0.0.1:1025`).

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
#    `dataq_test` DB, runs the whole suite incl. the real-broker E2E (this is what
#    CI runs; the script sets DATAQ_E2E=1 for you — see the note below):
scripts/test-backend.sh                    # → 1099 passed
scripts/test-backend.sh -k notifications   # extra pytest args pass through

# 2. Plain pytest — incl. the VS Code / PyCharm test runner. With the compose
#    services up, conftest AUTO-DETECTS the local Postgres (from .env, on dataq_test)
#    so the DB tests run — no env vars, no wrapper. The one real-broker E2E test
#    still skips here (see the note below):
docker compose up -d postgres redis
conda run -n dataq python -m pytest backend/tests           # → 1098 passed, 1 skipped

# 3. No services at all — the DB tests skip, the pure-unit suite still runs green:
conda run -n dataq python -m pytest backend/tests           # → 652 passed, 448 skipped

# Frontend:
cd frontend && pnpm test
```

> The auto-detect is safe: it only kicks in when `TEST_DATABASE_URL` is unset, targets a
> **separate `dataq_test` database** (your dev DB + seed data are untouched), and is a
> no-op in CI (which sets `TEST_DATABASE_URL` explicitly). The **one** test that needs an
> extra, EXPLICIT opt-in is the real-infra E2E (`test_probe_e2e`) — it spins up a live
> Celery worker + broker and does real commits/TRUNCATEs, so it requires `DATAQ_E2E=1`
> alongside `DATABASE_URL` + `REDIS_URL` (not merely the latter two): conftest's
> auto-detect above means `DATABASE_URL` alone is no longer a reliably deliberate signal,
> so a fourth, conftest-never-sets-it-for-you flag keeps this one test a conscious choice.
> `scripts/test-backend.sh` and CI both set it; a bare `pytest` does not.

Before pushing, run the same gate CI does: Ruff, Black `--check`, mypy, Bandit, pytest
(backend) and ESLint, Prettier `--check`, Vitest (frontend). See the
**[Contributing guide](contributing.md)**.
