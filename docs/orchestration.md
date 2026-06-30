# Orchestration integration (ADF & Airflow)

DataQ **observes** your pipelines and can **run check suites when they finish** — it does
not run the pipelines. Both Azure Data Factory and Apache Airflow sit behind one
`OrchestrationProvider` interface, so the behaviour is identical.

## What DataQ does with a pipeline

1. **Monitor** — every pipeline/DAG run is recorded in `pipeline_runs`.
2. **Detect failures** near-real-time via a webhook, with a 10-minute polling fallback.
3. **Trigger on success** — if a successful run matches an enabled **trigger binding**
   (`provider` + `pipeline/DAG id` + `env` → `suite_id`), DataQ queues that suite.
   *Failures alert but never trigger a run.*

## ADF

Azure Monitor raises an alert on pipeline events → an **Action Group webhook** POSTs to
`/api/v1/orchestration/events/adf` (shared-secret authenticated). Succeeded runs are also
picked up by the 10-min poll against the ADF REST API. To get the exact webhook URL to
paste into the Action Group (host + `?token=` secret, assembled from the live deployment
and Key Vault rather than by hand), see **One-time provisioning → step 5** in the
[deployment guide](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md).

## Airflow

Add the provided callback snippet
([`integrations/airflow/`](https://github.com/TheurgicDuke771/DataQ/tree/main/integrations/airflow))
to your DAGs — its `on_success_callback` / `on_failure_callback` HMAC-signs and POSTs to
`/api/v1/orchestration/events/airflow`. Polling the Airflow REST API is the fallback.

## Wiring a trigger

In the UI, open a suite's **Triggers** and bind it to a `(provider, pipeline/DAG, env)`.
When that pipeline next succeeds, the suite runs automatically and you'll see the run
correlated to the pipeline run on the **Results → Pipelines** view.
