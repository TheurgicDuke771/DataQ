# ADR 0024 — App deployment infrastructure: Terraform (ACA + SWA + KV + self-hosted Redis), sharing the Container Apps environment with the harness

> **Amendment (2026-07-27, in place — no new ADR):** the IaC **CLI** is now **OpenTofu**
> (`tofu`), not Terraform. The stack, state, providers, and every decision below are
> otherwise unchanged — only the binary that reads them differs. See the
> **"Amendment (2026-07-27) — OpenTofu replaces Terraform as the IaC CLI"** section
> at the foot of this ADR. Read "Terraform" in the original text below as "the IaC CLI".
> (Deliberately not an anchor link: MkDocs and GitHub slugify the em dash in that
> heading differently, so any fragment is broken in one of the two renderers.)

- **Status:** Accepted (amended 2026-07-27 — OpenTofu replaces Terraform as the IaC CLI)
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
`deploy/terraform/azure/`, reusing the subscription, `dataq-rg`, and the single Container Apps environment;
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

---

## Amendment (2026-07-27) — OpenTofu replaces Terraform as the IaC CLI

- **Status:** Accepted
- **Date:** 2026-07-27
- **Amends:** this ADR's choice of IaC **CLI** only. Every other decision above stands.
- **Related:** ADR [0031](0031-oss-byol-distribution-licensing.md) (MIT distribution + the
  standing no-source-available dependency guardrail, CONTRIBUTING rule 40),
  ADR [0021](0021-demo-test-data-environment-strategy.md) (the out-of-repo harness stack).

### Context

Terraform has been **BUSL-1.1** since v1.6; the stack's floor (`>= 1.9`) and the version
actually in use (1.15.x) are both under it. ADR 0031 put DataQ on record rejecting
BSL-style licensing *for itself* and set a standing guardrail keeping source-available
licenses (explicitly naming BUSL) out of the dependency tree.

**Terraform did not violate that guardrail.** Rule 40 governs the *dependency tree* — what
DataQ ships — and the CLI is a build-time tool that never enters an image or a release
artifact. BUSL-1.1's additional use grant permits using it to deploy your own product;
there is **no legal exposure and nothing was out of compliance.** This amendment is about
*coherence*: the deploy toolchain was the one place a BUSL binary still touched a project
that is explicit about not shipping one.

The practical trigger is [#505](https://github.com/TheurgicDuke771/DataQ/issues/505)
(AWS + GCP deploy IaC, post-v1). Converting one Azure stack is a contained change;
converting three, after they are written against a second CLI, is not. This is the
cheapest this decision will ever be.

### Decision

**Use OpenTofu (`tofu`) as the IaC CLI for every DataQ-owned stack — the in-repo
`deploy/terraform/azure/` stack and the out-of-repo harness stack (ADR 0021) — converted
together.**

- **Directory path and file extensions are unchanged.** `deploy/terraform/<cloud>/` and
  `*.tf` stay. `terraform {}` is OpenTofu's own block name and `.tf` its own format; they
  are not Terraform leftovers. Renaming would churn every doc link, the `.gitignore`
  path-independent globs, the `docs/compliance-posture.md` evidence links, and the
  `aws/`/`gcp/` sibling convention #505 will follow, for zero functional gain.
- **State carries over untouched.** Local backend, state format version 4, gitignored. No
  migration step, no re-import, no `state mv`.
- **Providers resolve from `registry.opentofu.org`.** `tofu init` rewrites
  `.terraform.lock.hcl` accordingly; the tracked lock is the record. Provider *versions*
  are preserved exactly — only the registry host and the hashes change (the OpenTofu
  project's provider builds are not byte-identical to HashiCorp's).
- **Both stacks convert; neither is applied as part of the change.** The harness stack is
  verified **plan-only** — a blanket harness `apply` arms ADF triggers and drifts the
  warehouse (see `docs/ops-log.md`).

### Evidence — how this was verified

A clean plan was **not** the acceptance bar, because the stack already carries pre-existing
drift — the ADR 0034 lineage env vars (`LINEAGE_PROVIDER`, `MARQUEZ_URL`,
`WAREHOUSE_LINEAGE_ENABLED`) are live on prod but absent from `containerapps.tf`, so an
apply would *delete* them, while `DBT_WEBHOOK_SECRET_NAME` (ADR 0029) is in config but not
live. Filed as #1086; unrelated to this change and reproducing identically on both CLIs.
The bar was therefore **equivalence**: the same config and the same state must produce the
*same plan* under both CLIs.

> Worth recording, because it nearly went in wrong: the azurerm provider diffs a container
> app's `env` block **positionally**, so the rendered plan reads as a 14-line shuffle of
> variable names and the actual content is invisible. Read from the rendering alone, the
> drift looks like "config vars that were never applied" — including
> `RATE_LIMIT_XFF_TRUSTED_HOPS`, which is in fact present on both sides and merely moves
> index. Only the `show -json` set-difference gives the real answer (2 removals + 1
> addition per app, 28 unchanged). Another instance of the standing rule: the rendered
> form is not the evidence.

Both CLIs were run against live Azure with identical inputs, their plans exported with
`-out` and rendered via `show -json`, and the `resource_changes` projection normalized
(sorted by address, rendering stripped) and diffed:

| | Terraform 1.15.8 | OpenTofu 1.12.5 |
|---|---|---|
| resource_changes | 40 | 40 |
| action summary | `no-op=38 update=2` | `no-op=38 update=2` |
| normalized diff | — | **byte-for-byte identical** |
| provider versions | azuread 3.9.0 · azurerm 4.79.0 · null 3.3.0 · random 3.9.0 · time 0.14.0 | identical |

The *rendered* text plans differ (branding prose, refresh-line ordering, column alignment,
and how each CLI counts `# (N unchanged attributes hidden)`). Those are presentation
artifacts; the semantic comparison is the one that carries weight, and eyeballing the
rendered diff would not have settled it.

### Consequences

**Positive**
- The deploy toolchain matches the project's stated licensing posture end to end.
- No BUSL binary anywhere in the workflow; no future HCP Terraform upsell surface.
- Unlocks OpenTofu **state encryption** — a real gain here, since the local state holds the
  generated Postgres password and secret values in plaintext on one machine. Tracked as a
  deliberate follow-up, not folded into this change.
- Done while there is exactly one stack pair, before #505 adds two more.

**Negative / watch**
- **The swap was convention, not enforcement — until #1087 closed that.** As written, this
  amendment left the config Terraform-parseable so the change stayed reversible, and named
  state encryption as the separate one-way door.
  > **Update (2026-07-27, #1093, closes #1087):** that door is now closed. `versions.tf`
  > carries an `encryption {}` block (AES-GCM + PBKDF2 over state *and* plan files), which
  > Terraform cannot parse — so **the stack can no longer be run with `terraform`, and the
  > OpenTofu migration is now structural rather than conventional.** The passphrase lives
  > in the gitignored `terraform.tfvars`, deliberately not in Key Vault (which this state
  > manages, making it circular) and not in `.env` (which `scripts/setup.sh` regenerates).
  > Two operational consequences: the state can no longer be grepped — use
  > `tofu show -json` — and `-input=false` is mandatory, since a missing required variable
  > otherwise prompts on stdin and hangs invisibly when stdout is redirected.
- **Two stacks, one resource group.** The app and harness stacks share `dataq-rg`.
  Converting only one would have left two CLIs and a wrong-binary hazard on shared
  resources; they were converted together for that reason.
- Ecosystem material (module docs, tutorials, assistant output) still defaults to
  `terraform`; occasional mental translation on `deploy/` contributions.
- The pre-existing drift found while establishing the baseline is **unrelated to this
  change** and is filed separately — it is not resolved here, and this amendment
  deliberately does not apply the stack.

### Alternatives considered

- **Stay on Terraform.** Zero work, zero risk, and *no compliance problem to fix* — the
  honest null option. Rejected on coherence plus the #505 timing argument, not on
  necessity.
- **A new ADR (0040) instead of amending in place.** The index convention pairs an
  amendment with a *new* ADR, but that convention exists for decisions large enough to
  stand alone. This changes one tool in one decision and would read as an orphan record;
  amending 0024 keeps the rationale where a reader of the deploy ADR will actually find
  it. Precedent: ADR 0038 §5, amended in place by #952.
- **Rename `deploy/terraform/` → `deploy/opentofu/`.** Rejected — see Decision above.
- **Fold state encryption into the same change.** Rejected: it mixes a
  provably-zero-diff swap with a state-rewriting change and burns the rollback path.
