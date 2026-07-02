# Observability & troubleshooting

## Logs & telemetry

- **Structured JSON logs** (structlog) everywhere, with a `request_id` correlated across
  FastAPI → Celery → Great Expectations. **PII is redacted at the logger level**, so
  failed-check sample rows can't leak into logs.
- **Azure Application Insights** captures logs + exceptions in deployed environments
  (gated on the connection string; off locally).
- **Request + task spans** (OpenTelemetry, Azure-exported) in deployed environments:
  every API request and Celery task run is a trace in App Insights, linked
  request → task, with a `dataq.request_id` attribute joining spans to the log lines.
  Health probes and the secret-bearing webhook URLs are excluded by design.

## Where to look

- **Dashboard** — workspace health score, pass-rate, run counts, and trends.
- **Results** — every run, drill into per-check pass/fail with observed-vs-expected and
  (PII-redacted) sample failing rows; a **Pipelines** tab correlates orchestration runs.
- **Run progress** — live check-by-check status for an in-flight run.

## Common failures

| Symptom | Likely cause / fix |
|---|---|
| Run stuck `queued` | Broker (Redis) unreachable at dispatch. A beat **reaper** fails runs left non-terminal past a threshold; re-run once the worker/broker is healthy. |
| Connection **Test** fails | Bad/expired credential or network egress — re-authenticate the connection; check the secret store. |
| Pipeline didn't trigger a suite | Only **successful** runs trigger; confirm an **enabled trigger binding** matches `(provider, pipeline/DAG, env)` exactly (the DAG/pipeline id is case-sensitive). |
| Checks all `skip` on a flat-file suite | The batch hasn't landed yet (no matching file) — expected; the run succeeds with skips. |
| Orchestration events not arriving | Webhook secret/HMAC mismatch — the 10-min poll is the fallback; verify the connection's secret, and copy the URL from **Settings → Webhooks** instead of assembling it. |
| A check reports `error`, not `fail` | Its evaluation threw (cast failure, missing column, SQL error) — an operational problem with the check/target, not a data breach. Fix the check config or the schema drift. |
| Scheduled run didn't happen at 9:00 | Check the schedule's **timezone** (9:00 in which zone?), whether it's **paused**, and whether the platform was down over the tick — missed ticks are [not backfilled](scheduling.md). |
| No alert for a red run | See the [notifications troubleshooting table](notifications.md#troubleshooting) — threshold, dedup, and snooze all gate delivery. |
| Freshness/volume check missing in the editor | Those monitors are SQL-only (Snowflake/UC) — not offered on flat-file suites ([#520](https://github.com/TheurgicDuke771/DataQ/issues/520)). |

## Operating notes

After a shared-Postgres delete/recreate, **restart dependent Container Apps** (the DB host
is a start-time secret snapshot). Full deploy + ops runbook:
[`deploy/README.md`](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md).
