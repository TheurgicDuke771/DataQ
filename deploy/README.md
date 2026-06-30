# DataQ — deployment guide

How DataQ v1 is deployed to Azure. Infrastructure is **in-repo Terraform**
(`deploy/terraform/`, applied — [ADR 0024](../docs/adr/0024-app-deployment-infrastructure.md));
the app rolls out via the **`Deploy`** workflow
([.github/workflows/deploy.yml](../.github/workflows/deploy.yml), `workflow_dispatch`).
The stack is **live** — this is the runbook to provision a fresh environment and to
deploy a new image. Related: [ADR 0025](../docs/adr/0025-production-image-pip-slim.md)
(slim+pip image), [ADR 0023](../docs/adr/0023-container-image-registry-ghcr.md) (GHCR).

Azure is **one** deploy target behind the app's seams (ADR 0010/0013) — the
manifests here are infra config, not business logic. No Azure resource names are
hardcoded in app code; they live only as Terraform vars + workflow `vars`/`secrets`.

## Topology

```
Browser ─► Azure Static Web App (frontend/dist)
              │  /api/* proxied to the linked backend (same-origin → no CORS)
              ▼
        Azure Container Apps
          • dataq-api     (FastAPI image, external ingress :8000)
          • dataq-worker  (same image, `celery -A ... worker` + beat)
          • dataq-migrate (Container Apps Job: `alembic upgrade head`)
              │
              ├─► Azure Database for PostgreSQL (DATABASE_URL)
              ├─► Azure Cache for Redis        (REDIS_URL)
              ├─► Azure Key Vault              (SECRET_STORE=azure_key_vault, managed identity)
              └─► Application Insights         (APPLICATIONINSIGHTS_CONNECTION_STRING)
        GitHub Container Registry (GHCR) — holds the backend image (ADR 0023)
          ghcr.io/theurgicduke771/dataq-backend:<tag> — public package, so ACA
          pulls it anonymously (no registry credential stored on the apps/job).
```

Both api + worker run the **same** image ([backend/Dockerfile](../backend/Dockerfile),
build context = repo root). The frontend is built and uploaded to SWA by the
workflow (the [frontend/Dockerfile](../frontend/Dockerfile) is the container
alternative — only needed if you run the UI on Container Apps instead of SWA).

## One-time provisioning

The datasource + compute infra is stood up by the external Terraform harness
(ADR 0021) — see `HARNESS_TODO.md`. Beyond that, this app needs:

1. An **ACA environment** + the three apps/job above (the backend image is on
   **GHCR**, not ACR — ADR 0023). The api/worker run `uvicorn …` / `celery …`;
   the migrate **job** runs `alembic upgrade head`. The `deploy/terraform/` stack
   provisions all of this; the GHCR package must be **public** so ACA pulls it
   anonymously.
2. **Managed identity** on the api + worker apps with **Key Vault Secrets User**
   on the vault (so `DefaultAzureCredential` resolves `SECRET_STORE=azure_key_vault`).
3. **App env**: set the keys on the api + worker apps. The **complete** env-var
   reference (every Settings key) is [../.env.app.example](../.env.app.example);
   the prod-specific *values* are in [deploy/.env.app.prod.example](.env.app.prod.example).
   Secret values (DB/Redis URL, App Insights, webhook URLs) are Key Vault-backed
   Container Apps secrets — never literals. The user-assigned managed identity
   needs `AZURE_CLIENT_ID` set so `DefaultAzureCredential` resolves it (#408).
4. **SWA linked backend**: link the `dataq-api` Container App as the SWA backend so
   `/api/*` is proxied same-origin (then `CORS_ALLOW_ORIGINS` can stay empty). If
   instead the SPA calls the API cross-origin, set `CORS_ALLOW_ORIGINS` to the SWA
   origin (the FastAPI CORS middleware turns on only when it's non-empty).
5. **Azure Monitor → ADF webhook** alert rule (Week-7 task) — needs the deployed
   API URL; configure after the first deploy. Per [ADR 0006](../docs/adr/0006-adf-webhook-authentication.md)
   the shared secret rides the URL as a `?token=` query param, so don't
   hand-assemble it (wrong host / stale token after rotation / missing `?token=`
   are easy to get wrong — #92).

   **Easiest path: the in-app webhook-config surface (#490).** Sign in as a
   workspace admin → **Admin → Inbound webhooks** to copy the ready-to-paste ADF
   URL (host + current `?token=` from Key Vault) and the Airflow URL. Set
   `PUBLIC_BASE_URL` so the generated host is the public origin (the deploy sets
   it to the SWA host; empty falls back to the request host). Paste the ADF URL
   into the Action Group webhook field. Note the **live ADF delivery still needs
   the Common-Alert-Schema payload mapping (#492)** — the alert body Azure Monitor
   sends differs from what the receiver parses today.

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
(OIDC app registration), `AZURE_STATIC_WEB_APPS_API_TOKEN`. The GHCR image push
uses the built-in `GITHUB_TOKEN` (`packages: write`) — no registry secret to set.

**Variables:** `AZURE_RESOURCE_GROUP`, `API_APP_NAME`, `WORKER_APP_NAME`,
`MIGRATE_JOB_NAME`, and the non-secret `VITE_AZURE_*` build values. (No `ACR_*` —
the image lives on GHCR at a fixed `ghcr.io/theurgicduke771/dataq-backend` path.)

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

- `GET /healthz` → `200 {"status":"ok"}`.
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
- **Workspace-admins are superusers over every suite** ([ADR 0027](../docs/adr/0027-suite-permission-model-workspace-admin.md) / #482):
  anyone in `WORKSPACE_ADMIN_EMAILS` can read **all** suites' results — including
  failing-row samples (`results.sample_failures`), the one place PII/PHI lands —
  and manage/delete any suite. **Keep the allowlist minimal.** For a **PHI / regulated
  deployment**, treat the data-access audit trail (G1 / #431 in
  [compliance-posture.md](../docs/compliance-posture.md)) as a **prerequisite before
  granting broad workspace-admin** — PHI is already G1-blocked, and this access breadth
  raises the bar.
