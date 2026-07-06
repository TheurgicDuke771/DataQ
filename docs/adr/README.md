# Architecture Decision Records (ADRs)

Each ADR captures a single significant architecture decision: the context, the decision, the consequences, and the alternatives considered. New ADRs are append-only — supersede an old decision by adding a new ADR and marking the old one's status as `Superseded by ADR-NNNN`.

## Format

- File name: `NNNN-short-kebab-slug.md` (zero-padded 4-digit sequence)
- Frontmatter fields:
  - **Status** — one of `Proposed`, `Accepted`, `Deprecated`, `Superseded by ADR-NNNN` (title-case)
  - **Date** — `YYYY-MM-DD`
  - **Deciders** — who made the call
  - **Consulted** *(optional)* — stakeholders whose sign-off the decision needed (e.g. product owner for ADR 0005). Omit when none.
  - **Supersedes** *(optional)* — `ADR-NNNN` this decision replaces. Omit when none.
  - **Superseded by** *(optional)* — `ADR-NNNN` that later replaced this one. Add when the status flips to `Superseded by`.
- Sections: Context, Decision, Consequences, Alternatives considered, Related (optional)
- Keep each ADR short — 1–2 pages. If it grows past that, the decision is probably two decisions.

### Template

```markdown
# ADR NNNN — <title>

- **Status:** Proposed
- **Date:** YYYY-MM-DD
- **Deciders:** @handle
- **Consulted:** <stakeholder>   <!-- optional; omit when none -->
- **Supersedes:** ADR-NNNN       <!-- optional; omit when none -->
- **Superseded by:** ADR-NNNN    <!-- optional; add when superseded -->

## Context

## Decision

## Consequences

## Alternatives considered
```

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-trunk-based-branching.md) | Trunk-based branching with squash-merge into `main` | Accepted |
| [0002](0002-conventional-commits.md) | Conventional commits for PR titles and commit messages | Accepted |
| [0003](0003-gx-only-for-v1.md) | GX-only for v1; DQX deferred to v1.1 for DLT/streaming | Accepted |
| [0004](0004-orchestration-abstraction.md) | Unified `OrchestrationProvider` abstraction for ADF and Airflow | Accepted |
| [0005](0005-severity-tier-weights.md) | Severity tier weights (warn / fail / critical → health score) | Accepted |
| [0006](0006-adf-webhook-authentication.md) | ADF webhook authentication (shared secret in URL + hard-cutover rotation) | Accepted |
| [0007](0007-airflow-callback-model.md) | Airflow callback model (HMAC-signed webhook + polling fallback) | Accepted |
| [0008](0008-mcp-server.md) | FastMCP server at `/mcp` — Azure AD token validated (same token as REST); all 8 exposed as tools; thin wrappers reusing the service layer + per-suite authz; fail-closed without auth | Accepted |
| [0009](0009-flat-monorepo-layout.md) | Repo layout — flat monorepo (`backend/` + `frontend/`) | Accepted |
| [0010](0010-provider-agnostic-infrastructure-seams.md) | Provider-agnostic infrastructure seams (Azure is the default, not the architecture) | Accepted |
| [0011](0011-extensibility-seams-for-deferred-integrations.md) | Extensibility seams for deferred connectors and integrations | Accepted |
| [0012](0012-monitor-kind-seam.md) | Monitor-kind seam (`check.kind` discriminator + numeric metric storage) | Accepted |
| [0013](0013-marketplace-distribution-and-anti-lock-in.md) | Marketplace distribution (customer-deployed BYOL) and anti-vendor-lock-in guardrails | Accepted |
| [0014](0014-reconciliation-comparison-check-kind.md) | Cross-dataset reconciliation as a `comparison` check kind (reuse FastAPI_DataComparison engine) | Accepted |
| [0016](0016-severity-derivation-semantics.md) | Severity derivation semantics (band the unexpected-%, thresholds override GX success) | Accepted |
| [0017](0017-python-313-runtime-upgrade.md) | Upgrade Python runtime 3.11 → 3.13 (3.14 deferred — GX-capped); bundled with the Snowflake 3→4 CVE refresh | Accepted |
| [0018](0018-results-surface-and-grafana-deferral.md) | Results surface is an in-app page (suite-scoped authz + PII redaction); Grafana deferred to optional ops add-on | Accepted |
| [0019](0019-custom-sql-check-kind.md) | Custom-SQL checks ride `kind='expectation'` via GX `UnexpectedRowsExpectation` (no new kind); read-only validation + SQL-datasource gating | Accepted |
| [0020](0020-history-and-audit-strategy.md) | History/audit: per-entity Type-4 snapshot tables (`check_versions`, `connection_versions`) where config history is needed; no SCD-2; credentials never snapshotted; cascade-delete accepted; cross-entity audit log deferred | Accepted |
| [0021](0021-demo-test-data-environment-strategy.md) | Live test/demo-data environment (retail model, 3 reference flows) lives outside the repo — Terraform/mock-data/Databricks notebook not git-tracked; discharges the deferred live-warehouse/file smoke | Accepted |
| [0022](0022-week6-prototype-adoption-and-chart-library.md) | Week-6 prototype adoption — full 13-screen set as dedicated pages (Share is the only drawer; prototype wins on conflicts; Settings/Admin pulled into W6); chart library = recharts (lazy-loaded) | Accepted |
| [0023](0023-container-image-registry-ghcr.md) | Container image registry — GitHub Container Registry (GHCR) over ACR / Docker Hub (vendor-neutral, public package → anonymous ACA pull, reuses `GITHUB_TOKEN`); supersedes the #379 ACR scaffolding | Accepted |
| [0024](0024-app-deployment-infrastructure.md) | App deploy infra — in-repo Terraform; shares subscription/RG/ACA-env/Postgres with the harness; ACA api+worker+migrate-job + SWA-Standard linked same-origin `/api` + self-hosted Redis + KV (UAMI) + App Insights + AAD-app-reg OIDC | Accepted |
| [0025](0025-production-image-pip-slim.md) | Production image — multi-stage `python:3.13-slim` + pip (not conda; ~2.84GB→~1GB); conda stays the local-dev tool; amends the W1 conda lock | Accepted |
| [0026](0026-auth-api-keys-and-principal-seam.md) | DataQ-issued API keys (PATs) as a second authenticator behind the `get_current_user` seam — REST + `/mcp` identically; phase 1 = user-scoped PATs (`dq_live_…`, sha256-at-rest, show-once, uniform 401, mandatory expiry, owner-cascade); service-account principals = phase 2 (deferred); HTTP Basic rejected | Accepted (phase 1 built 2026-07-04, #461) |
| [0027](0027-suite-permission-model-workspace-admin.md) | Suite permissions — workspace-admin is implicit `admin` on every suite (governance/break-glass); drop grantable suite-admin; normal users get owner/edit/view; workspace-admin gets workspace-wide visibility (supersedes #411/#412) | Accepted |
| [0028](0028-cloud-neutral-image-runtime-config-generic-oidc.md) | Cloud-neutral image — one multi-arch frontend image, nothing baked; auth config injected at runtime (`window.__DATAQ_CONFIG__` via nginx envsubst) behind a generic `DATAQ_AUTH_*` contract; bypass fail-closed (explicit `DATAQ_AUTH_MODE=bypass` only); replace MSAL with a generic OIDC client validated against Azure; frontend SWA→Container App (amends 0024); AWS/GCP IaC post-v1 (#505) | Accepted |
| [0029](0029-dbt-orchestration-provider.md) | dbt as a **third** `OrchestrationProvider` (mirrors the Airflow callback model 0007) — HMAC webhook + artifacts poll of `run_results.json` (adls/s3/file); binds to dbt's universal surface (no host API); job-level grain; migration widens the connection-type/provider/dedup value-sets (#611) | Accepted |

## Pending (to be written in their respective weeks)

| # | Topic | Target week |
|---|---|---|
| 0015 | Two-connection check model (source + target refs for `comparison` checks) | When reconciliation build starts (post-v1) |
