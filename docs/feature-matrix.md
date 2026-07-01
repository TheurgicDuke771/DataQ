# Feature matrix

One-page reference: what runs where. For the concepts behind the columns, see
[Concepts](concepts.md) and [Datasources & checks](datasources-checks.md).

## Check kinds × datasources

| Check kind | Snowflake | Unity Catalog | ADLS Gen2 (files) | S3 (files) |
|---|:-:|:-:|:-:|:-:|
| GX expectations (column / table shape) | ✅ | ✅ | ✅ | ✅ |
| Custom SQL (rows returned = failures) | ✅ | ✅ | — | — |
| Freshness monitor (hours since latest timestamp) | ✅ | ✅ | — | — |
| Volume monitor (row count in range) | ✅ | ✅ | — | — |
| Column profiler (nulls, distinct, min/max, top values) | ✅ | ✅ | ✅ | ✅ |
| Dry-run preview | ✅ | ✅ | ✅ | ✅ |

Custom SQL and the freshness/volume monitors run a SQL query, so they're **SQL-datasource
only** (flat-file support is a tracked enhancement,
[#520](https://github.com/TheurgicDuke771/DataQ/issues/520)). Flat-file suites target a
file or a batch pattern (e.g. `orders_*.csv`) in CSV or Parquet.

## Ways a suite runs

| Mode | Where | Notes |
|---|---|---|
| Run now | Suite detail → Run panel | Live per-check progress + cancel |
| Cron schedule | Suite detail → Schedules | 5-field cron, IANA timezone, DST-aware, [no backfill](scheduling.md) |
| Pipeline trigger | Suite detail → Triggers | Runs on a pipeline/DAG **success** — ADF + Airflow, see [Orchestration](orchestration.md) |
| API / MCP | `POST /suites/{id}/run` · `trigger_suite_run` MCP tool | Same authz as the UI |

## Severity & results

| Capability | Notes |
|---|---|
| Severity tiers | warn / fail / critical, banded from the observed unexpected-% (ADR 0005/0016) |
| Operational statuses | `error` (evaluation threw) and `skip` (precondition unmet) are distinct from failures |
| Health score | Severity-weighted, on the Dashboard |
| Failing-row samples | Redacted column-aware before display (suite column policy + classifier) |
| Run history retention | Samples purged after the retention window; metric trends kept |

## Alerting

| Capability | Notes |
|---|---|
| Channels | Teams (workspace + per-suite webhook), Slack, email — [details](notifications.md) |
| Threshold | Per suite: fail-only / warn+ (default) / always |
| Routing | Severity-aware urgency; critical escalates |
| Dedup | First failure / escalation only; clean run resets |
| Snooze | Per check, N hours |

## Orchestration providers (not datasources)

| Provider | Failure detection | Trigger on success |
|---|---|---|
| Azure Data Factory | Azure Monitor alert → webhook (+10-min poll) | ✅ trigger bindings |
| Apache Airflow | DAG callback → HMAC webhook (+10-min poll) | ✅ trigger bindings |

## Interfaces

| Surface | What |
|---|---|
| Web UI | Dashboard · Connections · Suites · Results · Profile · Admin · Settings |
| REST API | Versioned `/api/v1` (Swagger in non-prod) |
| MCP | 8 curated tools at `/mcp` for AI assistants (ADR 0008) |
