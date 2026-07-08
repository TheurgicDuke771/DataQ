# Changelog

Notable, user-facing changes. Dates are the release/merge date. This is a curated summary —
the per-PR history lives in the repo's commit log and pull requests.

## Unreleased — v1.1 (in progress)

Portability, auto-monitors, and polish on top of v1:

- **dbt as a third orchestration provider** — observe dbt builds and trigger suites on
  success, via a post-build HMAC callback + a `run_results.json` artifact poll (ADR 0029).
- **Freshness & volume monitors** — the first auto-monitor kinds (is the data stale? did the
  load land whole?) on SQL datasources.
- **Vendor-neutral observability** — OpenTelemetry logs + traces, exportable to Application
  Insights and/or a generic OTLP endpoint.
- **Personal access tokens (PATs)** — `dq_live_` tokens for headless / AI-client use, on REST
  and MCP (ADR 0026).
- **Workspace-admin visibility** extended to the MCP tools + schedules; **dry-run preview**
  extended to every datasource.
- **Run failure reasons** — a run that fails to execute now shows a redaction-safe reason.
- **Secret lifecycle** — connection delete cleans up its stored secret.
- Docs: Features overview, Recommended usage, this changelog, security, REST API,
  troubleshooting, deployment, and a first-suite tutorial.

## v1.0.0 — 2026-07-04

First production release. Deployed to Azure Container Apps.

- **Data-quality checks** across four datasources — Snowflake, Unity Catalog, ADLS Gen2,
  AWS S3 — on Great Expectations, with a catalog-driven check editor and custom SQL.
- **Suites** with per-suite view/edit sharing and export/import for env promotion.
- **Runs** — run-now with live progress + cancel, cron **scheduling**, and **pipeline
  triggers** from ADF & Airflow.
- **Results & dashboard** — severity tiers, a health score, trends, and **column-aware
  redacted** failing-row samples.
- **Alerting** — Teams / Slack / email with severity routing, dedup, and per-check snooze.
- **AI assistants** — an 8-tool MCP server for Claude / Copilot / Cursor.
- **SSO** (OIDC) and secrets in a managed vault; the frontend is the sole public surface.

See the [Features](features.md) page for the full current capability set.
