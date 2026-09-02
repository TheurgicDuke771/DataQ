# Changelog

All notable user-facing changes to DataQ are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

**Cadence:** one curated entry per **minor/major release, written at tag time** —
not per PR. Patch releases get an entry only when the change is user-visible or
security-relevant. Raw material comes from the squashed conventional commits
(`git log v<prev>..HEAD --oneline`); curate it down to what a deployer or user
would act on. The step lives in the release checklist in
[docs/site/operate/runbook-faq.md](docs/site/operate/runbook-faq.md).

## [Unreleased]

## [1.1.0] — 2026-08-21

The post-v1 cycle (2026-07-04 → 2026-08-21, six weeks + a stretch week). First tagged
2026-08-15; the tag was moved to the true cycle close after the stretch week landed the
RBAC, MCP-expansion, security-audit and compliance tracks. The curated user-facing entry
lives on the docs site — [docs/site/reference/changelog.md](docs/site/reference/changelog.md) — and in the GitHub
Release body; headlines:

### Added

- **Monitor kinds completed:** `comparison` (cross-dataset reconciliation, ADR 0015),
  `schema_drift`, and `anomaly` (rolling z-score baseline) join freshness/volume — all
  classified on the ADR 0038 **DQ-dimension** axis and rolled into asset scorecards.
- **Assets, lineage & incidents (ADR 0034/0037):** the monitored table/file is a
  first-class entity with health rollup, OpenLineage-named identity, pulled lineage
  (dbt manifests, warehouse `GET_LINEAGE`, `LineageProvider`/Marquez), stateful deduped
  incidents with evidence cards, and the navigation inversion (assets lead).
- **AWS as a second live reference deployment** (ECS Fargate + RDS + ElastiCache +
  Cognito + Secrets Manager behind CloudFront) beside Azure; **OpenTofu** IaC; generic
  provider-neutral OIDC (ADR 0026 amendment).
- **Workspace roles — Admin / Member / Viewer (ADR 0033):** a second authorization axis
  beside per-suite grants; connection mutations are Admin-only. ⚠️ **Breaking:** Members
  lose connection-write — promote connection-managers to Admin before upgrading.
- **Email OTP sign-in (ADR 0032)** — IdP-less authenticator, the local/eval default;
  **PATs** (`dq_live_`) for headless/AI clients (ADR 0026); **rate limiting** (ADR 0035).
- **MCP server 8 → 46 tools** (23 read-only / 18 state-changing / 5 live-probe) with an
  honesty pass across all 46 — every tool states its blind spots as fields, not prose.
- **Compliance controls:** append-only config + data-access **audit trail** (ADR 0041),
  warehouse-tag **PII classification** feeding the redaction ladder, fail-closed
  classification mode, residency declaration + IaC region postcondition, encryption
  posture documentation, and the compliance document set (sub-processors, DPIA input,
  breach runbook, DPA/BAA templates).
- **Scale-aware execution:** sampling + partition batching + an OOM guardrail that
  refuses instead of SIGKILLing, with sampled-ness recorded per result.
- **Iceberg** as a fifth datasource (native `pyiceberg`, ADR 0030); **S3-compatible
  stores** (MinIO/R2/Wasabi/…); **dbt** as a third orchestration provider (ADR 0029);
  **OpenBao/Vault-API secret store** (ADR 0039); **warehouse inventory sync** (ADR 0040).

### Security

- Self-registration closed at the IdP **and** an app-side OIDC allowlist enforced per
  request; browser security headers (CSP, HSTS, …) on every response; CloudFront WAF +
  edge caching; nginx-enforced origin secret; backend image runs as **non-root**;
  Iceberg catalog credential moved out of `catalog_uri` (rotation required — see the
  docs changelog's Fixed entry); credential-redirect guard: a stored secret is never
  forwarded to a destination the caller just changed.

## [1.0.0] — 2026-07-04

First production release. Single-tenant data quality monitoring platform on
Great Expectations (GX Core), deployed to Azure Container Apps.

### Added

- **Datasources (4):** Snowflake (DEV/QA/UAT), ADLS Gen2, AWS S3, and Unity
  Catalog (Databricks) behind a common `ConnectionAdapter`/`CheckRunner` seam,
  with a connection manager UI, connection testing, re-auth, and per-connection
  version history. All credentials live in Azure Key Vault — never in the DB.
- **Checks & suites:** catalog-driven GX expectation editor + Monaco custom-SQL
  checks (read-only, single-statement guardrails — ADR 0019); freshness & volume
  monitor kinds (ADR 0012); severity tiers `warn`/`fail`/`critical` derived from
  user thresholds banding the GX unexpected-% (ADR 0005/0016); dry-run preview;
  column profiler; per-check version history; suite export/import.
- **Execution:** async Celery runs across all datasource types with live
  per-check progress, cancel, flat-file batch resolution (absent batch → skip,
  not failure), a stuck-run reaper, and cron scheduling (DST-aware, no-backfill).
- **Orchestration (monitor + trigger only, never datasources):** ADF and Apache
  Airflow behind one `OrchestrationProvider` interface — webhook receivers
  (shared-secret / HMAC-signed), a 10-minute polling fallback, downtime gap
  recovery, and trigger bindings that start suite runs on pipeline success.
- **Results & dashboard:** in-app Results page with run drill-down,
  observed-vs-expected, PII-redacted failing-row samples (column-aware policy +
  retention purge), pipeline-runs correlation, and a monitoring dashboard with
  health score, pass-rate, and trends.
- **Alerting:** MS Teams, Slack, and email publishers behind the
  `ResultPublisher` seam with severity-aware routing, dedup, per-check snooze,
  and per-suite delivery config.
- **Access model:** Azure AD SSO via a generic OIDC contract (`DATAQ_AUTH_*`,
  no MSAL — ADR 0028); suite-level sharing (`owner`/`edit`/`view`); implicit
  workspace-admin (ADR 0027) with an admin control centre.
- **AI integration:** 8 curated FastMCP tools at `/mcp`, validated with the same
  Azure AD bearer token, fail-closed without auth (ADR 0008).
- **Deployment:** in-repo Terraform for Azure Container Apps (API internal,
  frontend the sole public surface), GHCR multi-arch images, GitHub Actions
  build→migrate→deploy pipeline, App Insights logs + OpenTelemetry traces.

[Unreleased]: https://github.com/TheurgicDuke771/DataQ/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/TheurgicDuke771/DataQ/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/TheurgicDuke771/DataQ/releases/tag/v1.0.0
