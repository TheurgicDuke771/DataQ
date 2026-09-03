# Production deployment

How to stand DataQ up in production. This is the operator's overview; the exhaustive
provisioning runbook, the OpenTofu stack, and the complete env-var reference live in the
repository's `deploy/README.md` and
[`deploy/terraform/azure/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/azure).
**Azure is the primary reference target; AWS is a second, live-verified reference stack**
([`deploy/terraform/aws/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/aws) —
see [AWS reference deployment](#aws-reference-deployment) below). GCP is planned behind the
same seams.

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

Use the in-repo OpenTofu stack ([`deploy/terraform/azure/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/azure),
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

Run the **pre-deploy** and **post-deploy smoke** checklists in the repository's
`deploy/README.md` around every deploy. In short:

- **Before:** CI green on the SHA, docs up to date, migrations safe, secrets/config in place.
- **After:** `/healthz` → 200; a user can **sign in**; the **UI renders** (key pages, desktop
  + mobile); **every high-level flow works** end-to-end; auth is enforced (`401` on the API
  and MCP — MCP must be `401`, **not** `421`); prod docs are gated (`404`); and the api /
  worker / frontend are on the deployed tag with the migrate job `Succeeded`.

> A side-by-side comparison of the three installations — hosting, features, harness,
> workflow and config differences — lives on the
> [Deployment parity](deployment-parity.md) page.

## AWS reference deployment

The same product deploys to AWS from the in-repo OpenTofu stack
([`deploy/terraform/aws/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/aws)) —
identical images, identical topology, each seam pointed at the AWS implementation:

```
Browser ──HTTPS──► CloudFront (public surface, origin secret verified at nginx)
                   ▼
              ALB ──► frontend (ECS Fargate, nginx + SPA) ──proxies──► api (ECS Fargate)
                   │  api/worker sidecars: ADOT collector → X-Ray traces + CloudWatch logs
                   ├──► RDS PostgreSQL          ├──► ElastiCache Redis (TLS)
                   ├──► AWS Secrets Manager     └──► SES (email alerts)
              Cognito (OIDC issuer for the SPA + API)
```

- **Secrets:** `SECRET_STORE=aws_secrets_manager` (fourth store behind the seam).
- **Auth:** Amazon Cognito through the same generic OIDC contract (`DATAQ_AUTH_*`,
  `AUTH_OIDC_*`) — the second issuer validated in a real deployment after Azure AD. Cognito
  needs `DATAQ_AUTH_LOGOUT_STYLE=cognito` (its `/logout` is not RP-Initiated-Logout
  conformant) and resolves the user profile via the **userinfo endpoint** (its access tokens
  carry neither `email` nor `aud`).
- **Who can sign up — set this deliberately.** A Cognito pool allows **self-service
  registration by default**, and DataQ provisions an account for anyone the issuer vouches
  for, so the pool's registration setting effectively *is* your access policy. This stack
  sets `allow_admin_create_user_only = true`; pair it with `OIDC_ALLOWED_EMAILS` /
  `OIDC_ALLOWED_DOMAINS` (enforced on every request, on REST and MCP, so it revokes as well
  as admits). Leaving the allowlist empty is permitted and logs
  `auth_oidc_no_signup_allowlist` at WARNING on every boot.
- **Edge protection:** a WAF per-IP rate ceiling on the distribution, in front of the in-app
  limiter (which fails open by design), plus edge caching of the fingerprinted bundle. Set
  `waf_enabled = false` to opt out of the ~$7/month.
- **Browser security headers** ship with the frontend image on every deployment. Narrow the
  CSP's `connect-src` to your identity provider's origins with `DATAQ_CSP_CONNECT_SRC` — for
  Cognito that is **two** hosts (the issuer for discovery/JWKS, the hosted-UI domain for the
  token exchange); the permissive `https:` default keeps sign-in working if you don't.
- **Observability:** the app's vendor-neutral OTLP export feeds an **ADOT collector sidecar**
  → X-Ray traces + OpenTelemetry logs in CloudWatch with matching trace ids.
- **Deploy:** a parallel **Deploy (AWS)** workflow (`deploy-aws.yml`) — GitHub OIDC role
  login, immutable `aws-<sha>` tags, migrate run-task gated on exit 0, then ECS service
  rolls and a CloudFront smoke.
- **Gotchas** (full list in the repository's `deploy/terraform/aws/README.md`):
  a `rediss://` broker URL needs `ssl_cert_reqs` (the app now defaults it to `required`);
  task definitions are under `ignore_changes`, so env/sidecar edits need a targeted
  `tofu apply -replace`; CloudFront sends an **origin secret header** that nginx enforces,
  so a third-party distribution cannot origin-point at the ALB.

## Running DataQ without Azure

Azure is **one implementation behind each seam, never the architecture** (ADR
[0010](https://github.com/TheurgicDuke771/DataQ/blob/main/docs/site/adr/0010-provider-agnostic-infrastructure-seams.md)).
Every seam has a working non-Azure implementation, so a fresh clone runs the
whole product — API, worker, scheduler, UI, checks — with **zero Azure
configuration**:

| Seam | Cloud implementation | Local / non-cloud implementation |
|---|---|---|
| Secrets | Key Vault (`SECRET_STORE=azure_key_vault`) · AWS Secrets Manager (`SECRET_STORE=aws_secrets_manager`) | `SECRET_STORE=openbao` — OpenBao in compose (the default in `.env.app.example`), or `env` for host-only dev. ADR [0039](../adr/0039-openbao-self-hosted-secret-backend.md); the store speaks the KV v2 API, so the same mode also serves Vault or HCP |
| Auth | Entra SSO (`AZURE_*`) · any OIDC issuer via `AUTH_OIDC_*` (Cognito validated live) | **Email OTP** for humans (`AUTH_EMAIL_*` + an allowlist — ADR [0032](../adr/0032-email-otp-signin.md)); `AUTH_DEV_BYPASS=true` for local dev; **PATs** (`dq_live_…`) for headless REST/MCP — see [API keys](../guides/api-keys.md). `/mcp` is served in every one of these modes; under OTP it accepts **PATs only** (no IdP ⇒ no bearer token to validate) |
| Observability | App Insights connection string | `OTEL_EXPORTER_OTLP_ENDPOINT` → any OTLP consumer; `docker-compose --profile telemetry up` starts a local Jaeger (UI on `:16686`). Unset ⇒ telemetry off, which is a supported posture, not a degraded one |
| Queue / cache | — | Redis in compose (same image as prod) |
| Database | Shared Azure Postgres | Postgres in compose |
| Lineage catalog | — | `docker-compose --profile lineage up` starts Marquez (dev-only reference consumer, ADR 0034) |

```bash
git clone <repo> && cd DataQ
./scripts/setup.sh          # conda env, hooks, images, migrations, seed data
docker-compose up           # postgres + redis + openbao + api + worker + frontend
```

`.env.app.example` ships with the local-first values already selected
(`SECRET_STORE=openbao`, `AUTH_DEV_BYPASS=true`, both telemetry endpoints blank);
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
- A deployment used for evaluation may carry **demo/test fixtures** (the local
  bootstrap seeds them, and they can reach a deployment from a restored database or a
  seeded environment). Remove them before any customer-facing use — see the deploy
  README's operational notes.

For the full runbook — one-time provisioning, the complete env-var reference, SSO setup, and
the checklists — see the repository's `deploy/README.md`.
