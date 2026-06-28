# DataQ — app infra (Terraform)

Provisions the DataQ **application's own** production resources into the existing
`dataq-rg`. Separate from the harness stack
(`~/Coding/Python/DataQ-harness/terraform`, ADR 0021), **except** for three shared
resources forced by free/trial subscription caps (1 Container App Environment and
1 Postgres Flexible Server per subscription — see ADR 0024):

- the **subscription** + **resource group** (`dataq-rg`),
- the **Container Apps environment** `dataq-cae` (neutral name, `purpose=dataq-shared`),
- the **Postgres Flexible Server** `dataq-pg-wus3-*` (neutral, `purpose=dataq-shared`).

Both shared resources are **owned by the harness Terraform**; this stack only
*references* them (data sources). Everything the app creates is `dataq-app-*` /
`purpose=dataq-app`; the harness's `dataq-harness-*` resources are untouched.

## What it creates

| Resource | Name |
|---|---|
| Log Analytics workspace | `dataq-app-logs` |
| Application Insights | `dataq-app-ai` |
| User-assigned identity | `dataq-app-id` (api/worker → Key Vault) |
| Key Vault (RBAC) | `dataq-app-kv-<suffix>` (SecretStore + webhook secrets) |
| API / worker / migrate | `dataq-app-api` · `dataq-app-worker` · `dataq-app-migrate` (job) |
| Redis broker (Container App) | `dataq-app-redis` (internal TCP, password-auth) |
| Static Web App (Standard) | `dataq-app-web` (+ linked `dataq-app-api` backend) |
| Azure AD SSO app regs | `dataq-app-api-sso` (API) + `dataq-app-spa` (SPA) |
| GitHub-deploy app registration | `dataq-github-deploy` (OIDC federated cred) |

**Referenced, not created:** the `dataq-cae` environment and the
`dataq-pg-wus3-*` server (both harness-owned). The app's database is a **distinct
`dataq` database** + least-privilege **`dataq_app`** role on the shared server.

Backend image: `ghcr.io/theurgicduke771/dataq-backend:<image_tag>` (GHCR, public —
ACA pulls anonymously, ADR 0023). Must exist + be **public** before apply.

## Prerequisites

- `az login` as a subscription **Owner** (registers RPs; creates role assignments,
  AAD app registrations, and Key Vault secrets). **Do not** `source` the harness
  `secrets.sh` — that switches Terraform to the harness SP, which lacks Key Vault
  data-plane rights (403) and isn't the right identity for this stack.
- The shared `dataq-cae` env + `dataq-pg-wus3-*` server already exist (harness).
- The GHCR backend image pushed + public.
- The `dataq` database + `dataq_app` role provisioned (one-off, below).
- State is **local + gitignored**.

## Shared Postgres — one-off role + database

The app's DB lives on the shared server but is provisioned **out-of-band** (keeps
this stack connection-free / CI-friendly — no postgres provider). Run once,
connected as the server admin (add a temp firewall rule for your IP first):

```sql
-- as the server admin, against the `postgres` database:
CREATE ROLE dataq_app LOGIN PASSWORD '<generated>';
CREATE DATABASE dataq OWNER dataq_app;
-- then, connected to the `dataq` database (PG15+ doesn't grant the db owner
-- CREATE on public by default):
GRANT ALL ON SCHEMA public TO dataq_app;
ALTER SCHEMA public OWNER TO dataq_app;
```

Pass that password at apply time as `TF_VAR_app_db_password` (it becomes the
`DATABASE_URL` Container App secret; never committed). The app reaches the server
at runtime over the server's allow-Azure-services firewall rule.

## Apply

```bash
cd deploy/terraform
terraform init
TF_VAR_app_db_password='<the dataq_app password>' terraform plan    # review
TF_VAR_app_db_password='<the dataq_app password>' terraform apply
```

## After apply — wire the Deploy workflow

`.github/workflows/deploy.yml` reads these. Push them with `gh` from the outputs:

```bash
gh secret  set AZURE_CLIENT_ID       -b "$(terraform output -raw github_actions_client_id)"
gh secret  set AZURE_TENANT_ID       -b "$(terraform output -raw azure_tenant_id)"
gh secret  set AZURE_SUBSCRIPTION_ID -b "$(terraform output -raw azure_subscription_id)"
gh secret  set AZURE_STATIC_WEB_APPS_API_TOKEN -b "$(terraform output -raw swa_api_token)"
gh variable set AZURE_RESOURCE_GROUP -b "$(terraform output -raw resource_group)"
gh variable set API_APP_NAME         -b "$(terraform output -raw api_app_name)"
gh variable set WORKER_APP_NAME      -b "$(terraform output -raw worker_app_name)"
gh variable set MIGRATE_JOB_NAME     -b "$(terraform output -raw migrate_job_name)"
gh variable set VITE_AZURE_TENANT_ID    -b "$(terraform output -raw azure_tenant_id)"
gh variable set VITE_AZURE_SPA_CLIENT_ID -b "$(terraform output -raw azure_spa_client_id)"
gh variable set VITE_AZURE_API_CLIENT_ID -b "$(terraform output -raw azure_api_client_id)"
gh variable set VITE_AZURE_API_SCOPE    -b "$(terraform output -raw azure_api_scope)"
```

Create the `production` GitHub environment (federated-credential subject
`repo:<owner>/<repo>:environment:production`). **One-time GHCR step:** in the
`dataq-backend` package settings, *Connect repository* + grant the repo Actions
**write** access so the workflow's `GITHUB_TOKEN` can push (label-linking alone
doesn't grant it for user-scoped packages).

## Verify

```bash
# App resources only — harness untouched:
az resource list -g dataq-rg --query "[?tags.purpose=='dataq-app'].name" -o tsv

# API up (401 = healthy + auth-enforced):
curl -s -o /dev/null -w "%{http_code}\n" "$(terraform output -raw api_url)/api/v1/runs"

# SPA + same-origin /api proxy:
curl -s -o /dev/null -w "%{http_code}\n" "$(terraform output -raw swa_url)/"
```
