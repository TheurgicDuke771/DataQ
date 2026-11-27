# DataQ — app infra (OpenTofu)

Provisions DataQ's production resources on Azure. On an empty subscription the first
`tofu apply` creates everything, as the AWS stack does: the **resource group**
(`azure_resource_group`, default `dataq-rg`), the **Container Apps environment**
(`container_app_environment_name`, default `dataq-cae`) and a **Postgres Flexible Server**
with the app's `dataq` database.

A free/trial subscription allows only one Container Apps environment and one Flexible
Server, which other workloads may already use (ADR 0024). For that case set
**`create_shared_resources = false`**: the stack then attaches to the existing three instead
of creating them (see [Using existing resources](#using-existing-resources)). Everything else
this stack creates is `dataq-app-*` / `purpose=dataq-app`; any other resources in the group
are untouched.

## What it creates

| Resource | Name |
|---|---|
| Log Analytics workspace | `dataq-app-logs` |
| Application Insights | `dataq-app-ai` |
| User-assigned identity | `dataq-app-id` (api/worker/beat → Key Vault) |
| Key Vault (RBAC) | `dataq-app-kv-<suffix>` (SecretStore + webhook secrets) |
| API / worker / beat / migrate | `dataq-app-api` · `dataq-app-worker` · `dataq-app-beat` (sole schedule dispatcher, #1811) · `dataq-app-migrate` (job) |
| Frontend (Container App) | `dataq-app-frontend` (nginx SPA; proxies /api + /mcp same-origin to `dataq-app-api`) |
| Redis broker (Container App) | `dataq-app-redis` (internal TCP, password-auth) |
| Azure AD SSO app regs | `dataq-app-api-sso` (API) + `dataq-app-spa` (SPA) |
| GitHub-deploy app registration | `dataq-github-deploy` (OIDC federated cred) |

Plus, by default, the resource group, the Container Apps environment and the Postgres
Flexible Server (`dataq-pg-<suffix>` unless `shared_pg_server_name` names it). On a server
the stack creates, the app's role **`dataq_app`** is the server's administrator and owns the
`dataq` database, the same one-role model as the AWS stack's RDS instance; the single-role
constraint in `postgres.tf` holds either way.

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
  AAD app registrations, and Key Vault secrets). Use your own login, not a service
  principal without Key Vault data-plane rights: the apply writes secrets and would 403.
- The GHCR backend **and** frontend images pushed + public.
- State is **local + gitignored**.

Nothing needs to exist beforehand: with the default `create_shared_resources = true` the
apply creates the resource group, the environment and the server.

## Using existing resources

Set `create_shared_resources = false` to attach to a resource group, a Container Apps
environment and a Postgres Flexible Server that already exist, all in `azure_location` (a
postcondition refuses a mismatch). Name the environment with
`container_app_environment_name` if it is not `dataq-cae`, and the server with
**`shared_pg_server_name`**, which is then required: with `-input=false` (which the apply
below uses) a missing value fails at plan time rather than prompting into a redirected
stdout, which looks exactly like a hang.

**Upgrading a deployment made before the stack could create these:** set
`create_shared_resources = false` (and the server name you already pass) before the next
`tofu plan`, or the plan proposes to create a resource group, an environment and a server
that already exist. The plan also removes `null_resource.ensure_rg`, the old
`az group create` step; that removal touches nothing in Azure.

On an existing server the app's DB is provisioned **out-of-band** (keeps
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
`DATABASE_URL` Container App secret; never committed). On a server the stack creates, the
same variable is the administrator password the server is created with. The app reaches the server
at runtime over the server's allow-Azure-services firewall rule.

## Apply

```bash
cd deploy/terraform/azure
tofu init
# image_tag / frontend_image_tag have no default. On a FIRST apply, pass a release tag:
IMG=v1.2.0 FE=v1.2.0
# On a LATER apply, pass what the deployment runs now, so any app the apply creates
# (e.g. dataq-app-beat, #1811) starts on the live image rather than an older one:
#   IMG=$(az containerapp show -n dataq-app-worker -g dataq-rg --query 'properties.template.containers[0].image' -o tsv); IMG=${IMG##*:}
#   FE=$(az containerapp show -n dataq-app-frontend -g dataq-rg --query 'properties.template.containers[0].image' -o tsv); FE=${FE##*:}
TF_VAR_app_db_password='<the dataq_app password>' tofu plan -out=azure.plan \
  -var="image_tag=$IMG" -var="frontend_image_tag=$FE"   # review
tofu apply azure.plan
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
gh variable set BEAT_APP_NAME        -b dataq-app-beat   # no output for it; fixed name in containerapps.tf
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
# The app's resources only — anything else in the group is untouched:
az resource list -g dataq-rg --query "[?tags.purpose=='dataq-app'].name" -o tsv

# Frontend up (200) — the public surface. The api has NO public ingress since
# ADR 0028 §5, so it's verified THROUGH the frontend, not directly (curl on
# api_url from outside the env will not connect). /healthz (proxied) = 200 the api
# is live; /api/v1/runs = 401 healthy + auth-enforced.
curl -s -o /dev/null -w "%{http_code}\n" "$(tofu output -raw frontend_url)/"
curl -s -w "\n" "$(tofu output -raw frontend_url)/healthz"
curl -s -o /dev/null -w "%{http_code}\n" "$(tofu output -raw frontend_url)/api/v1/runs"
```
