# Features

Everything DataQ offers, by area. This is the readable tour — for the terse
what-runs-where lookup see the [feature matrix](feature-matrix.md), and for how to turn
each feature on the recommended way see [Recommended usage](recommended-usage.md).

DataQ is a single-tenant **data-quality monitoring platform**: it runs checks against your
data stores, records every result, surfaces health on a dashboard, alerts the right people,
and reacts to your pipelines — all behind SSO, with an AI-assistant surface.

## Datasources

Stores you write checks **against** (see [Datasources & checks](datasources-checks.md)):

- **Snowflake** (DEV / QA / UAT) — key-pair or password auth.
- **Unity Catalog (Databricks)** — three-level `catalog.schema.table`.
- **ADLS Gen2** and **AWS S3** — flat files, a single object or a **batch** pattern
  (`orders_*.csv`, latest-or-specific) in **CSV** or **Parquet**.
- **Apache Iceberg** — native `pyiceberg` read straight from object storage, no query
  engine in front: `namespace.table` addressing, REST / SQL / Glue / Hive catalogs,
  credential-less catalogs supported, also reads Delta UniForm tables (ADR 0030).

## Checks & authoring

Every check is a Great Expectations expectation in v1 (`check.kind`), authored in the UI:

- **GX expectations** — column- and table-shape rules (not-null, unique, in-set, row
  count, value ranges, …) from the GX catalog, with a form editor.
- **Custom SQL** — an escape hatch for cross-column/join rules: rows returned = failures.
  Read-only, single-statement (enforced). SQL datasources only.
- **Freshness monitor** — hours since the latest timestamp (is the data stale?).
- **Volume monitor** — row count within an expected range (did the load land whole?).
  Freshness + volume are the auto-monitor kinds; run on monitor-capable datasources —
  the SQL datasources (Snowflake / Unity Catalog) plus Apache Iceberg, computed natively
  via a `pyiceberg` scan rather than SQL.
- **Column profiler** — nulls, distinct count, min/max, and top values for a column, on
  any datasource — the baseline you set thresholds from.
- **Dry-run preview** — run one check against live data **without persisting**, on every
  datasource with a runner (Snowflake / UC / flat files / Iceberg).
- **Severity thresholds** — band a check into **warn / fail / critical** from the observed
  unexpected-% (ADR 0005/0016); leave blank for binary pass/fail.
- **Check version history** — every edit is versioned and viewable per check.

## Suites

A **suite** is a named set of checks bound to one connection + target
([Datasources & checks](datasources-checks.md)):

- **Run target** — one table (SQL) or one file/batch (flat file) per suite.
- **Sharing** — per-suite **view / edit** grants (suite-level is the access model; no
  folder scopes). See [access & auth](#access-auth).
- **Export / import** — move a suite's definition between environments (author on DEV,
  import against PROD) instead of re-clicking checks.

## Running checks

Four ways a suite runs (all the same authz — [feature matrix](feature-matrix.md#ways-a-suite-runs)):

- **Run now** — with **live per-check progress** and **cancel**.
- **Cron schedule** — 5-field cron + IANA timezone, DST-aware, no backfill
  ([Scheduling](scheduling.md)).
- **Pipeline trigger** — runs automatically when a bound ADF / Airflow / dbt run **succeeds**
  ([Orchestration](orchestration.md)).
- **API / MCP** — `POST /suites/{id}/run`, or the `trigger_suite_run` MCP tool.

## Results, dashboard & history

- **Monitoring dashboard** — a severity-weighted **health score**, pass rate, run counts,
  average duration, per-day trends, and per-suite performance.
- **Results page** — every run with its per-check outcomes; drill into a run for
  observed-vs-expected values and **redacted failing-row samples**.
- **Severity + operational statuses** — warn / fail / critical for data outcomes, plus
  `error` (evaluation threw) and `skip` (precondition unmet) kept distinct from failures.
- **Failure reasons** — a run that fails to execute shows a redaction-safe reason
  (config / connectivity / permission), not a bare "failed".
- **Metric trends** — the numeric `metric_value` per check is kept for trend/baseline even
  after samples are purged by the retention sweep.

## Orchestration (monitor + trigger only)

DataQ **observes** pipelines and triggers suites on success — it never runs them, and these
are **not** datasources ([Orchestration](orchestration.md)):

- **Azure Data Factory** — Azure Monitor alert → webhook (+ 10-min poll).
- **Apache Airflow** — DAG callback → HMAC webhook (+ 10-min poll).
- **dbt** — post-build callback → HMAC webhook (+ 10-min `run_results.json` artifact poll).

All three sit behind one provider interface; **trigger bindings** map
`(provider, pipeline/DAG/job, env) → suite`.

## Alerting

Delivered when a run breaches its threshold ([Notifications & alerting](notifications.md)):

- **Channels** — Microsoft Teams (workspace + per-suite webhook), Slack, and email, with
  deep links + expected-vs-observed context.
- **Threshold** — per suite: fail-only / warn-and-worse (default) / always.
- **Severity routing** — urgency scales with severity; critical escalates.
- **Dedup** — you hear about a breakage once (and again on escalation); a clean run resets.
- **Snooze** — silence a known-broken check for N hours (history + re-fire preserved).
- **Per-suite config** — channels + threshold + recipients set per suite.

## Data protection

- **Column-aware redaction** — failing-row samples are the one place results can carry PII;
  a suite **column policy** (auto-derived by the classifier or set by hand) keeps
  non-sensitive breaches debuggable while PII columns stay masked.
- **PII-minimising retention** — samples are purged after the retention window; the
  aggregatable metric history survives.

## Access & auth

- **OIDC SSO** — provider-neutral (Azure AD validated); the web UI signs in via your IdP.
- **Personal access tokens (PATs)** — `dq_live_` tokens for headless / AI-client use, same
  authz as the user, on REST **and** `/mcp` ([API keys](api-keys.md), ADR 0026).
- **Suite sharing** — per-suite view / edit grants.
- **Workspace admin** — an allowlisted superuser role with workspace-wide visibility over
  every suite, its results, and schedules (ADR 0027) — keep the allowlist minimal.

## AI assistants (MCP)

A curated **8-tool MCP server** at `/mcp` lets Claude / Copilot / Cursor list suites, read
results, trigger runs, poll status, add checks, profile columns, and read the health score
and pipeline status — in natural language, with the same per-suite authz as the UI
([AI assistants](mcp-setup.md), ADR 0008).

## Observability

Structured JSON logs with `request_id` correlation (FastAPI → Celery → GX), PII redaction at
the logger, and **OpenTelemetry** traces + logs — exported to Azure Application Insights
and/or a generic OTLP endpoint ([Observability](observability.md)).

## Deployment & portability

Runs on **Azure Container Apps** today (API + worker + frontend; the frontend is the sole
public surface). Every cloud dependency sits behind a seam — OIDC auth, the secret store,
observability export, and the orchestration providers are each one implementation of a
provider-agnostic interface, so DataQ is not locked to Azure ([Architecture](architecture.md)).
