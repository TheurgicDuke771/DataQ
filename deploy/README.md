# DataQ — deployment guide

How DataQ v1 is deployed to Azure. Infrastructure is **in-repo OpenTofu**
(`deploy/terraform/azure/`, applied — [ADR 0024](../docs/adr/0024-app-deployment-infrastructure.md));
the app rolls out via the **`Deploy`** workflow
([.github/workflows/deploy.yml](../.github/workflows/deploy.yml), `workflow_dispatch`).
The stack is **live** — this is the runbook to provision a fresh environment and to
deploy a new image. Related: [ADR 0025](../docs/adr/0025-production-image-pip-slim.md)
(slim+pip image), [ADR 0023](../docs/adr/0023-container-image-registry-ghcr.md) (GHCR).

Azure is **one** deploy target behind the app's seams (ADR 0010/0013) — the
manifests here are infra config, not business logic. No Azure resource names are
hardcoded in app code; they live only as OpenTofu vars + workflow `vars`/`secrets`.

## Before you deploy: production prerequisites

Read this before a production rollout. It's the "what must change, what access you
need, and what your cloud must provide" checklist; the [provisioning runbook](#one-time-provisioning)
below is the how.

### 1. What you must change (never ship the eval/dev defaults)

The prebuilt-image quickstart ([docs/getting-started](../docs/getting-started.md)) is a
**dev-bypass eval stack** — it disables auth, uses a passwordless DB, and binds to
loopback. A production deployment must flip all of the following. Values live in
[`deploy/.env.app.prod.example`](.env.app.prod.example) (app settings) +
[`deploy/terraform/azure/variables.tf`](terraform/azure/variables.tf) (infra):

| Setting | Eval default | Production |
|---|---|---|
| `AUTH_DEV_BYPASS` | `true` | **`false`** — this is the master auth switch; leaving it on means **no authentication at all**. |
| `AZURE_TENANT_ID` / `AZURE_API_CLIENT_ID` / `AZURE_SPA_CLIENT_ID` | empty | your Azure AD tenant + the two app registrations (API + SPA). |
| **Frontend auth config** | `DATAQ_AUTH_MODE=bypass` (eval) | the **same generic image**, reconfigured at **runtime** — `DATAQ_AUTH_MODE=oidc` + `DATAQ_AUTH_AUTHORITY` / `DATAQ_AUTH_CLIENT_ID` / `DATAQ_AUTH_API_SCOPE` (ADR 0028). **No rebuild** — nginx injects `/config.js` from env. See [frontend/Dockerfile](../frontend/Dockerfile). |
| `SECRET_STORE` | `openbao` (local/eval — ADR 0039) | **`azure_key_vault`** + `AZURE_KEY_VAULT_URL` + the managed identity's `AZURE_CLIENT_ID` (#408). |
| `DATABASE_URL` / `REDIS_URL` | inline, passwordless | Key Vault-backed Container Apps secrets — **never literals**; real credentials. |
| `CORS_ALLOW_ORIGINS` | n/a (same-origin) | empty — the frontend Container App proxies `/api` same-origin (ADR 0028); set the SPA origin only if you split them. |
| `PUBLIC_BASE_URL` | n/a | the public origin — used to assemble inbound webhook URLs **and** the "View run" deep links in Slack/email alerts (#416); unset → alerts omit the link. |
| `WORKSPACE_ADMIN_EMAILS` | seeded dev user | a **minimal** real allowlist — admins can read every suite's failing-row samples (see [Operational notes](#operational-notes)). |
| `RATE_LIMIT_ENABLED` | `true` | keep **`true`** — the fixed-window throttle on every public surface (REST + webhooks + `/mcp`), ADR 0035. Fail-open (a Redis outage disables it, logged); set `false` only to fully disable. |
| `RATE_LIMIT_AUTHENTICATED_PER_MINUTE` / `RATE_LIMIT_UNAUTHENTICATED_PER_MINUTE` / `RATE_LIMIT_WEBHOOK_PER_MINUTE` | `300` / `120` / `120` | per-minute caps, keyed per `sha256(bearer)` (authenticated) or client-IP (rest). The webhook cap is **per provider + IP** (#785) — each of adf/airflow/dbt has its own bucket at this cap; the per-IP webhook *total* is `RATE_LIMIT_WEBHOOK_IP_PER_MINUTE`. Defaults are generous; **tighten `RATE_LIMIT_WEBHOOK_PER_MINUTE`** to your orchestrator's real callback cadence. |
| `RATE_LIMIT_WEBHOOK_IP_PER_MINUTE` | `240` | per-IP ceiling across **all** webhook buckets from one IP (#785) — bounds the aggregate a single IP can spend by rotating provider segments; without it, per-provider buckets would multiply the per-IP webhook budget. |
| `RATE_LIMIT_IP_PER_MINUTE` | `1200` | per-IP ceiling across **all** bearer buckets from one IP — the rotated-token backstop (a client cycling a fresh random `Bearer` per request can't mint unlimited fresh per-token buckets to dodge the cap). Applies only to the `default` (bearer) class; the unauth class is already per-IP, the webhook class has its own ceiling above. |
| `RATE_LIMIT_IPV4_PREFIX` / `RATE_LIMIT_IPV6_PREFIX` | `24` / `64` | per-IP buckets key on this address **prefix**, not the full address (#789) — a rotating NAT/proxy pool inside one allocation shares a bucket instead of diluting the cap across sibling /32s. `/32` / `/128` disable grouping; widen or narrow per deployment (a CGNAT-heavy user base may warrant `/32`). |
| `RATE_LIMIT_XFF_TRUSTED_HOPS` | `1` | number of trusted proxies that append `X-Forwarded-For` in your deployment — the real client is the entry that many hops from the right. **`1`** for a single-proxy / compose setup (rightmost); **`3`** for the ACA public-envoy→nginx→internal-envoy chain (set in the IaC stack). A chain shorter than this falls back to the socket peer. |
| `COMPARISON_MAX_ROWS` | `100000` | default per-side row cap for `comparison` checks (ADR 0015) — both sides materialize in worker memory for the diff, so this is a memory guardrail; over-cap runs **fail fast** (never a silently truncated diff). A check's `config.max_rows` overrides it. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | unset | Azure Monitor / App Insights backend for spans + logs (observability, OTel — ADR 0010). |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | generic OTLP/HTTP backend for spans + logs (#589) — any OTLP consumer (Tempo/Jaeger/Datadog/Collector); set alongside App Insights for parity, or alone for a non-Azure deploy. |
| `OPENLINEAGE_URL` | unset | OpenLineage emission (ADR 0034, #758) — **dark by default**: unset ⇒ zero emission. Point at an OL receiver (Marquez, DataHub's OL endpoint) and every suite run emits START + terminal RunEvents with DQ facets (5s emit timeout, fail-open, no sample rows ever). Advanced transports via the library-owned `OPENLINEAGE__TRANSPORT__*` / `OPENLINEAGE_CONFIG`; `OPENLINEAGE_DISABLED=true` forces dark. |
| `WAREHOUSE_LINEAGE_ENABLED` | unset (`false`) | **`true`** on the **worker only** to run the daily `refresh_warehouse_lineage` sweep (ADR 0034, #858) — **dark by default** because the Snowflake `ACCOUNT_USAGE` / UC `system.access` views it reads need a grant the connection principal may not hold. Set in `containerapps.tf` (`local.worker_env`). Only the worker runs beat, so setting it on the api does nothing. |
| `LINEAGE_PROVIDER` / `MARQUEZ_URL` | unset | **leave unset** unless you run a lineage server. This deployment deliberately does **not** set them (#1086): Marquez is a dev-only compose profile and production is expected to bring its own catalog (ADR 0034). Set them only when pointing at a reachable OL-compatible server. Two failure shapes differ: an unreachable `MARQUEZ_URL` leaves the pull *configured but failing* (fail-soft, logged, pruning deliberately skipped for cross-source safety); **un-setting the provider is now handled** ([#1090](https://github.com/TheurgicDuke771/DataQ/issues/1090)) — the next daily `refresh_lineage_pull` tick sweeps the orphaned `source='marquez'` edges (logged as `lineage_pull_orphans_purged`; re-configuring re-pulls). A configured-but-broken provider (typo'd name, missing URL) deliberately does NOT purge. |
| `LINEAGE_STALE_AFTER_HOURS` | `48` | staleness window for the asset-view lineage-source banner (#1091): a warehouse lineage source last refreshed longer ago than this is flagged **stale**, independent of error/degraded — a refresh loop that silently stops must not render as healthy. `0` disables. |
| `WAREHOUSE_LINEAGE_MAX_SEEDS` | `500` | seed cap for the Snowflake `GET_LINEAGE` per-seed traversal (#892) — the Enterprise-only top tier walks this many enumerated tables (ADR 0040 seam) per refresh, **two round trips each** (upstream + downstream), so it is a latency/cost bound. Overflow walks the first N in catalog order and logs `get_lineage_seeds_truncated`; `<=0` removes the bound. Ignored on Standard accounts (the tier is edition-gated) and by every other tier. |
| `ASSET_INVENTORY_MAX_TABLES` | `2000` | per-connection cap on the #919 warehouse inventory sync (ADR 0040 — opt-in per connection via the `inventory_sync` config toggle); overflow syncs the first N in catalog order and logs `inventory_sync_truncated`. `<=0` removes the bound. UC connections need `SELECT` on `system.information_schema` (same grant class as the lineage pull's `system.access`); a sync failing on grants logs `inventory_sync_connection_failed` per tick. |
| `POLL_STALENESS_ALERT_AFTER_S` | `1800` | workspace-wide dead-poll alert (#1052), **api-side**: the API lifespan loop alerts the workspace Teams/Slack/email channels when `max(last_polled_at)` over ALL orchestration connections exceeds this age — it fires precisely when the worker is too broken to alert for itself. `0` disables the loop. Set on the **api** (the worker value is ignored — deliberately: the check must not live in the process it monitors). |
| `key_vault_purge_protection` (OpenTofu var) | `false` (bring-up) | **`true`** for a hardened vault (irreversible). |
| Interactive API docs | served | **404 in prod** via the prod-docs gate (`ENVIRONMENT=prod`). |

### 2. Access you need

- **Azure subscription** — rights to create the resource group, Container Apps
  environment, PostgreSQL Flexible Server, Cache for Redis, Key Vault, and Application
  Insights + Log Analytics (Contributor on the RG/subscription); the frontend is a
  Container App too (no Static Web App since ADR 0028). **Plus** `User Access
  Administrator`/`Owner` to grant the managed identity a **custom get+list+set Key Vault
  role** (an RBAC role assignment — read+write so the app can persist/rotate connection
  credentials at runtime, but not the broader built-in Secrets Officer; #622).
- **Azure AD (Entra ID)** — `Application Administrator` (or Global Admin) to create the
  **two app registrations** (API + SPA) and **grant admin consent** for the API scope.
- **Subscription resource-provider registration** — the app's OpenTofu stack registers
  `Microsoft.App`, `Microsoft.Cache`, `Microsoft.KeyVault`, `Microsoft.Web` (see
  [rp.tf](terraform/azure/rp.tf)); the PostgreSQL + monitoring providers
  (`Microsoft.DBforPostgreSQL`, `Microsoft.Insights`, `Microsoft.OperationalInsights`)
  come registered with the shared harness resources (ADR 0024). Registration needs
  subscription-level rights.
- **GitHub repo admin** — to set the Actions [secrets/vars](#github-config-the-workflow-reads)
  and create the OIDC **federated credential** (subject = the repo's `production`
  environment). The GHCR image push uses the built-in `GITHUB_TOKEN` (`packages: write`);
  the package must be **public** so Container Apps pulls it anonymously (ADR 0023).
- **Tooling** — **OpenTofu** (`tofu`; `brew install opentofu`) + the `az` CLI,
  authenticated to the subscription. Not Terraform — ADR 0024 amendment (2026-07-27).

### 3. Cloud prerequisites

DataQ is provider-agnostic by design — Azure is one target behind the app's seams
(ADR [0010](../docs/adr/0010-provider-agnostic-infrastructure-seams.md) /
[0013](../docs/adr/0013-marketplace-distribution-and-anti-lock-in.md)), so no cloud is
baked into app code. Today **Azure is the supported, implemented target**; AWS and GCP
are planned.

#### Azure — supported today

- An Azure **subscription** + a region with quota for **1 Container Apps environment**,
  **1 PostgreSQL Flexible Server**, Cache for Redis, Key Vault, and App Insights + Log
  Analytics (the frontend is a Container App, not a Static Web App — ADR 0028).
  (Free/trial tiers cap one ACA env + one Postgres
  server per subscription, so the app **shares** the RG/env/Postgres server with the
  harness and namespaces its own DB + role — ADR [0024](../docs/adr/0024-app-deployment-infrastructure.md).)
- The **resource providers** and **app registrations** from §2 registered/created.
- The **GHCR** backend package public. Then follow [One-time provisioning](#one-time-provisioning).

#### AWS — planned (not yet available)

Not yet implemented. The seams map to: ECS Fargate or App Runner (api + worker) · RDS
for PostgreSQL · ElastiCache for Redis · Secrets Manager (`SecretStore` impl) · CloudWatch
+ OpenTelemetry (observability) · Cognito or an OIDC IdP behind `get_current_user`. Track
via the anti-lock-in roadmap ([ADR 0013](../docs/adr/0013-marketplace-distribution-and-anti-lock-in.md)).

#### GCP — planned (not yet available)

Not yet implemented. The seams map to: Cloud Run (api + worker) · Cloud SQL for
PostgreSQL · Memorystore for Redis · Secret Manager (`SecretStore` impl) · Cloud Logging +
OpenTelemetry · Identity Platform / an OIDC IdP behind `get_current_user`.

## Topology

```
Browser ─► dataq-app-frontend (Container App: nginx SPA, external ingress :8080)
              │  /api/* + /mcp + /healthz proxied same-origin (→ no CORS) to ↓
              ▼
        Azure Container Apps
          • dataq-app-api      (FastAPI image, INTERNAL ingress :8000 — not public)
          • dataq-app-worker   (same image, `celery -A ... worker` + beat)
          • dataq-app-migrate  (Container Apps Job: `alembic upgrade head`)
              │
              ├─► Azure Database for PostgreSQL (DATABASE_URL)
              ├─► Azure Cache for Redis        (REDIS_URL)
              ├─► Azure Key Vault              (SECRET_STORE=azure_key_vault, managed identity)
              └─► Application Insights         (APPLICATIONINSIGHTS_CONNECTION_STRING)
        GitHub Container Registry (GHCR) — holds both images (ADR 0023)
          ghcr.io/theurgicduke771/dataq-{backend,frontend}:<tag> — public packages,
          so ACA pulls them anonymously (no registry credential on the apps/job).
```

api + worker run the **same** backend image ([backend/Dockerfile](../backend/Dockerfile),
build context = repo root). The frontend is **one generic nginx image**
([frontend/Dockerfile](../frontend/Dockerfile)) whose auth config + `/api` proxy
upstream are injected at **runtime** from env (ADR 0028) — the same image serves the
eval stack (`DATAQ_AUTH_MODE=bypass`) and prod (`=oidc`).

## One-time provisioning

The datasource + compute infra is stood up by the external OpenTofu harness
(ADR 0021) — see the harness repo's `README.md` (not git-tracked here). Beyond
that, this app needs:

1. An **ACA environment** + the three apps/job above (the backend image is on
   **GHCR**, not ACR — ADR 0023). The api/worker run `uvicorn …` / `celery …`;
   the migrate **job** runs `alembic upgrade head`. The `deploy/terraform/azure/` stack
   provisions all of this; the GHCR package must be **public** so ACA pulls it
   anonymously.
2. **Managed identity** on the api + worker apps with a **custom get+list+set Key Vault
   role** (read+write but not the broader built-in Secrets Officer, so
   `DefaultAzureCredential` resolves `SECRET_STORE=azure_key_vault` for both reads and
   the connection-credential writes the API performs; read-only breaks
   connection-create-with-secret — #622).
3. **App env**: set the keys on the api + worker apps. The **complete** env-var
   reference (every Settings key) is [../.env.app.example](../.env.app.example);
   the prod-specific *values* are in [deploy/.env.app.prod.example](.env.app.prod.example).
   Secret values (DB/Redis URL, App Insights, webhook URLs) are Key Vault-backed
   Container Apps secrets — never literals. The user-assigned managed identity
   needs `AZURE_CLIENT_ID` set so `DefaultAzureCredential` resolves it (#408).
4. **Frontend Container App** (`dataq-app-frontend`): the nginx image reverse-proxies
   `/api/*` + `/mcp` + `/healthz` to the api app same-origin (via its `DATAQ_API_UPSTREAM`
   env), so `CORS_ALLOW_ORIGINS` stays empty. If instead you split the SPA onto a different
   origin, set `CORS_ALLOW_ORIGINS` to it (the FastAPI CORS middleware turns on only
   when it's non-empty). The api uses **internal ingress over HTTP** with
   `allow_insecure_connections = true` — ACA's internal service-to-service pattern; nginx
   must proxy as **HTTP/1.1** (`proxy_http_version 1.1`) or ACA ingress returns `426`.
   > **⚠️ One-time cutover cleanup — disable ACA EasyAuth on the api.** If the api was ever
   > **linked as an Azure Static Web App backend** (the pre-ADR-0028 topology), Azure
   > auto-enabled Container Apps **built-in authentication (EasyAuth)** on it with the
   > `azureStaticWebApps` identity provider. After the SWA→Container-App cutover the SWA is
   > destroyed but that EasyAuth config is **orphaned** and 401s *every* request at the
   > ingress (including `/healthz` and valid Bearer tokens), because DataQ does its **own**
   > token validation (`fastapi-azure-auth`) and doesn't use EasyAuth. Turn it off once:
   > ```
   > az containerapp auth update -n dataq-app-api -g dataq-rg --enabled false
   > ```
   > It's durable (nothing in the IaC stack re-enables it — the old `staticwebapp backends link`
   > is gone). A fresh deploy that never had an SWA won't have EasyAuth, so this only applies
   > when cutting over from the SWA topology.
5. **Azure Monitor → ADF webhook** alert rule (Week-7 task) — targets the public
   **frontend** origin (`<frontend>/api/v1/orchestration/events/adf`, proxied to the
   internal api); configure after the first deploy. Per [ADR 0006](../docs/adr/0006-adf-webhook-authentication.md)
   the shared secret rides the URL as a `?token=` query param, so don't
   hand-assemble it (wrong host / stale token after rotation / missing `?token=`
   are easy to get wrong — #92).

   **Easiest path: the in-app webhook-config surface (#490).** Sign in as a
   workspace admin → **Settings → Webhooks** to copy the ready-to-paste ADF
   URL (host + current `?token=` from Key Vault) and the Airflow URL. Set
   `PUBLIC_BASE_URL` so the generated host is the public origin (the deploy sets
   it to the frontend Container App host; empty falls back to the request host). Paste the ADF URL
   into the Action Group webhook field and turn **"Enable the common alert
   schema" ON** — the receiver keys off `schemaId=azureMonitorCommonAlertSchema`
   (#492): a fired alert acks `reconciling` and triggers an immediate targeted
   poll, so the failed run (with its true runId) lands in `pipeline_runs`
   within seconds. A legacy-format alert body would 422 instead.

   Or build it from the CLI (the live host + Key Vault secret):

   ```bash
   # Vars you already set for the deploy workflow + the vault name.
   RG=<AZURE_RESOURCE_GROUP>; API_APP_NAME=<API_APP_NAME>; VAULT=<key-vault-name>
   API_HOST=$(az containerapp show -n "$API_APP_NAME" -g "$RG" \
     --query properties.configuration.ingress.fqdn -o tsv)
   # ADF_WEBHOOK_SECRET_NAME (default 'adf-webhook-secret') is the Key Vault *key*.
   TOKEN=$(az keyvault secret show --vault-name "$VAULT" --name adf-webhook-secret \
     --query value -o tsv)
   printf 'ADF webhook URL: https://%s/api/v1/orchestration/events/adf?token=%s\n' \
     "$API_HOST" "$TOKEN"
   ```

   ⚠️ The printed URL **contains the shared secret**. Paste it straight into the
   Action Group webhook config; never commit it, and don't run this where the
   output is captured to a log (CI, `script`, screen-share). The secret has a
   single source of truth (Key Vault), so re-run after a rotation
   ([ADR 0006](../docs/adr/0006-adf-webhook-authentication.md) is a hard cutover).

   The token is placed in the URL **un-encoded**, and the receiver compares the
   *URL-decoded* `token` against the Key Vault value — so the webhook secret must
   be **URL-safe** (generate it as e.g. `openssl rand -hex 32`). If an existing
   secret contains reserved characters (`+` `/` `=` `&` `#` space), percent-encode
   the token in the pasted URL, or it will silently fail auth (401).

   The **Airflow** callback URL is the sibling endpoint but carries **no secret**
   — it's HMAC-signed in a header ([ADR 0007](../docs/adr/0007-airflow-callback-model.md)),
   with the signing key configured in the DAG snippet ([integrations/airflow/](../integrations/airflow/)),
   not the URL — so it's just `https://$API_HOST/api/v1/orchestration/events/airflow`.

## GitHub config the workflow reads

Set under repo **Settings → Secrets and variables → Actions**, and add a
federated credential for OIDC login (subject = this repo's `production`
environment).

**Secrets:** `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
(OIDC app registration). The GHCR image push uses the built-in `GITHUB_TOKEN`
(`packages: write`) — no registry secret to set. Since ADR 0028 there is **no
`AZURE_STATIC_WEB_APPS_API_TOKEN`** — the frontend deploys as a Container App via
the same OIDC login.

**Variables:** `AZURE_RESOURCE_GROUP`, `API_APP_NAME`, `WORKER_APP_NAME`,
`FRONTEND_APP_NAME`, `MIGRATE_JOB_NAME`. No `VITE_AZURE_*` build values (the
frontend is configured at runtime, ADR 0028) and no `ACR_*` — the images live on
GHCR at fixed `ghcr.io/theurgicduke771/dataq-{backend,frontend}` paths.

## Going live

1. Provision the resources + set the secrets/vars above.
2. Run the **Deploy** workflow manually (`workflow_dispatch`) to validate end-to-end.
3. To deploy on every merge, uncomment the `push: branches: [main]` trigger in
   the workflow.

Migrations are additive/backward-compatible (CLAUDE.md), so the workflow runs
`alembic upgrade head` **before** rolling the apps — the running old code
tolerates the new schema. (This is exactly the dev-DB step that, when skipped,
500s the checks endpoint after a schema-adding deploy.)

Use an **immutable** `image_tag` per release — ACA caches a tag at the node, so a
same-tag rebuild won't be re-pulled on a new revision. Push-on-merge is
intentionally **off**; deploys are manual `workflow_dispatch`.

### Pre-deploy checklist

Confirm the change is *ready and green* before you push it to prod:

- [ ] **Everything intended is merged to `main`** and you're deploying that known SHA (blank
  `image_tag` → the immutable commit SHA).
- [ ] **CI is fully green** on that SHA — lint, format, type-check, **all unit + integration
  tests**, the security scans (Bandit / CodeQL / secret-scan / dependency-audit), and the
  frontend E2E. The local verification battery (the same gate) passes too — don't let CI be
  the first feedback loop.
- [ ] **Docs are up to date** for what's shipping — `CLAUDE.md` §13 headline + `docs/progress.md`
  ticked; an **ADR** for any significant decision; the **env-var reference** + this deploy
  guide for any new config; and **user docs** for any new user-facing feature.
- [ ] **DB migrations are safe** — additive/backward-compatible (nullable `ADD COLUMN` / new
  table; no `DROP`/rename/`NOT NULL`-without-default in the same PR as the code that needs
  it), `upgrade` **and** `downgrade` tested locally. The workflow runs `alembic upgrade head`
  **before** rolling the apps, so old code never sees a missing column — you just confirm the
  revision is safe. *Note:* an `ALTER` on a hot table (`runs` / `results` / `pipeline_runs`)
  can block on a live-worker lock and hang the migrate job (#605); recovery + root-cause
  hardening are in [#708](https://github.com/TheurgicDuke771/DataQ/issues/708).
- [ ] **Config + secrets are in place** — the required GitHub env/vars and Key Vault secrets
  (see the [prerequisites](#before-you-deploy-production-prerequisites) above), especially any
  new key this release reads.

### Post-deploy smoke checklist

After the workflow is green, confirm the app is actually **healthy and fully functional** —
don't stop at HTTP 200s. Work top-down:

- [ ] **It's up & reachable** — `GET /healthz` → 200; the SPA root and a deep link load.
- [ ] **A user can sign in** — complete the Azure AD SSO flow end-to-end and land on the
  dashboard as a real user (not just the login screen).
- [ ] **The UI renders correctly** — walk the key pages (Dashboard, Connections, Suites,
  Results, Profile, Admin) and confirm they render with data and **no console / network
  errors**, on **desktop *and* mobile** viewports. (The `ui-tester` agent automates this.)
- [ ] **Every high-level capability works end-to-end** — spot-check the core flows, e.g.:
  add/edit a connection and **test** it; author a check; **trigger a run** and see live
  progress → results → (redacted) failing samples; view dashboard trends; a schedule; an
  alert delivered; the **MCP tools** answer for an AI client. If a release touched a specific
  area, exercise that area harder.
- [ ] **Auth + guards hold** — unauthenticated API and MCP requests are rejected (`401`), and
  the prod-docs gate is on (`/docs`, `/redoc`, `/openapi.json` → `404`, #170).
- [ ] **Infra rolled cleanly** — api / worker / frontend are on the **deployed tag** (not the
  old image), the migrate job execution is `Succeeded`, Celery beat starts clean (#405/#407)
  and orchestration polling reads Key Vault (#406/#408), and App Insights shows no post-roll
  errors.
- [ ] **(Optional, deeper)** run the live-smoke lane (`frontend/e2e-live/` gated on
  `E2E_LIVE_BASE_URL` + `e2e_smoke.py` `DATAQ_BEARER` mode, #531) and the MCP 4-query protocol
  smoke for an authenticated end-to-end pass.

**Quick reachability + auth probes** (no token — a `401` *is* the pass for an auth-gated
route; everything is the public **frontend** host since the api has no public ingress, ADR
0028 §5):

```bash
FE=https://<your-frontend-fqdn>   # e.g. az containerapp show -n dataq-app-frontend -g dataq-rg \
                                  #        --query properties.configuration.ingress.fqdn -o tsv
curl -s -o /dev/null -w "healthz         %{http_code}\n"        $FE/healthz       # 200
curl -s -o /dev/null -w "api (auth)      %{http_code}\n"        $FE/api/v1/me     # 401
curl -s -o /dev/null -w "mcp GET         %{http_code}\n"        $FE/mcp/          # 401  NOT 421
curl -s -o /dev/null -w "mcp POST        %{http_code}\n" -X POST -H "content-type: application/json" $FE/mcp/  # 401  NOT 421
curl -s -o /dev/null -w "openapi (gated) %{http_code}\n"        $FE/api/v1/openapi.json  # 404
# NOTE: probe the gate on the API path. Bare $FE/openapi.json returns 200 — that's the
# SPA catch-all serving index.html (nginx serves the shell for any non-proxied path
# since the ACA cutover), not a leaked schema.

# api + worker rolled to the deployed tag?
az containerapp revision list -n dataq-app-api    -g dataq-rg --query "[?properties.active].properties.template.containers[0].image" -o tsv
az containerapp revision list -n dataq-app-worker -g dataq-rg --query "[?properties.active].properties.template.containers[0].image" -o tsv
```

> A `421 Misdirected Request` on `/mcp/` (instead of `401`) is a specific known failure —
> FastMCP's DNS-rebind **Host guard** rejecting the nginx-proxied Host (regressed by the
> fastmcp 3.4.3 bump, fixed via `build_mcp_app(allowed_hosts=…)`; see
> [#706](https://github.com/TheurgicDuke771/DataQ/issues/706)).

## Incident: a migration that hangs, and takes prod reads with it

**This happened** (2026-07-10, deploy run 29069821010) and is the reason for the
`lock_timeout` and `/readyz` below. Know the shape, because the symptom does not
look like a database problem.

**Symptom.** The migrate job sits in `Running`. Meanwhile authenticated requests
start timing out (>25s) — not erroring, *hanging*. `/healthz` stays **green**
throughout, because it only proves the process is alive.

**Cause.** A migration needing `ACCESS EXCLUSIVE` (any `ALTER TABLE`) queues
behind a long-lived lock holder from the running apps. In Postgres a *queued*
exclusive lock blocks **all new readers** — so one waiting DDL statement stalls
every request touching that table. Worse: killing the ACA job execution does not
kill the Postgres backend. It stays as a zombie, still queued, still blocking, and
a retry simply queues behind it.

**Remediation, in order:**

```bash
# 1. Confirm it: anything waiting, and what holds the lock it wants.
psql "$DATABASE_URL" -c "
  SELECT pid, state, wait_event_type, left(query, 60) AS query, age(clock_timestamp(), query_start) AS age
  FROM pg_stat_activity WHERE state <> 'idle' ORDER BY query_start;"

# 2. Stop the migrate job execution (does NOT clear the backend).
az containerapp job execution list -n dataq-app-migrate -g dataq-rg -o table
az containerapp job stop -n dataq-app-migrate -g dataq-rg --job-execution-name <name>

# 3. Clear the zombie backend. Terminate the specific pid…
psql "$DATABASE_URL" -c "SELECT pg_terminate_backend(<pid>);"
# …or, if that is not enough, restart the server (what actually recovered it):
az postgres flexible-server restart -n <server> -g dataq-rg

# 4. Re-run migrate. It took 16s once unblocked.
```

**What is in place now, so this should fail fast instead:**

- `lock_timeout = 15s` on the migration engine (`backend/alembic/env.py`). A
  contended migration now aborts loudly rather than queueing and degrading
  readers. Longer than the app's 5s because a migration is rarer and more
  important, and may legitimately wait out a brush.
- `GET /readyz` exercises a real DB read with a 2s `statement_timeout`, so a read
  degradation is *visible*. `/healthz` stays liveness-only on purpose — a
  liveness probe that fails on a DB blip gets the container killed, which cannot
  fix a database and turns degradation into an outage.
- `transaction_per_migration` in `env.py`, so a revision can set
  `transactional_ddl = False` and use `CREATE INDEX CONCURRENTLY` rather than
  taking a write-blocking lock on a hot table.

**Still your judgement:** migrations touching hot tables (`connections`, `runs`,
`results`, `checks`) deserve an off-peak window or a quiesce. `lock_timeout` turns
a hang into a failed deploy — better, but still a failed deploy.

## Operational notes

- **The OpenTofu state passphrase is a data-at-rest key, not a credential (#1087).** State is
  encrypted (AES-GCM / PBKDF2); the passphrase lives in the gitignored
  `deploy/terraform/azure/terraform.tfvars`. It **cannot be revoked and cannot be re-minted** —
  losing it makes `terraform.tfstate` unrecoverable, which is worse than the plaintext exposure
  encryption removes. **Keep a second copy off the machine.** Full note, including what this does
  and does not protect against, in [terraform/azure/README.md](terraform/azure/README.md).
- **Env vars set out-of-band must be reconciled back into the stack (#1086).** The Deploy
  workflow is `az`-only — it never runs `tofu` — so it is easy to set a container env var
  with `az containerapp update` and never land it in `containerapps.tf`. When that happens
  the stack silently stops describing production, and **the next `tofu apply`, for any
  unrelated reason, deletes the var.** This has already bitten once: `LINEAGE_PROVIDER`,
  `MARQUEZ_URL` and `WAREHOUSE_LINEAGE_ENABLED` were live but absent from the stack.
  - Treat `az containerapp update --set-env-vars` as a **temporary** measure only, and
    land the same change in `containerapps.tf` in the same session.
  - Before any apply, read the plan with `tofu show -json` and diff the env **name sets** —
    the azurerm provider renders the `env` block **positionally**, so the human-readable
    plan looks like a large shuffle and hides what actually changes. Rendered plans have
    misread this drift before.
  - `tofu plan` returning `No changes` is the check that the stack is still truthful. Run
    it periodically, not only when you intend to apply.
- **Restart dependent Container Apps after a shared-Postgres delete/recreate** —
  the DB host is injected as a start-time secret snapshot, so every dependent
  revision must be restarted or it keeps resolving the old/dead host.
- The shared RG / Container Apps env / Postgres server are **reused, never
  destroyed** (free/trial caps one of each; shared with the harness — ADR 0024).
- `key_vault_purge_protection` is off during bring-up (so a destroy/re-apply can
  reuse the vault name); set it **true** for a hardened prod (irreversible).
  **Decision (2026-07-02): deliberately left off** for this deployment — the vault
  is demo/trial-scoped and destroy/re-apply flexibility wins; every secret it holds
  (PATs, SAS, webhook secrets) is rotatable, so accidental-delete recovery is
  re-mint, not data loss. Revisit (flip to `true`) before any regulated or
  production-critical use.
- **This reference deployment carries demo/test fixtures — tear them down before any
  commercial or marketplace use.** The live connections (Snowflake/UC/ADLS/ADF/Airflow),
  Flows A/B/C, demo users, and the deliberately-failing "seeded breach" check are the
  ADR 0021 test harness, not product. The harness Databricks workspace is **Free Edition
  (non-commercial licence)** — recorded 2026-07-03: fine for demo/eval, but before any
  commercial demo, marketplace listing, or customer-facing deployment, migrate UC to a paid
  workspace and remove the harness flows/connections/users (post-v1 gap register G-h/G-i in
  [post-v1-roadmap.md](../context/post-v1-roadmap.md)).
- **Azure CLI is pre-authorized on the API scope** (`azuread_application_pre_authorized.azure_cli_on_api`
  in `terraform/azure/sso.tf`): operators mint API bearers non-interactively with
  `az account get-access-token --resource api://<api-client-id>` (live smoke,
  `e2e_smoke.py` `DATAQ_BEARER` mode, MCP clients). The signed-in Azure user must
  still exist in DataQ's user model to see anything (suite-scoped authz applies as
  normal — a token for an unknown/unshared user reads an empty workspace). The
  grant was first applied manually via Graph on 2026-07-03; if your state predates
  it, `tofu import` the existing grant instead of recreating:
  `tofu import azuread_application_pre_authorized.azure_cli_on_api <api-application-object-id>/preAuthorizedApplication/04b07795-8ddb-461a-bbee-02f9e1bf7b46`.
  Interim posture per [ADR 0026](../docs/adr/0026-auth-api-keys-and-principal-seam.md)
  (DataQ-issued API keys) — build deferred to post-v1 (decided 2026-07-03).
- **Workspace-admins are superusers over every suite** ([ADR 0027](../docs/adr/0027-suite-permission-model-workspace-admin.md) / #482):
  anyone in `WORKSPACE_ADMIN_EMAILS` can read **all** suites' results — including
  failing-row samples (`results.sample_failures`), the one place PII/PHI lands —
  and manage/delete any suite. **Keep the allowlist minimal.** For a **PHI / regulated
  deployment**, treat the data-access audit trail (G1 / #431 in
  [compliance-posture.md](../docs/compliance-posture.md)) as a **prerequisite before
  granting broad workspace-admin** — PHI is already G1-blocked, and this access breadth
  raises the bar.
