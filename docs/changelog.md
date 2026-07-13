# Changelog

Notable, user-facing changes. Dates are the release/merge date. This is a curated summary —
the per-PR history lives in the repo's commit log and pull requests.

## Unreleased — v1.1 (in progress)

Portability, auto-monitors, and polish on top of v1:

- **dbt as a third orchestration provider** — observe dbt builds and trigger suites on
  success, via a post-build HMAC callback + a `run_results.json` artifact poll (ADR 0029).
- **Apache Iceberg as a fifth datasource** — native `pyiceberg` read (v2 baseline; ADR
  0030, #716/#722), with natively-computed freshness/volume monitors, a column profiler +
  column listing, and dry-run preview (#721).
- **Freshness & volume monitors** — the first auto-monitor kinds (is the data stale? did the
  load land whole?) on SQL datasources plus Iceberg.
- **Vendor-neutral observability** — OpenTelemetry logs + traces, exportable to Application
  Insights and/or a generic OTLP endpoint.
- **Personal access tokens (PATs)** — `dq_live_` tokens for headless / AI-client use, on REST
  and MCP (ADR 0026).
- **Workspace-admin visibility** extended to the MCP tools + schedules; **dry-run preview**
  extended to every datasource.
- **Run failure reasons** — a run that fails to execute now shows a redaction-safe reason.
- **Secret lifecycle** — connection delete cleans up its stored secret.
- **Assets as the primary lens** — a data asset (table/file) is now a first-class entity
  with its own page: health rolled up across every suite that targets it, open incidents,
  and lineage. The dashboard and sidebar lead with assets; suites and runs link back to
  the asset they touch (ADR 0034).
- **Lineage graph** — an asset's provenance and blast radius render as one left-to-right
  graph, one column per hop, with clickable nodes.
- **Asset browse by source** — drill down datasource → database → schema → table, with a
  flat searchable table as the second lens.
- **Two health axes on an asset** — "could DataQ reach the datasource?" is shown separately
  from "is the data good?", so an unreachable datasource no longer masquerades as a data
  failure, and a run that evaluated nothing no longer reads as a green pass.
- **Datasources read as names** — the UI shows `Snowflake · ACCT` / `ADLS · account/container`
  / `iceberg_catalog` instead of the raw connection string (an Iceberg namespace is a full
  DSN). The raw identifier stays available on hover and via copy.
- **Mobile** — the sidebar becomes an overlay drawer on a narrow viewport, and the share /
  edit panels reflow so their controls stay on screen (previously the "Add" button in the
  share drawer was painted off the right edge, making a suite unshareable from a phone).
- **A broken orchestration poll now tells you** — an expired credential silently stopped
  pipeline-run ingest, suite triggering, and lineage refresh for six days in our own
  production. A failing poll is now a fact about the connection: the connections list badges
  it with a failure count, the lineage panel warns instead of showing a confident empty
  graph, and after 3 consecutive failures an alert is pushed to the workspace channel — once
  on the way down and once on recovery, never once per poll. The reason is always the
  **classified** one, never raw error text (the real failure carried a SAS token in its
  message).

### Fixed

- **Iceberg catalog credential exposure** — the SQL-catalog password had to be carried
  inline in the `catalog_uri` (the connection type had only one secret slot), which meant
  it was persisted in the connections table, copied into the asset identity, returned by
  the API, and rendered in the UI. Iceberg connections now take a **second secret**
  (`catalog_secret_name`), the config **rejects** a password in `catalog_uri`, and a
  migration scrubs existing rows. **Action required:** an existing Iceberg connection whose
  `catalog_uri` carried a password must be re-pointed at a secret and **the password
  rotated** — treat it as disclosed.
- **`/assets` deep links 404'd** in the production image — the nginx rule for the hashed
  bundle directory also swallowed the `/assets` app route.

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
