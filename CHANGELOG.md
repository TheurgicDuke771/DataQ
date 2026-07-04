# Changelog

All notable user-facing changes to DataQ are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

**Cadence:** one curated entry per **minor/major release, written at tag time** —
not per PR. Patch releases get an entry only when the change is user-visible or
security-relevant. Raw material comes from the squashed conventional commits
(`git log v<prev>..HEAD --oneline`); curate it down to what a deployer or user
would act on. The step lives in the release checklist in
[docs/runbook-faq.md](docs/runbook-faq.md).

## [Unreleased]

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

[Unreleased]: https://github.com/TheurgicDuke771/DataQ/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/TheurgicDuke771/DataQ/releases/tag/v1.0.0
