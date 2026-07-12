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
  > **`artifacts_uri` is the base prefix, not the full published path** — DataQ appends
  > `/<job>/latest/…` itself. If your publisher writes
  > `adls://<account>/<container>/<prefix>/dbt/latest/run_results.json`, register
  > `artifacts_uri: adls://<account>/<container>/<prefix>` with `jobs: ["dbt"]`. Pasting the
  > publisher's own output variable verbatim usually double-counts the job segment (the poll
  > then looks in `<prefix>/dbt/dbt/latest/` and finds nothing).
- Copy the callback snippet
  ([`integrations/dbt/`](https://github.com/TheurgicDuke771/DataQ/tree/main/integrations/dbt))
  and run it right after `dbt build`, pointed at that run's `run_results.json`. It HMAC-signs
  (same scheme as Airflow) and POSTs to `/api/v1/orchestration/events/dbt`.
- **Fallback:** DataQ polls `<artifacts_uri>/<job>/latest/run_results.json` on the 10-minute
  cadence, so a build is still recorded even if the callback never fires. Grain is
  **job-level** (one `pipeline_run` per dbt job build).

Store the HMAC signing key as the `dbt-webhook-secret` in DataQ's secret store; the webhook
URL is shown in **Settings → Webhooks** like the others.

### Lineage from `manifest.json` (ADR 0034)

Alongside `run_results.json`, DataQ reads each job's **`manifest.json`** — the sibling artifact
at `<artifacts_uri>/<job>/latest/manifest.json` — for **table-level lineage**. On a **succeeded**
dbt run, the ingest path (webhook immediately, or the 10-min poll as fallback) enqueues an
**async `refresh_dbt_lineage` worker task** — the artifact download + parse + upserts run off the
webhook/poll thread, so ingestion never blocks. The task parses the model dependency graph and
refreshes the `lineage_edges` cache, which powers the blast-radius view (a failing asset's
downstream dependents). Stale edges from a previous refresh of **that connection** are pruned;
another dbt project's edges are never touched (edges are provenance-scoped to the refreshing
connection).

dbt's manifest has no OpenLineage **namespace** (no warehouse account/host), so DataQ **infers**
it from assets you've already resolved via suite targets for the same table names — env-strict
(it never anchors a QA project into the PROD namespace) and majority-wins with a deterministic
tie-break. **Skip conditions** (the refresh no-ops, fail-soft): no manifest published yet, no
matching asset to anchor from, or an empty/too-old manifest. For a **greenfield project** with no
suites yet — or a multi-database project — set **`lineage_namespace`** on the dbt connection
config (the OpenLineage namespace verbatim, e.g. `snowflake://<account>`) to pin the anchor and
bypass the inference entirely.

### Lineage from a catalog — the `LineageProvider` seam (ADR 0034, #762)

The dbt slice above sees only what the dbt manifest models. A **governance catalog** sees
more — including consumers that emit no OpenLineage themselves (a Power BI report now sitting
downstream of a monitored mart). DataQ pulls that graph through a provider-agnostic
**`LineageProvider`** seam (mirroring the `OrchestrationProvider` discipline — no
provider-specific branching in service code), caching the pulled edges into the same
`lineage_edges` table with `source='marquez'`. The seam's graph carries a **node kind** per node
(`dataset` today; `job` collapsed through; `bi_report`/`dashboard` reserved) — so **downstream
nodes are not assumed to be tables**, and a BI/dashboard node round-trips the moment a
capable catalog (Purview/DataHub) lands behind the seam, with no schema or query change.

- **Reference implementation: Marquez** (Apache-2.0). Pull = `GET {MARQUEZ_URL}/api/v1/lineage?
  nodeId=dataset:{namespace}:{name}&depth=N`; identity matches DataQ assets byte-for-byte because
  both use the OpenLineage naming spec. Fail-soft (5 s timeout, node cap, depth clamp) — and a
  dead catalog is treated as **unavailable**, not as empty lineage: the refresh skips pruning for
  that pass (an outage can never wipe the cached graph), never surfaces an error, and stale edges
  wait for the next clean pull.
- **Deferred behind the same seam** (do not build): **DataHub** (8 GB-RAM footprint — bring your
  own; our emitter already feeds its OL receiver), **OpenMetadata** (SDK is source-available,
  blocked by ADR 0031; REST-only would need its own ADR), **Purview** (Azure-only, parked).
- **Config (dark by default).** Set `LINEAGE_PROVIDER=marquez` + `MARQUEZ_URL=http://marquez:5000`
  in `.env.app`. The daily **`refresh_lineage_pull`** beat task then seeds a pull from each known
  asset, collapses jobs to dataset→dataset edges, and upserts them. Pulled edges carry a **NULL
  `connection_id`** (a catalog pull has no orchestration connection) and dedupe on the
  `(upstream, downstream, source) WHERE connection_id IS NULL` partial unique index; their prune is
  scoped to `(source='marquez', connection_id IS NULL)`, so **dbt-sourced edges are never touched**
  — the two sources coexist as distinct rows for the same table pair, and the blast-radius walk
  spans both.

**Local round-trip (producer → Marquez → pull → graph).** The reference compose stack ships a
Marquez (+ its own Postgres) behind an opt-in profile so a plain `docker compose up` is unaffected.

> **DataQ's own emission is not enough to produce lineage — and that is by design.** A DataQ run
> *reads* the asset it validates, so the emitter (#758) sends it as a job **input** and emits no
> outputs. That makes the DQ job a consumer in the catalog; it creates **no dataset→dataset edge**.
> Edges come from whatever actually *transforms* data — dbt's OpenLineage integration, a Spark or
> Airflow OL emitter, or any producer posting a RunEvent with both `inputs` and `outputs`. Point
> Marquez at one of those (or post the events yourself, as below) and the pull has a graph to find.

```bash
# 1. start Marquez (2 extra containers) alongside the app; it serves on host :5002
docker compose --profile lineage up -d marquez-db marquez
curl -sf http://localhost:5002/api/v1/namespaces   # ready check

# 2. get a producer chain INTO Marquez. Any OL producer will do; to verify the seam
#    without one, post RunEvents whose inputs/outputs use the SAME OpenLineage
#    namespace + name as your DataQ assets (that identity match is the whole point —
#    see ADR 0034). One event per transformation, e.g.
#       {namespace}: snowflake://<account>
#       ORDERS_HEADER -> STG_ORDERS -> MART_ORDER_REVENUE -> BI dashboard
curl -X POST http://localhost:5002/api/v1/lineage -H 'Content-Type: application/json' -d '{
  "eventType": "COMPLETE", "eventTime": "2026-07-12T19:05:00.000Z",
  "run": {"runId": "<uuid>"}, "job": {"namespace": "dbt-local", "name": "build_stg_orders"},
  "inputs":  [{"namespace": "snowflake://ACCT", "name": "DB.RETAIL.ORDERS_HEADER"}],
  "outputs": [{"namespace": "snowflake://ACCT", "name": "DB.ANALYTICS_STG.STG_ORDERS"}],
  "producer": "https://example/local-verify"}'

# 3. point the pull at Marquez (dark until you do) and refresh. In .env.app:
#      LINEAGE_PROVIDER=marquez
#      MARQUEZ_URL=http://marquez:5000        # in-network; :5002 is host-side only
#    (add OPENLINEAGE_URL=http://marquez:5000/api/v1/lineage too if you also want
#     DataQ's own DQ-job events in the catalog — they add no edges, see the note above)
docker compose exec worker python -c \
  "from backend.app.worker.tasks import refresh_lineage_pull; print(refresh_lineage_pull())"

# 4. the pulled edges land in lineage_edges with source='marquez' (dbt edges untouched),
#    and render in the asset's lineage graph (#805).
docker compose exec postgres psql -U dataq -d dataq \
  -c "select source, count(*) from lineage_edges group by source;"
```

**Verified locally 2026-07-12 (#804).** Off-by-default confirmed (`get_lineage_provider()` → `None`
with no env); env-configured, the pull walked each known asset as a seed, **cached 3
`source='marquez'` edges** alongside the existing 8 `source='dbt'` ones (neither pruned the other),
and the asset's lineage graph rendered the full chain — including a **BI dashboard node that only
Marquez knew about**, which dbt's manifest never produced. Seeds that Marquez has never heard of
log a fail-soft `marquez_lineage_pull_failed` warning per node and are skipped, exactly as intended.

**Not yet verified against a deployed app.** The Azure subscription lapsed (2026-07-12), so there is
no running prod to attach a catalog to, and the harness Flow-A chain needs live Snowflake. The
prod-side stand-up — a Marquez the deployed app can reach, plus enabling OL emission on the harness
flows — is deferred until infra returns; the app side needs no further change (it is one env var).

Marquez's newest release is 0.50.0 (2024-10); its slow cadence is an **accepted low risk** for a
dev-time reference consumer (ADR 0034) — the `/api/v1/lineage` contract has been stable across
releases, and production lineage is a bring-your-own catalog behind the same seam.

## Wiring a trigger

In the UI, open a suite's **Triggers** and bind it to a `(provider, pipeline/DAG, env)`.
When that pipeline next succeeds, the suite runs automatically and you'll see the run
correlated to the pipeline run on the **Results → Pipelines** view.
