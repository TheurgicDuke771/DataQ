# DataQ — app infra (OpenTofu)

Provisions the DataQ **application's own** production resources into the existing
`dataq-rg`. Separate from the harness stack
(`~/Coding/Python/DataQ-harness/terraform`, ADR 0021), **except** for three shared
resources forced by free/trial subscription caps (1 Container App Environment and
1 Postgres Flexible Server per subscription — see ADR 0024):

- the **subscription** + **resource group** (`dataq-rg`),
- the **Container Apps environment** `dataq-cae` (neutral name, `purpose=dataq-shared`),
- the **Postgres Flexible Server** `dataq-pg-wus3-*` (neutral, `purpose=dataq-shared`).

Both shared resources are **owned by the harness stack**; this stack only
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
| Frontend (Container App) | `dataq-app-frontend` (nginx SPA; proxies /api + /mcp same-origin to `dataq-app-api`) |
| Redis broker (Container App) | `dataq-app-redis` (internal TCP, password-auth) |
| Azure AD SSO app regs | `dataq-app-api-sso` (API) + `dataq-app-spa` (SPA) |
| GitHub-deploy app registration | `dataq-github-deploy` (OIDC federated cred) |

**Referenced, not created:** the `dataq-cae` environment and the
`dataq-pg-wus3-*` server (both harness-owned). The app's database is a **distinct
`dataq` database** + least-privilege **`dataq_app`** role on the shared server.

Images (GHCR, public — ACA pulls anonymously, ADR 0023; must exist + be **public**
before apply):
- backend — `ghcr.io/theurgicduke771/dataq-backend:<image_tag>`
- frontend — `ghcr.io/theurgicduke771/dataq-frontend:<frontend_image_tag>` (one
  generic runtime-configured image; auth + `/api` upstream injected via env, ADR 0028)

## Prerequisites

- **OpenTofu** (`tofu`), not Terraform — ADR 0024 amendment (2026-07-27). Install with
  `brew install opentofu` (or see opentofu.org). The directory path and the `.tf`
  extension keep the `terraform` name deliberately: `terraform {}` is OpenTofu's own
  block name and `.tf` its own format. Validated on OpenTofu 1.12.5.
- `az login` as a subscription **Owner** (registers RPs; creates role assignments,
  AAD app registrations, and Key Vault secrets). **Do not** `source` the harness
  `secrets.sh` — that switches the CLI to the harness SP, which lacks Key Vault
  data-plane rights (403) and isn't the right identity for this stack.
- The shared `dataq-cae` env + `dataq-pg-wus3-*` server already exist (harness).
- The GHCR backend **and** frontend images pushed + public.
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
cd deploy/terraform/azure
tofu init
TF_VAR_app_db_password='<the dataq_app password>' tofu plan    # review
TF_VAR_app_db_password='<the dataq_app password>' tofu apply
```

## After apply — wire the Deploy workflow

`.github/workflows/deploy.yml` reads these. Push them with `gh` from the outputs:

```bash
gh secret  set AZURE_CLIENT_ID       -b "$(tofu output -raw github_actions_client_id)"
gh secret  set AZURE_TENANT_ID       -b "$(tofu output -raw azure_tenant_id)"
gh secret  set AZURE_SUBSCRIPTION_ID -b "$(tofu output -raw azure_subscription_id)"
gh variable set AZURE_RESOURCE_GROUP -b "$(tofu output -raw resource_group)"
gh variable set API_APP_NAME         -b "$(tofu output -raw api_app_name)"
gh variable set WORKER_APP_NAME      -b "$(tofu output -raw worker_app_name)"
gh variable set FRONTEND_APP_NAME    -b "$(tofu output -raw frontend_app_name)"
gh variable set MIGRATE_JOB_NAME     -b "$(tofu output -raw migrate_job_name)"
```

Since the ADR 0028 §5 cutover there are **no `VITE_*` build vars and no SWA API
token**: the frontend is one generic image configured at runtime (the Container
App's `DATAQ_AUTH_*` env is wired straight from the SSO app registrations in
`frontend.tf`), so nothing needs copying into repo vars.

Create the `production` GitHub environment (federated-credential subject
`repo:<owner>/<repo>:environment:production`). **One-time GHCR step:** in the
`dataq-backend` package settings, *Connect repository* + grant the repo Actions
**write** access so the workflow's `GITHUB_TOKEN` can push (label-linking alone
doesn't grant it for user-scoped packages).

## State encryption (#1087)

State is **encrypted at rest** (OpenTofu `encryption` block in
[versions.tf](versions.tf), AES-GCM with a PBKDF2-derived key). It holds the generated
Postgres password, the Redis password and webhook secret values; unencrypted it is a
plaintext credential file sitting on one laptop.

### Read this before you touch it

**The passphrase is a data-at-rest key, not a credential.** The distinction is the whole
operational story:

|  | A credential (e.g. the OpenBao token) | This passphrase |
|---|---|---|
| Leaked | revoke it; blast radius bounded by policy + TTL | decrypts **every copy of the state that ever existed** — no revocation |
| **Lost** | re-mint it; no data impact | **`terraform.tfstate` is unrecoverable** |

So losing it is worse than the exposure encryption removes. **Keep a second copy off this
machine** (password manager). Recovering from total loss means rebuilding the stack by
`tofu import`, resource by resource, against live Azure.

### Where it lives, and why not `.env`

`state_encryption_passphrase` in the gitignored `terraform.tfvars`, which OpenTofu loads
automatically — no wrapper, no sourcing step.

Deliberately **not** `.env`: `scripts/setup.sh` regenerates `.env` on first run, and
regenerating this value would silently make the state permanently unreadable.
`terraform.tfvars` is never written by tooling.

### What it does and does not protect against

The passphrase sits beside the ciphertext, so against **host compromise it buys little** —
anyone who can read `terraform.tfstate` can read `terraform.tfvars` next to it.

It buys a great deal against **accidental disclosure**, which is the failure that actually
happens: a state file committed by a `.gitignore` slip, a `*.tfstate.backup` swept into a
backup or disk image, a state pasted into a support ticket. In each case encryption turns
"every production credential leaked" into "an opaque blob leaked."

### Behaviour to expect

- A wrong or missing passphrase **fails closed** — `decryption failed: cipher: message
  authentication failed` — and does **not** corrupt the state file.
- Plan files are encrypted too (they embed resolved sensitive values).
- This is what makes the ADR 0024 OpenTofu amendment **structural**: Terraform cannot parse
  an `encryption` block, so the stack can no longer be run with `terraform`.

### Practical consequence: you can no longer grep the state

`terraform.tfstate` is ciphertext, so anything that read values straight out of it now
fails silently or returns nothing. Use `tofu show -json`, which decrypts on the way out:

```bash
# was: jq ... terraform.tfstate
tofu show -json | jq -r '.values.root_module.resources[]
  | select(.address=="azurerm_container_app.api")
  | .values.secret[] | select(.name=="database-url") | .value'
```

This bites immediately, because `app_db_password` is a required variable with no default
and the usual way to recover it is out of the state. **Always pass `-input=false`**: without
it, a missing required variable makes OpenTofu prompt on stdin, and if stdout is redirected
the prompt is invisible — the command simply hangs forever rather than failing. That cost
several hours here before it was diagnosed.

### Rotating the passphrase

Re-encryption is a migration, not an edit — put the *old* key back as a `fallback` on the
`state` block, set the new one as primary, `tofu apply`, then remove the fallback. Update
the off-machine copy in the same session.

## Verify

```bash
# App resources only — harness untouched:
az resource list -g dataq-rg --query "[?tags.purpose=='dataq-app'].name" -o tsv

# Frontend up (200) — the public surface. The api has NO public ingress since
# ADR 0028 §5, so it's verified THROUGH the frontend, not directly (curl on
# api_url from outside the env will not connect). /healthz (proxied) = 200 the api
# is live; /api/v1/runs = 401 healthy + auth-enforced.
curl -s -o /dev/null -w "%{http_code}\n" "$(tofu output -raw frontend_url)/"
curl -s -w "\n" "$(tofu output -raw frontend_url)/healthz"
curl -s -o /dev/null -w "%{http_code}\n" "$(tofu output -raw frontend_url)/api/v1/runs"
```
