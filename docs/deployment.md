# Production deployment

How to stand DataQ up in production. This is the operator's overview; the exhaustive
provisioning runbook, the Terraform, and the complete env-var reference live in
[`deploy/README.md`](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md) and
[`deploy/terraform/azure/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/azure).
Azure is the supported target today; AWS/GCP are planned behind the same seams.

## Topology

```
Browser ──HTTPS──► Frontend Container App (nginx + SPA — the ONLY public ingress)
AI clients ─MCP──► │  proxies /api + /mcp + /healthz same-origin
                   ▼
              FastAPI (internal ingress) ──► PostgreSQL
                   │  ├──► Celery worker ──► GX execution ──► your datasources
                   │  ├──► Redis (task queue)
                   │  ├──► Key Vault (secrets)
                   │  └──► App Insights / OTLP (observability)
```

The frontend is the sole public surface; the API is internal and reached only through the
frontend proxy (ADR 0028 §5).

## Prerequisites

- A **container platform** — Azure Container Apps in the reference deploy (API + worker +
  frontend apps, a one-shot migrate **job**, and Redis).
- **PostgreSQL** (a dedicated database + a least-privilege app role).
- A **secret store** — Azure Key Vault, reached via a managed identity.
- An **OIDC identity provider** — app registrations for the API (audience) and the SPA.
- **Observability** — Application Insights and/or a generic OTLP endpoint.
- A container registry the platform can pull from (GHCR in the reference).

## 1. Provision

Use the in-repo Terraform ([`deploy/terraform/azure/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/azure),
ADR 0024) to stand up the app stack — the Container Apps, the migrate job, Redis, Key Vault +
managed identity, App Insights, and the SSO app registrations — plus a dedicated database and
least-priv role on your Postgres server. Set the required **GitHub environment variables and
Key Vault secrets** (full list in the deploy README) — never ship the eval/dev defaults.

## 2. Deploy

Deployment is a **manual GitHub Actions workflow** (`workflow_dispatch` → **Deploy**). Each
run, in order:

1. **Builds + pushes** the backend and frontend images (tag defaults to the immutable commit
   SHA).
2. **Runs migrations** — a Container Apps job runs `alembic upgrade head` and the workflow
   **waits for it to succeed** *before* rolling anything. Migrations are additive/
   backward-compatible, so the still-running old code tolerates the new schema.
3. **Rolls** the API + worker, then the frontend (gated on the backend succeeding — no partial
   deploys).

Use an immutable image tag per release; push-on-merge is intentionally off.

## 3. Verify

Run the **pre-deploy** and **post-deploy smoke** checklists in
[`deploy/README.md`](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md#pre-deploy-checklist)
around every deploy. In short:

- **Before:** CI green on the SHA, docs up to date, migrations safe, secrets/config in place.
- **After:** `/healthz` → 200; a user can **sign in**; the **UI renders** (key pages, desktop
  + mobile); **every high-level flow works** end-to-end; auth is enforced (`401` on the API
  and MCP — MCP must be `401`, **not** `421`); prod docs are gated (`404`); and the api /
  worker / frontend are on the deployed tag with the migrate job `Succeeded`.

## Running DataQ without Azure

Azure is **one implementation behind each seam, never the architecture** (ADR
[0010](https://github.com/TheurgicDuke771/DataQ/blob/main/docs/adr/0010-provider-agnostic-infrastructure-seams.md)).
Every seam has a working non-Azure implementation, so a fresh clone runs the
whole product — API, worker, scheduler, UI, checks — with **zero Azure
configuration** (#591):

| Seam | Azure implementation | Local / non-Azure implementation |
|---|---|---|
| Secrets | Key Vault (`SECRET_STORE=azure_key_vault`) | `SECRET_STORE=redis` (the default in `.env.app.example`), or `env` |
| Auth | Entra SSO (`AZURE_*`) | `AUTH_DEV_BYPASS=true` for local dev; **PATs** (`dq_live_…`) for headless REST/MCP — see [API keys](api-keys.md) |
| Observability | App Insights connection string | `OTEL_EXPORTER_OTLP_ENDPOINT` → any OTLP consumer; `docker-compose --profile telemetry up` starts a local Jaeger (UI on `:16686`). Unset ⇒ telemetry off, which is a supported posture, not a degraded one |
| Queue / cache | — | Redis in compose (same image as prod) |
| Database | Shared Azure Postgres | Postgres in compose |
| Lineage catalog | — | `docker-compose --profile lineage up` starts Marquez (dev-only reference consumer, ADR 0034) |

```bash
git clone <repo> && cd DataQ
./scripts/setup.sh          # conda env, hooks, images, migrations, seed data
docker-compose up           # postgres + redis + api + worker + frontend
```

`.env.app.example` ships with the local-first values already selected
(`SECRET_STORE=redis`, `AUTH_DEV_BYPASS=true`, both telemetry endpoints blank);
every `AZURE_*` key may stay empty. Nothing in the app reads an Azure SDK unless
the corresponding seam is explicitly pointed at Azure.

**What is not available locally** is *datasources*, not the platform: a
Snowflake or ADLS **connection** needs a live Snowflake or ADLS to run against.
The local-first path keeps flat files (local + S3), Unity Catalog (Databricks
Free Edition), and Iceberg. The test suite is unaffected either way — its
datasource reads are stubbed, so `pytest` is green with no cloud credentials of
any kind.

## Operating notes

- **Backward-compatible migrations only** — no `DROP`/rename/`NOT NULL`-without-default in the
  same release as the code that needs it. An `ALTER` on a hot table can briefly block on a
  live-worker lock; recovery + hardening are documented in the deploy README.
- **Secrets rotate** without a redeploy (they're read from the store at runtime); restart the
  dependent apps after a shared-Postgres recreate (start-time secret snapshot).
- The reference deployment carries **demo/test fixtures** — tear them down before any
  customer-facing or marketplace use (see the deploy README's operational notes).

For the full runbook — one-time provisioning, the complete env-var reference, SSO setup, and
the checklists — see
[`deploy/README.md`](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md).
