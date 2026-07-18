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

#### When lineage is empty — check the poll before you check the graph

An empty lineage graph and a broken lineage pipeline once **looked identical in the UI** — the
asset said "No lineage recorded" whether that was the truth or whether DataQ had been unable to
read your artifacts for a week. That is fixed ([#828](https://github.com/TheurgicDuke771/DataQ/issues/828),
[#837](https://github.com/TheurgicDuke771/DataQ/issues/837)), but the underlying failure mode is
worth understanding, because it is quiet by nature.

**1. The artifacts-store credential is a single point of failure — now a *visible* one.** The dbt
connection's secret is the read credential for the artifacts store (an ADLS SAS, an S3 secret
key). When it expires, every poll fails with an auth error and DataQ stops reading
`manifest.json` — while the dbt builds keep succeeding and keep publishing artifacts nobody
consumes. DataQ now says so in three places: the **connections list badges** the failing poll with
its consecutive-failure count, the **lineage panel warns** instead of showing a confident empty
graph, and after 3 consecutive failures (~30 min) an **alert is pushed** to your workspace channel
(see [notifications](notifications.md#connection-poll-health-alerts)). Poll failures are also
logged as `orchestration_poll_failed` with `provider=dbt`. If lineage still looks wrong, **test
the dbt connection** (`Connections → the dbt connection → Test`): a red test is your answer.

**2. Fixing the credential is not enough — the backlog is already stranded.** The poll only
records builds whose `generated_at` falls inside its **15-minute lookback**. Once the credential
is restored the poll starts succeeding and still records *nothing*, because every build produced
during the outage is now older than the window. The artifacts are sitting right there in the
store, and DataQ will not read them.

> **Recovery:** re-run the dbt build (or wait for the next scheduled one) so a **fresh** artifact
> lands inside the poll window. The next poll ingests it, dispatches `refresh_dbt_lineage`, and
> the graph repopulates. Re-running the producer is currently the only way to recover a lineage
> gap.

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

- **Cross-producer identity — names do NOT join byte-for-byte** (#823, ADR 0034 amendment).
  OpenLineage has no case-folding rule, so two producers naming the same physical table need not
  agree: real `openlineage-dbt` emits `DATAQ_DB.ANALYTICS.mart_order_revenue` where DataQ's asset
  identity is `DATAQ_DB.ANALYTICS.MART_ORDER_REVENUE` (its `database`/`schema` come from the dbt
  profile, the table from the model filename). Namespaces *do* agree. So the pull **enumerates the
  catalog's own dataset names and seeds with those**, reconciling them to DataQ's assets through an
  engine-correct fold (`snowflake://` → upper, `unitycatalog://` → lower, and **no fold** for
  `abfss://`/`s3://`/Iceberg, which are case-**sensitive**). An exact name always wins over the
  fold, and an ambiguous fold (two catalog datasets folding to one key — Snowflake's quoted
  `"orders"` vs unquoted `ORDERS`) is **refused, never guessed**. Pulled identities are
  canonicalized on ingest, so a catalog can never fork a second asset for a table you already
  monitor.
- **Reference implementation: Marquez** (Apache-2.0). Pull = `GET {MARQUEZ_URL}/api/v1/lineage?
  nodeId=dataset:{namespace}:{name}&depth=N`, seeded from `GET .../namespaces/{ns}/datasets`.
  Fail-soft (5 s timeout, node cap, depth clamp) — and a
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

### Lineage from the warehouse — the `WarehouseLineageProvider` seam (#858)

The catalog pull above reconciles a byte-mismatched identity we **can't construct** (a producer
spells a name in some other case, so we enumerate the catalog and fold — #823). Reading the
**warehouse's own** lineage views sidesteps that entirely: the engine returns identifiers in its
own case — Snowflake `ACCOUNT_USAGE` upper, Unity Catalog `system.access` lower — which is
**byte-identical to DataQ's asset identity**, so no fold, no enumerate step. That is why warehouse
lineage is a **distinct seam** (`lineage.warehouse.WarehouseLineageProvider`, SQL → edge pairs),
not a second `LineageProvider`.

**Dark by default** (`WAREHOUSE_LINEAGE_ENABLED=true` to turn on): the views need a grant
(Snowflake `IMPORTED PRIVILEGES` on `SNOWFLAKE` — or the finer `SNOWFLAKE.GOVERNANCE_VIEWER`
database role, which covers the ACCESS_HISTORY/OBJECT_DEPENDENCIES tiers without blanket
ACCOUNT_USAGE; UC `SELECT` on `system.access`) the connection's principal may not have. A tier
the role can't read **skips with a "not authorized" reason and the ladder descends** (#902) —
tested live: a denied tier never aborts the tiers the role *can* read, and a fully-denied
account reports classified-unavailable, never a confident empty. When enabled, a **daily beat** (`refresh_warehouse_lineage`) refreshes every
Snowflake / Unity Catalog connection independently — one unreachable warehouse records a classified
error and never aborts the sweep — writing edges tagged `source='snowflake'` / `'unity_catalog'`
with the connection's id (the full-constraint regime, so a warehouse refresh never touches a dbt or
Marquez row).

**Column grain (#901, live-verified on UC):** where the warehouse offers it —
`system.access.column_lineage`, read from the same watermark window — each table edge is refined
with `[upstream_column, downstream_column]` pairs, stored on the edge row (`lineage_edges.columns`)
and **merged union-wise** on incremental refreshes (a log window only re-observes pairs whose
queries ran inside it; forgetting the rest would be a prune the never-prune regime forbids). A
separately-gated `column_lineage` degrades honestly: table edges still land, with a note. The asset
page shows the mappings per direct edge; an edge whose far endpoint is outside the viewer's grants
arrives **count-only** from the server (the #845 one-rule — a hidden asset's column names are
schema disclosure) and renders as a locked box. Snowflake's column grain lives in `ACCESS_HISTORY`
(Enterprise) and is honestly absent on Standard.

**Snowflake — a tier ladder, richest first** (from the 2026-07-17 live spike):

| Tier | Grain | Edition | Notes |
|---|---|---|---|
| `GET_LINEAGE` | object-level traversal | Enterprise+ | Tried first: its absence is a clean, catchable `0A000`, the best preflight signal. Per-seed traversal is a follow-up (no Enterprise account to test against). |
| `ACCESS_HISTORY` | column/statement | Enterprise+ | **Present-but-empty on Standard** — so emptiness is corroborated against `QUERY_HISTORY` (edition-gated vs genuinely idle), never read as "no lineage". ~2–3h lag. |
| `OBJECT_DEPENDENCIES` | view-level | all editions | The floor — live-verified on the demo account (RETAIL→STG→ANALYTICS chain). Views/matviews/dynamic-tables; no column detail. |

A degraded descent (e.g. down to view-level because the account isn't Enterprise) is **surfaced on
the asset's lineage graph**, not hidden — the graph says "view-level only" rather than presenting a
coarse graph as complete (#828). `OBJECT_DEPENDENCIES` is a current-state view, so its refresh is a
**snapshot diff** (re-read whole, prune stale edges).

**Unity Catalog — `system.access.table_lineage`**, an append-only **log** with an `event_time`. So
its refresh is **incremental**: read forward from a persisted watermark (`connections.lineage_watermark`),
advance it to the new max, and **never prune** — an edge absent from the latest window is a historical
fact, not a removed dependency. A 6h safety window before the watermark absorbs the system table's
ingestion lag so a late-arriving row is never lost to a strict `>`. Only rows with **both** a source
and a target table are edges (most rows are pure read-access with a null target).

**Verified against real captured payloads (2026-07-17 spike), not against a deployed app.** The
providers' parse + identity are pinned byte-for-byte against `asset_identity` using the actual
`OBJECT_DEPENDENCIES` / `table_lineage` payloads captured from the live demo account/workspace; the
edition-gated Snowflake tiers (Enterprise) have no live payload yet and their descent is exercised by
fakes reproducing the connector's observed `0A000` / silent-empty. Enabling it against a deployed
warehouse + the per-seed `GET_LINEAGE` traversal are follow-ups.

## Wiring a trigger

In the UI, open a suite's **Triggers** and bind it to a `(provider, pipeline/DAG, env)`.
When that pipeline next succeeds, the suite runs automatically and you'll see the run
correlated to the pipeline run on the **Results → Pipelines** view.
