# DataQ — app infra (Terraform)

Provisions the DataQ **application's own** production resources into the existing
`dataq-rg`. Deliberately **separate** from the harness stack
(`~/Coding/Python/DataQ-harness/terraform`, ADR 0021) — only the subscription +
RG are shared. All resources here are prefixed `dataq-app-*` and tagged
`purpose=dataq-app`; the harness's `dataq-harness-*` / `purpose=dataq-harness`
resources are never touched.

## What it creates

| Resource | Name |
|---|---|
| Log Analytics workspace | `dataq-app-logs` |
| Application Insights | `dataq-app-ai` |
| User-assigned identity | `dataq-app-id` (api/worker → Key Vault) |
| Key Vault (RBAC) | `dataq-app-kv-<suffix>` (SecretStore + webhook secrets) |
| Postgres Flexible Server | `dataq-app-pg-wus3-<suffix>` (westus3, B1ms) |
| Container Apps environment | `dataq-app-cae` |
| Redis broker (Container App) | `dataq-app-redis` (internal TCP) |
| API / worker / migrate | `dataq-api` · `dataq-worker` · `dataq-migrate` (job) |
| Static Web App (Standard) | `dataq-app-web` (+ linked api backend) |
| GitHub-deploy app registration | `dataq-github-deploy` (OIDC federated cred) |

Backend image: `ghcr.io/theurgicduke771/dataq-backend:<image_tag>` (GHCR, public —
ACA pulls anonymously, ADR 0023). It must exist + be **public** before apply.

## Prerequisites

- `az login` as a subscription **Owner** (this stack registers RPs + creates role
  assignments + an AAD app registration).
- The GHCR backend image pushed + public (see repo root `deploy/README.md` /
  the Week-7 bring-up steps).
- State is **local + gitignored** — do not commit `terraform.tfstate`.

## Apply

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # optional; defaults work
terraform init
terraform plan      # review
terraform apply
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
# Plus the non-secret VITE_AZURE_* build vars (tenant/spa/api client ids + scope).
```

Create the `production` GitHub environment (the federated credential subject is
`repo:<owner>/<repo>:environment:production`).

## Verify

```bash
# Exactly the app stack — harness untouched:
az resource list -g dataq-rg --query "[?tags.purpose=='dataq-app'].name" -o tsv

# Re-plan is clean (idempotent):
terraform plan      # expect: No changes

# API health + Swagger:
curl -fsS "$(terraform output -raw api_url)/docs" >/dev/null && echo OK
```
