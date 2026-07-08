# Orchestration integration (ADF, Airflow & dbt)

DataQ **observes** your pipelines and can **run check suites when they finish** — it does
not run the pipelines. Azure Data Factory, Apache Airflow, and dbt all sit behind one
`OrchestrationProvider` interface, so the behaviour is identical.

## What DataQ does with a pipeline

1. **Monitor** — every pipeline/DAG run is recorded in `pipeline_runs`.
2. **Detect failures** near-real-time via a webhook, with a 10-minute polling fallback.
3. **Trigger on success** — if a successful run matches an enabled **trigger binding**
   (`provider` + `pipeline/DAG id` + `env` → `suite_id`), DataQ queues that suite.
   *Failures alert but never trigger a run.*

## ADF

Azure Monitor raises an alert on pipeline events → an **Action Group webhook** (with the
**common alert schema enabled**) POSTs to `/api/v1/orchestration/events/adf`
(shared-secret authenticated). The alert names the factory/pipeline but no run id, so a
fired alert triggers an immediate targeted poll — the failed run lands in the pipeline
feed within seconds. Succeeded runs are picked up by the same poll on its 10-min cadence.

**Getting the webhook URL:** a workspace admin opens **Settings → Webhooks** in the app —
it shows the ready-to-paste inbound URL per provider (the ADF one embeds the shared
secret behind a reveal toggle; treat it as a credential). No hand-assembly from Key
Vault needed. Provisioning details: **One-time provisioning → step 5** in the
[deployment guide](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md).

## Airflow

Add the provided callback snippet
([`integrations/airflow/`](https://github.com/TheurgicDuke771/DataQ/tree/main/integrations/airflow))
to your DAGs — its `on_success_callback` / `on_failure_callback` HMAC-signs and POSTs to
`/api/v1/orchestration/events/airflow`. Polling the Airflow REST API is the fallback.

## dbt

dbt binds to dbt's **universal surface** — the `run_results.json` build artifact plus a
post-build callback — so it works with any dbt runner (Core, Cloud, an orchestrator step)
with no host API dependency (ADR 0029). dbt Core has no callback hook like Airflow's, so
you run a tiny **post-build wrapper**:

- Register a **dbt connection** (Connections → dbt) with its `project_name`, the `jobs` it
  publishes, and the `artifacts_uri` where builds land (`adls://…`, `s3://…`, or `file://…`)
  plus the store's read credential.
- Copy the callback snippet
  ([`integrations/dbt/`](https://github.com/TheurgicDuke771/DataQ/tree/main/integrations/dbt))
  and run it right after `dbt build`, pointed at that run's `run_results.json`. It HMAC-signs
  (same scheme as Airflow) and POSTs to `/api/v1/orchestration/events/dbt`.
- **Fallback:** DataQ polls `<artifacts_uri>/<job>/latest/run_results.json` on the 10-minute
  cadence, so a build is still recorded even if the callback never fires. Grain is
  **job-level** (one `pipeline_run` per dbt job build).

Store the HMAC signing key as the `dbt-webhook-secret` in DataQ's secret store; the webhook
URL is shown in **Settings → Webhooks** like the others.

## Wiring a trigger

In the UI, open a suite's **Triggers** and bind it to a `(provider, pipeline/DAG, env)`.
When that pipeline next succeeds, the suite runs automatically and you'll see the run
correlated to the pipeline run on the **Results → Pipelines** view.
