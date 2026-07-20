# Feature matrix

One-page reference: what runs where. For the readable tour of everything DataQ offers see
[Features](features.md); for the concepts behind the columns see [Concepts](concepts.md) and
[Datasources & checks](datasources-checks.md).

## Check kinds × datasources

| Check kind | Snowflake | Unity Catalog | ADLS Gen2 (files) | S3 (files) | Iceberg |
|---|:-:|:-:|:-:|:-:|:-:|
| GX expectations (column / table shape) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Custom SQL (rows returned = failures) | ✅ | ✅ | — | — | — |
| Freshness monitor (hours since latest timestamp) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Freshness from **file arrival time** (no column — catches "no new file") | — | — | ✅ | ✅ | — |
| Volume monitor (row count in range) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Comparison / reconciliation (diff vs a baseline connection) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Column profiler (nulls, distinct, min/max, top values) | ✅ | ✅ | ✅ | ✅ | ✅ |
| DQ dimension on checks + asset scorecard (coverage + score) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Dry-run preview | ✅ | ✅ | ✅ | ✅ | ✅ |

Custom SQL runs a SQL query, so it's **SQL-datasource only** (Snowflake, Unity Catalog;
flat-file support is a tracked enhancement, [#520](https://github.com/TheurgicDuke771/DataQ/issues/520);
Iceberg is not SQL-queryable — reads go through `pyiceberg` scans, not a query engine).
**Comparison checks** (ADR [0015](adr/0015-two-connection-comparison-check-model.md), #791–#795)
diff the suite's dataset (the **target under test**) against a baseline on any other
datasource connection — cross-type and cross-env both supported — joined on key columns,
producing matched / mismatched / additional-per-side buckets with a mismatch-% metric,
capped fail-fast reads (`COMPARISON_MAX_ROWS`), redacted samples, and an on-demand
CSV/XLSX report download (derived, never stored). Either SQL side may use a read-only
query projection.

The freshness/volume monitors run on **monitor-capable datasources** — the SQL
datasources plus Apache Iceberg — computed natively (Iceberg's are `pyiceberg` scans, not
SQL; ADR 0012/0030). Flat-file suites target a file or a batch pattern (e.g.
`orders_*.csv`) in CSV or Parquet; Iceberg suites target a `namespace.table`. Dry-run
preview works on every datasource with a runner — Snowflake, Unity Catalog, flat files,
and Iceberg ([#532](https://github.com/TheurgicDuke771/DataQ/issues/532)).

## Assets & lineage × datasources

Every datasource gets a first-class **asset** (identity = the OpenLineage dataset naming
spec, ADR 0034); lineage edges are **observed, never inferred**, and can arrive through
five mechanisms:

1. **Run-stamping** — every suite run (and suite save) resolves its target to an asset row
   and stamps `last_seen`. Works on all datasources; unreferenced stale rows are retired by
   the daily orphan sweep.
2. **dbt `manifest.json`** — table-level model lineage cached into `lineage_edges` on every
   successful dbt build ([details](orchestration.md#lineage-from-manifestjson-adr-0034)).
   dbt models warehouse tables, so raw flat files don't appear here.
3. **OpenLineage emission** (outbound) — DataQ broadcasts RunEvents + DQ facets per run to
   any OL-compatible receiver (`OPENLINEAGE_URL`, dark by default).
4. **Catalog pull** — the `LineageProvider` seam pulls a governance catalog's graph back in
   as `source='marquez'` edges (daily beat, dark by default;
   [details](orchestration.md#lineage-from-a-catalog-the-lineageprovider-seam-adr-0034-762)).
5. **Warehouse-native pull** (#858) — the `WarehouseLineageProvider` seam reads the
   warehouse's OWN lineage views straight into `lineage_edges` with `source='snowflake'` /
   `'unity_catalog'`: Snowflake `OBJECT_DEPENDENCIES` (all editions) → `ACCESS_HISTORY` /
   `GET_LINEAGE` (Enterprise); Unity Catalog `system.access.table_lineage`. First-hand, no
   dbt hop. Daily beat, **dark by default** (`WAREHOUSE_LINEAGE_ENABLED` — the views need a
   grant); the tier that answered and any degraded/failing state surface on the asset's
   lineage graph so a view-level-only or stale graph never reads as a confident complete
   one ([details](orchestration.md#lineage-from-the-warehouse-warehouselineageprovider-858)).
   **Column grain (#901):** where the warehouse offers it (UC
   `system.access.column_lineage` — live-verified), the pull refines each table edge with
   `upstream column → downstream column` pairs, shown on the asset page to every
   workspace member (ADR 0037 — column names are schema metadata, i.e. identity).
   Snowflake's column grain lives in `ACCESS_HISTORY` (Enterprise) and reports honestly
   unavailable on Standard.

| Datasource | Asset entity | ① Run-stamping | ② dbt manifest | ③ OL emission | ④ Catalog pull | ⑤ Warehouse-native |
|---|---|:-:|:-:|:-:|:-:|:-:|
| Snowflake | `snowflake://{org}-{account}` / `DB.SCHEMA.TABLE` | ✅ | ✅ (live-verified) | ✅ | ✅ | ✅ (OBJECT_DEPENDENCIES live; ACCESS_HISTORY/GET_LINEAGE Enterprise) |
| Unity Catalog | `unitycatalog://{host}` / `catalog.schema.table` | ✅ | ✅ (adapter-aware) | ✅ | ✅ | ✅ (system.access.table_lineage, incremental; **+ column grain, live-verified**) |
| ADLS Gen2 (files) | `abfss://{container}@{account}.dfs.core.windows.net` / pattern **base prefix** | ✅ | — | ✅ | ✅ | — |
| S3 (files) | `s3://{bucket}` / base prefix | ✅ | — | ✅ | ✅ | — |
| Iceberg | `{catalog_uri}` / `namespace.table` | ✅ | —¹ | ✅ | ✅ | —³ |
| BI reports / dashboards | not yet materialized² | — | — | — | reserved² | — |

¹ dbt-managed Iceberg tables surface through the warehouse adapter (Snowflake/UC rows);
native `pyiceberg` connections have no dbt slice of their own.
³ Warehouse-native lineage reads a query engine's lineage view; a native `pyiceberg`
connection has no engine to ask (an engine-registered Iceberg table is covered under its
Snowflake/UC connection).
² The lineage graph's node-kind contract reserves `bi_report`/`dashboard` — a BI node
(e.g. a Power BI report downstream of a mart) becomes representable the moment a capable
catalog (Purview/DataHub) lands behind the seam plus an `assets.kind` column; no schema or
query rewrite needed.

Orchestration providers contribute **no lineage of their own** — ADF and Airflow are
observed for pipeline runs only; dbt is the one orchestration provider that doubles as a
lineage source (mechanism ②). Flat-file and Iceberg edges therefore depend on an external
catalog knowing about them (mechanism ④).

## Ways a suite runs

| Mode | Where | Notes |
|---|---|---|
| Run now | Suite detail → Run panel | Live per-check progress + cancel |
| Cron schedule | Suite detail → Schedules | 5-field cron, IANA timezone, DST-aware, [no backfill](scheduling.md) |
| Pipeline trigger | Suite detail → Triggers | Runs on a pipeline/DAG/dbt-job **success** — ADF + Airflow + dbt, see [Orchestration](orchestration.md) |
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
| dbt | Post-build callback → HMAC webhook (+10-min `run_results.json` artifact poll) | ✅ trigger bindings |

## Interfaces

| Surface | What |
|---|---|
| Web UI | Dashboard · Assets · Connections · Suites · Results · Profile · Admin · Settings (Assets lead as the primary lens — ADR 0034 nav inversion; the Dashboard opens with an asset-health strip, and suites/runs link back to their asset) |
| REST API | Versioned `/api/v1` (Swagger in non-prod) |
| MCP | 8 curated tools at `/mcp` for AI assistants (ADR 0008) |
