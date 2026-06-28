# ADR 0024 — App deployment infrastructure: Terraform (ACA + SWA + KV + self-hosted Redis), sharing the Container Apps environment with the harness

- **Status:** Accepted
- **Date:** 2026-06-27
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) (Azure = one impl behind each
  seam), [0013](0013-marketplace-distribution-and-anti-lock-in.md) (BYOL distribution), [0018](0018-results-surface-and-grafana-deferral.md)
  (in-app surface → same-origin SPA), [0021](0021-demo-test-data-environment-strategy.md) (the harness
  infra is out-of-repo and separate), [0023](0023-container-image-registry-ghcr.md) (GHCR registry).
  Realizes the Week-7 deploy and supersedes the #379 scaffolding's ACR assumption.

## Context

Week 7 is "go to cloud." The #379 scaffolding documented a topology but had **no infra-as-code** for the
app's own resources, and still assumed ACR (superseded by ADR 0023). Separately, the demo/datasource
infra already lives in an **out-of-repo** Terraform harness (ADR 0021) in the shared `dataq-rg`. We had
to decide: where the app's production IaC lives, how it stays isolated from the harness, and the
concrete service choices (registry pull, Redis, secrets, frontend↔API wiring, CI auth).

**Constraint discovered at apply time:** this subscription is hard-capped at **one Container App
Environment, subscription-wide** (`MaxNumberOfGlobalEnvironmentsInSubExceeded` — a free/trial-tier
limit; not per-region, so no region trick helps), and the harness already owns it. So a *dedicated* app
environment is impossible without a quota increase or switching the app to App Service (whose request/
HTTP model fits the Celery worker + self-hosted Redis broker poorly). We therefore **share the harness's
Container Apps environment** — the one deliberate exception to "subscription + RG only."

## Decision

**Provision the app's production resources with a dedicated, in-repo Terraform stack at
`deploy/terraform/`, reusing the subscription, `dataq-rg`, and the single Container Apps environment;
everything else is a separate `dataq-app-*` resource.**

- **In-repo IaC, local gitignored state.** Unlike the harness (out-of-repo, ADR 0021), this is the
  *app's own* deploy config and belongs with the code; state is gitignored (it holds the generated
  Postgres/Redis passwords). The provider lock is tracked.
- **Separation by namespace + tag.** All app resources are `dataq-app-*` / `purpose=dataq-app`; harness
  resources stay `dataq-harness-*` / `purpose=dataq-harness`. The **one shared resource** — the Container
  Apps environment — is renamed to the neutral **`dataq-cae`** and tagged **`purpose=dataq-shared`**. The
  RG is **referenced, never managed** — an **idempotent `az group create` step** (preserving the existing
  `project=dataq` tag) guarantees it exists without Terraform owning/destroying the shared RG.
- **Shared Container Apps environment (`dataq-cae`).** Owned/created by the harness Terraform (renamed
  there); the app stack **references it via a data source** and runs its own apps inside it. Because the
  env's region is fixed (westus2), the app's container resources land there too. `dataq-app-api`
  (external ingress), `dataq-app-worker` (Celery + embedded beat), `dataq-app-migrate` (manual Job:
  `alembic upgrade head`). All run the **same GHCR image** (ADR 0023), pulled **anonymously** (public
  package — no registry block/credential).
- **Redis = self-hosted `redis:7-alpine` Container App** (internal TCP), not Azure Cache for Redis. The
  Celery broker is transient (no persistence SLA needed); self-hosting is materially cheaper for v1. It
  still requires a password (`--requirepass`) as defense-in-depth atop internal-only ingress.
- **Secrets.** Key Vault (RBAC) is the app's runtime `SecretStore` (`SECRET_STORE=azure_key_vault`),
  read via a **user-assigned managed identity** (Key Vault Secrets User). Boot-critical config
  (`DATABASE_URL`, `REDIS_URL`, App Insights) is injected as **inline Container App secrets**, *not* KV
  references — decoupling first-revision activation from KV-RBAC propagation delay. Webhook secrets are
  pre-seeded in KV.
- **Frontend ↔ API = SWA Standard + linked backend.** The SPA uses a relative `/api/v1` base URL, so
  the Static Web App links the `dataq-app-api` Container App as its backend and proxies `/api/*`
  **same-origin** (CORS middleware stays off — consistent with the existing `staticwebapp.config.json`
  and ADR 0018's same-origin authz). Linking uses `az staticwebapp backends link` (no azurerm resource
  covers arbitrary linked backends).
- **CI auth = AAD app registration + GitHub OIDC.** A `dataq-github-deploy` app registration with a
  federated credential (`repo:…:environment:production`) — no stored client secret. Its Contributor
  role is **scoped to the three deploy targets**, not the shared RG (least privilege; the RG holds
  harness resources).
- **Hardening toggles.** `postgres_public_network_access` (true for v1 over the allow-Azure-services
  firewall + TLS; flip to false for the VNet-private path) and `key_vault_purge_protection` (false for
  bring-up name reuse; true for prod) are explicit variables, not silent defaults.

## Consequences

**Positive**
- App IaC is reviewable, reproducible, and idempotent (`plan` is clean on re-run); app resources are
  cleanly identifiable (`az resource list … purpose=='dataq-app'`) and the harness's `dataq-harness-*`
  resources stay separate.
- No Azure registry, no stored registry/CI secret (GHCR public + OIDC); cheapest viable Redis.
- Same-origin SPA↔API needs no CORS and matches the committed SWA config.
- Least-privilege CI principal can't reach harness resources in the shared RG.
- Sharing the one allowed Container Apps environment keeps the whole architecture (worker + broker +
  job as first-class) within the subscription cap, at no extra cost.

**Negative / watch**
- **Shared Container Apps environment.** The app and harness share `dataq-cae`, so: (a) renaming/
  rebuilding the env from *either* Terraform recreates *both* sides' apps (the rename to `dataq-cae`
  forced a one-time harness rebuild); (b) the env's single Log Analytics workspace (`dataq-harness-logs`)
  collects *both* sides' container stdout — the app's structured telemetry still lands cleanly in its own
  `dataq-app-ai`; (c) the two stacks share the env's network + quota. The two Terraform states stay
  independent; only the env resource is referenced (data source) from the app side. Revisit (dedicated
  env) if a quota increase is obtained.
- **Self-hosted Redis** has no managed HA/persistence; a replica restart drops in-flight broker state
  (acceptable for a Celery broker — tasks re-dispatch). Revisit Azure Cache if durability is needed.
  (The shared env is exactly why the broker is password-protected.)
- **Postgres public access** (v1) is gated only by the allow-Azure-services rule + TLS + credentials;
  the VNet-private path is the post-v1 hardening (variable already in place).
- **Linked-backend via CLI** (`null_resource`) is outside Terraform's graph — re-link by tainting it if
  the API resource id changes.
- Cross-internet GHCR pulls add cold-start latency (already noted in ADR 0023).

## Alternatives considered

- **Dedicated app Container Apps environment.** The original intent — rejected at apply time by the hard
  1-env-per-subscription cap. Sharing the harness env was chosen over a **quota increase** (uncertain
  timing/approval on a free/trial subscription) and over **App Service** (full separation, but its
  HTTP-request model fights a non-HTTP Celery worker and forces managed Azure Cache for Redis + a
  Terraform rewrite). Reconsider a dedicated env if the quota is raised.
- **Out-of-repo, harness-style (ADR 0021 precedent).** Rejected: that precedent is for *demo/datasource*
  infra; the app's deploy config belongs with the app (state still gitignored, no secrets in files).
- **Azure Cache for Redis (managed).** Rejected for v1 on cost; the broker doesn't need a managed SLA.
- **SWA Free + CORS (absolute API URL).** Rejected: needs a frontend code change (relative `/api/v1`
  baseURL) and re-introduces CORS; linked backend is the same-origin design already committed.
- **User-assigned identity + federated cred for CI** (instead of an app registration). Viable and needs
  no app-reg rights; the app registration was chosen for a cleaner separation of the CI principal.
