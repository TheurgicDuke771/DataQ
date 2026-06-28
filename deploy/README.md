# DataQ — deployment scaffolding

Apply-ready Azure deploy scaffolding (Week 7). **Nothing here deploys on its own**
— the workflow ([.github/workflows/deploy.yml](../.github/workflows/deploy.yml)) is
`workflow_dispatch`-only until the Azure resources exist and the secrets/vars
below are set. This documents what to provision and how the pieces fit.

Azure is **one** deploy target behind the app's seams (ADR 0010/0013) — the
manifests here are infra config, not business logic. No Azure resource names are
hardcoded in app code; they live only as workflow `vars`/`secrets`.

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
3. **App env**: set the keys in [deploy/.env.app.prod.example](.env.app.prod.example)
   on the api + worker apps. Secret values (DB/Redis URL, App Insights) are Key
   Vault-backed Container Apps secrets — never literals.
4. **SWA linked backend**: link the `dataq-api` Container App as the SWA backend so
   `/api/*` is proxied same-origin (then `CORS_ALLOW_ORIGINS` can stay empty). If
   instead the SPA calls the API cross-origin, set `CORS_ALLOW_ORIGINS` to the SWA
   origin (the FastAPI CORS middleware turns on only when it's non-empty).
5. **Azure Monitor → ADF webhook** alert rule (Week-7 task) — needs the deployed
   API URL; configure after the first deploy.

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
