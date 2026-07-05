# DataQ ↔ dbt callback

Report dbt build runs to DataQ so it can record them in `pipeline_runs` and trigger
a DQ suite when a build succeeds (DataQ ADR 0004 / 0029). dbt is an **orchestration
provider** in DataQ — a workflow whose runs DataQ observes and reacts to — not a
datasource.

dbt Core has no callback context (unlike Airflow's `on_*_callback`), so this is a
tiny **post-build wrapper** you run right after `dbt build`, pointed at the run's
`run_results.json`.

## Setup

1. **Register a dbt connection in DataQ** (Connections → dbt) with:
   - `project_name` — a logical name for this dbt project (the callback's
     `DATAQ_DBT_PROJECT` must match it).
   - `artifacts_uri` — where the build publishes artifacts, for the poll fallback:
     `adls://<account>/<container>/<prefix>`, `s3://<bucket>/<prefix>`, or
     `file:///<path>`.
   - `jobs` — the job names this project publishes (each polled at
     `<artifacts_uri>/<job>/latest/run_results.json`).
   - the artifacts-store read credential as the connection secret (ADLS SAS / S3
     secret key; none for `file://`).
2. **Store the HMAC signing key** in DataQ's secret store as `dbt-webhook-secret`
   (the same value you set as `DATAQ_WEBHOOK_SECRET` below).
3. **Copy `dataq_dbt_callback.py`** next to your dbt build wrapper and invoke it
   after `dbt build`:

   ```bash
   dbt build
   python dataq_dbt_callback.py target/run_results.json   # never fails the build
   ```

4. **(Optional) bind a suite** to the job (Suites → Triggers): provider `dbt`,
   pipeline/job = your `DATAQ_DBT_JOB`, env, → suite. A successful build then
   queues that suite's run.

## Environment variables

| Var | Required | Meaning |
|---|---|---|
| `DATAQ_WEBHOOK_URL` | yes | `https://<dataq>/api/v1/orchestration/events/dbt` |
| `DATAQ_WEBHOOK_SECRET` | yes | HMAC signing key = DataQ's `dbt-webhook-secret` |
| `DATAQ_DBT_JOB` | yes | Job name — the trigger unit (`pipeline_or_dag_id`) |
| `DATAQ_DBT_PROJECT` | no | Project name; must match the connection's `project_name`. Falls back to the project parsed from `run_results.json` node ids. |

## Push vs poll

The callback is the **near-real-time** channel (a run shows up in DataQ within
seconds). If a build can't run the callback, DataQ's **10-min poll** reads the same
`run_results.json` from `artifacts_uri` as a fallback — so publishing artifacts to a
stable `<job>/latest/run_results.json` (plus timestamped copies) is enough on its
own. Both channels are idempotent on the dbt `invocation_id`, so a run is never
double-counted or double-triggered.

## Design notes

- **Stdlib-only** — no extra pip installs in your dbt image.
- **Fail-safe** — every error is swallowed and logged; a callback failure can never
  break your build.
- The HMAC is computed over the exact POSTed bytes, matching DataQ's constant-time
  check on the raw request body (identical scheme to `integrations/airflow/`).
