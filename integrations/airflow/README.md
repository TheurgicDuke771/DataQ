# DataQ ↔ Apache Airflow — DAG callback snippet

This is the **producer** half of DataQ's Airflow integration. DataQ exposes a
signed webhook receiver at `POST /api/v1/orchestration/events/airflow`
([ADR 0007](../../docs/adr/0007-airflow-callback-model.md)); this snippet is what your
DAGs POST to it. DataQ can't mutate your DAGs, so you add this yourself — it's
copy-paste, stdlib-only, and fail-safe (a delivery failure never breaks a DAG).

> **Webhook vs. polling.** Callbacks are the near-real-time channel. For DAGs
> that don't adopt this snippet, DataQ's `dagRuns` REST **polling fallback**
> (every 10 min, lands in Week 5) backfills run status — at the cost of latency.
> Adopting the snippet is what makes failure detection and trigger-on-success
> prompt.

## 1. Provision the signing key

The snippet signs each request with HMAC-SHA256. The key must be the **same**
value on both sides:

- **DataQ side:** stored in Key Vault as `airflow-webhook-secret` (resolved via
  `settings.airflow_webhook_secret_name`; in dev, the `KV_SECRET_AIRFLOW_WEBHOOK_SECRET`
  env var).
- **Airflow side:** exposed to your workers as `DATAQ_WEBHOOK_SECRET`.

Generate one and set it in both places:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 2. Install the snippet

Copy [`dataq_airflow_callback.py`](dataq_airflow_callback.py) into your Airflow
`dags/` folder (or anywhere on the workers' `PYTHONPATH`).

## 3. Configure the environment

The snippet reads three environment variables at call time:

| Variable | Required | Purpose |
|---|---|---|
| `DATAQ_WEBHOOK_URL` | yes | Full receiver URL, e.g. `https://dataq.example.com/api/v1/orchestration/events/airflow` |
| `DATAQ_WEBHOOK_SECRET` | yes | HMAC signing key — the same value as DataQ's `airflow-webhook-secret` |
| `DATAQ_AIRFLOW_BASE_URL` | recommended | This Airflow's webserver root, e.g. `https://airflow.example.com`. **Must match the `base_url` of the Airflow connection registered in DataQ** — that's how DataQ attributes the run. Falls back to Airflow's `[webserver] base_url` if unset. |

## 4. Wire the callbacks onto a DAG

Attach them at the **DAG** level (not the task level) so they fire once per
DAG-run:

```python
from dataq_airflow_callback import on_dataq_success, on_dataq_failure

with DAG(
    dag_id="load_finance",
    on_success_callback=on_dataq_success,
    on_failure_callback=on_dataq_failure,
    # ... schedule, default_args, etc.
):
    ...
```

That's it. On every DAG-run completion DataQ records the run in `pipeline_runs`;
on **success**, any enabled `trigger_binding` for this DAG fires the bound suite.
Failures are recorded and alert, but never trigger a run (ADR 0004).

## What gets sent

A compact JSON body — the snippet owns this shape, and DataQ's
`AirflowProvider.parse_event` consumes it:

```json
{
  "dag_id": "load_finance",
  "run_id": "manual__2026-06-01T00:00:00+00:00",
  "state": "success",
  "base_url": "https://airflow.example.com",
  "start_date": "2026-06-01T00:00:00+00:00",
  "end_date": "2026-06-01T00:05:00+00:00"
}
```

`state` is `success` or `failed`; `start_date` / `end_date` / `error` are
included when available. The `X-DataQ-Signature` header carries the hex HMAC over
the exact bytes above.

## Troubleshooting

The snippet logs to the Airflow task/processor logger and never raises:

- **`DataQ callback skipped: … not set`** — `DATAQ_WEBHOOK_URL` / `DATAQ_WEBHOOK_SECRET`
  aren't in the worker environment.
- **`DataQ callback rejected: http=401`** — signature mismatch: the two
  `DATAQ_WEBHOOK_SECRET` / `airflow-webhook-secret` values differ.
- **`http=200` but no run in DataQ** — `DATAQ_AIRFLOW_BASE_URL` doesn't match any
  registered Airflow connection's `base_url`, so the event is accepted but
  unattributable (returns `{"status": "ignored"}`).
