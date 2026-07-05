# DataQ — deployment guide

How DataQ v1 is deployed to Azure. Infrastructure is **in-repo Terraform**
(`deploy/terraform/azure/`, applied — [ADR 0024](../docs/adr/0024-app-deployment-infrastructure.md));
the app rolls out via the **`Deploy`** workflow
([.github/workflows/deploy.yml](../.github/workflows/deploy.yml), `workflow_dispatch`).
The stack is **live** — this is the runbook to provision a fresh environment and to
deploy a new image. Related: [ADR 0025](../docs/adr/0025-production-image-pip-slim.md)
(slim+pip image), [ADR 0023](../docs/adr/0023-container-image-registry-ghcr.md) (GHCR).

Azure is **one** deploy target behind the app's seams (ADR 0010/0013) — the
manifests here are infra config, not business logic. No Azure resource names are
hardcoded in app code; they live only as Terraform vars + workflow `vars`/`secrets`.

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
| `SECRET_STORE` | `redis` (eval) | **`azure_key_vault`** + `AZURE_KEY_VAULT_URL` + the managed identity's `AZURE_CLIENT_ID` (#408). |
| `DATABASE_URL` / `REDIS_URL` | inline, passwordless | Key Vault-backed Container Apps secrets — **never literals**; real credentials. |
| `CORS_ALLOW_ORIGINS` | n/a (same-origin) | empty — the frontend Container App proxies `/api` same-origin (ADR 0028); set the SPA origin only if you split them. |
| `PUBLIC_BASE_URL` | n/a | the public origin (used to assemble webhook URLs). |
| `WORKSPACE_ADMIN_EMAILS` | seeded dev user | a **minimal** real allowlist — admins can read every suite's failing-row samples (see [Operational notes](#operational-notes)). |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | unset | your App Insights resource (observability). |
| `key_vault_purge_protection` (Terraform) | `false` (bring-up) | **`true`** for a hardened vault (irreversible). |
| Interactive API docs | served | **404 in prod** via the prod-docs gate (`ENVIRONMENT=prod`). |

### 2. Access you need

- **Azure subscription** — rights to create the resource group, Container Apps
  environment, PostgreSQL Flexible Server, Cache for Redis, Key Vault, and Application
  Insights + Log Analytics (Contributor on the RG/subscription); the frontend is a
  Container App too (no Static Web App since ADR 0028). **Plus** `User Access
  Administrator`/`Owner` to grant the managed identity the **Key Vault Secrets Officer**
  role (an RBAC role assignment — read+write, so the app can persist/rotate connection
  credentials at runtime; #622).
- **Azure AD (Entra ID)** — `Application Administrator` (or Global Admin) to create the
  **two app registrations** (API + SPA) and **grant admin consent** for the API scope.
- **Subscription resource-provider registration** — the app's Terraform registers
  `Microsoft.App`, `Microsoft.Cache`, `Microsoft.KeyVault`, `Microsoft.Web` (see
  [rp.tf](terraform/azure/rp.tf)); the PostgreSQL + monitoring providers
  (`Microsoft.DBforPostgreSQL`, `Microsoft.Insights`, `Microsoft.OperationalInsights`)
  come registered with the shared harness resources (ADR 0024). Registration needs
  subscription-level rights.
- **GitHub repo admin** — to set the Actions [secrets/vars](#github-config-the-workflow-reads)
  and create the OIDC **federated credential** (subject = the repo's `production`
  environment). The GHCR image push uses the built-in `GITHUB_TOKEN` (`packages: write`);
  the package must be **public** so Container Apps pulls it anonymously (ADR 0023).
- **Tooling** — Terraform + the `az` CLI, authenticated to the subscription.

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

The datasource + compute infra is stood up by the external Terraform harness
(ADR 0021) — see the harness repo's `README.md` (not git-tracked here). Beyond
that, this app needs:

1. An **ACA environment** + the three apps/job above (the backend image is on
   **GHCR**, not ACR — ADR 0023). The api/worker run `uvicorn …` / `celery …`;
   the migrate **job** runs `alembic upgrade head`. The `deploy/terraform/azure/` stack
   provisions all of this; the GHCR package must be **public** so ACA pulls it
   anonymously.
2. **Managed identity** on the api + worker apps with **Key Vault Secrets Officer**
   on the vault (read+write, so `DefaultAzureCredential` resolves
   `SECRET_STORE=azure_key_vault` for both reads and the connection-credential writes
   the API performs; read-only breaks connection-create-with-secret — #622).
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
   > It's durable (nothing in Terraform re-enables it — the old `staticwebapp backends link`
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

## Verify

All against the public **frontend** host (the api has no public ingress since
ADR 0028 §5 — it's reached through the frontend's `/api` + `/healthz` + `/mcp`
proxy; `<frontend>/api/v1/...` is the base URL for any external client too):

- `GET /healthz` → `200 {"status":"ok"}` (proxied to the api).
- `GET /api/v1/me` → **401** (auth enforced; a valid token resolves the user).
- SPA root + a deep link → 200; sign-in via Azure AD SSO.
- Interactive API docs (`/docs`, `/redoc`, `/openapi.json`) are **404 in prod**
  (#170 — the prod-docs gate); the MCP server is mounted at `/mcp` (Azure-AD
  validated; fail-closed without auth — [ADR 0008](../docs/adr/0008-mcp-server.md)).
- Celery beat starts clean (no `NoneType` lock crash, #405/#407) and orchestration
  polling reads Key Vault secrets (#406/#408).

## Operational notes

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
  it, `terraform import` the existing grant instead of recreating:
  `terraform import azuread_application_pre_authorized.azure_cli_on_api <api-application-object-id>/preAuthorizedApplication/04b07795-8ddb-461a-bbee-02f9e1bf7b46`.
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
