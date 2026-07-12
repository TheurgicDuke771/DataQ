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
  - **Amends** *(optional)* — `ADR-NNNN` this decision partially overrides (whole-ADR replacement uses `Supersedes` instead). Pair it with an inline `> **Amendment (date, ADR-NNNN):** …` blockquote at the top of the amended ADR and an "(amended by NNNN — …)" note on its index Status, so the override is visible where readers actually look. Precedents: ADR 0028 (amends 0024), ADR 0012's amendment blockquote, ADR 0031 (amends 0013).
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
| [0013](0013-marketplace-distribution-and-anti-lock-in.md) | Marketplace distribution (customer-deployed BYOL) and anti-vendor-lock-in guardrails | Accepted (amended by 0031 — §5 licensing line + licensed-revenue framing) |
| [0014](0014-reconciliation-comparison-check-kind.md) | Cross-dataset reconciliation as a `comparison` check kind (reuse FastAPI_DataComparison engine) | Accepted |
| [0015](0015-two-connection-comparison-check-model.md) | Two-connection comparison check model — suite stays single-connection and supplies the **target under test**; a `comparison` check adds one **source** ref (`checks.source_connection_id` FK) + suite-target-shaped `config.source`; execution via a new `DatasetReader` seam + the ported FDC engine, row-cap fail-fast; report files derived on-demand, never stored; **no** connection→connection generalization (0030 Option B stays deferred) | Accepted (built #791–#795, 2026-07-12) |
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
| [0026](0026-auth-api-keys-and-principal-seam.md) | DataQ-issued API keys (PATs) as a second authenticator behind the `get_current_user` seam — REST + `/mcp` identically; phase 1 = user-scoped PATs (`dq_live_…`, sha256-at-rest, show-once, uniform 401, mandatory expiry, owner-cascade); service-account principals = phase 2 (deferred); HTTP Basic rejected | Accepted (phase 1 built 2026-07-04, #461; amended by 0032 — email-identity slice of the phase-2 principal question) |
| [0027](0027-suite-permission-model-workspace-admin.md) | Suite permissions — workspace-admin is implicit `admin` on every suite (governance/break-glass); drop grantable suite-admin; normal users get owner/edit/view; workspace-admin gets workspace-wide visibility (supersedes #411/#412) | Accepted (amended by 0033 — admin source → stored `users.role`; Viewer share-cap) |
| [0028](0028-cloud-neutral-image-runtime-config-generic-oidc.md) | Cloud-neutral image — one multi-arch frontend image, nothing baked; auth config injected at runtime (`window.__DATAQ_CONFIG__` via nginx envsubst) behind a generic `DATAQ_AUTH_*` contract; bypass fail-closed (explicit `DATAQ_AUTH_MODE=bypass` only); replace MSAL with a generic OIDC client validated against Azure; frontend SWA→Container App (amends 0024); AWS/GCP IaC post-v1 (#505) | Accepted (amended by 0032 — `otp` mode + cookie session credential) |
| [0029](0029-dbt-orchestration-provider.md) | dbt as a **third** `OrchestrationProvider` (mirrors the Airflow callback model 0007) — HMAC webhook + artifacts poll of `run_results.json` (adls/s3/file); binds to dbt's universal surface (no host API); job-level grain; migration widens the connection-type/provider/dedup value-sets (#611) | Accepted |
| [0030](0030-iceberg-native-read-path.md) | Apache Iceberg — engine-level read (Snowflake/UC iceberg tables) is free & zero-code; the only new build is a **native `pyiceberg` read** (v2 baseline, v3 deferred) behind a thin `IcebergCheckRunner` (scan → DataFrame → `gx_runner`); new **self-contained** `iceberg` connection type (Option A: own catalog + storage credential — independent lifecycle, cascade-safe), Option B two-connection ref deferred to 0015; reads Delta UniForm too; native impl deferred (#286) | Accepted (native read shipped #722; introspection #721) |
| [0031](0031-oss-byol-distribution-licensing.md) | Distribution licensing — **free open-source (MIT) + customer-deployed BYOL**; no entitlement/license-key (amends 0013: supersedes its §5 licensing-model line + licensed-revenue framing); marketplace listings are free offers of the OSS artifacts; THIRD-PARTY-NOTICES/SBOM in images + releases; standing no-strong-copyleft dependency guardrail (CONTRIBUTING rule 40) | Accepted |
| [0032](0032-email-otp-signin.md) | Email OTP sign-in — passwordless **third authenticator** behind `get_current_user` (`dq_sess_` cookie sessions, PAT-style sha256-at-rest); auth-mode ladder `bypass · otp · oidc`, fail-closed startup; mandatory signup allowlist (no open registration); one user row per normalized email (aad_object_id → nullable, two-step); separate `AUTH_EMAIL_*` mailer + SMTP pre-flight; hard prereq = #725 auth-slice rate limiting (#738); amends 0026 (email-identity slice) + 0028 (mode enum + cookie credential) | Accepted (amended by 0033 — signup default role + widened trust statement) |
| [0033](0033-workspace-roles-rbac.md) | Workspace roles — **Admin / Member / Viewer** as stored `users.role` on the two-axis model (role × per-suite ladder, ladder untouched); **connection mutations Admin-only** (closes the workspace-global hole, breaking for Members); Viewer capped at `view`; `WORKSPACE_ADMIN_EMAILS` demotes to bootstrap/break-glass; in-app role management + last-admin guard; amends 0027 (admin source) + 0032 (signup default role) (#744) | Accepted |
| [0034](0034-asset-entity-openlineage-identity-lineage-pull.md) | Assets & lineage (gap G-d design, #596) — first-class **asset entity keyed by the OpenLineage dataset-naming spec** (namespace+name adopted verbatim, dbt-ol/Spark byte-compatible; DEV/QA = distinct assets, UI groups over `env`); **lineage emitted/pulled, never built** — OL emission w/ quality facets → dbt `manifest.json` → `lineage_edges` cache → `LineageProvider` seam (Marquez reference; OpenMetadata SDK license-blocked per 0031); incidents anchor to assets (one open per asset+check, evidence-card payload); asset/incident authz derived from suite grants | Accepted |
| [0035](0035-request-rate-limiting.md) | Request rate limiting (#725) — **app-level innermost HTTP middleware** (portable per 0028/0013, covers the `/mcp` mount a dependency can't, principal-aware where nginx isn't); fixed-window Redis counter (window index in the key → no EXPIRE race), **no new dep** (INCR over slowapi/limits); **fail-open**; endpoint classes `webhook`(per-IP) · `default`(per-`sha256(token)`) · `unauth`(per-IP), raw token never keyed/logged; `/healthz`+`OPTIONS` exempt; headers on 429 only; nginx `limit_req` out of scope; hard prereq for 0032's OTP (#738) | Accepted |
