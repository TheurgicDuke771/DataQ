# Performance baseline — all datasources

> Captured **2026-07-10** (v1.1 W3), while both the Snowflake trial and the Azure
> subscription were still live. This supersedes the Snowflake-only W1 baseline
> (#587, kept below as the historical appendix) and is the **reference datum for
> [#595](https://github.com/TheurgicDuke771/DataQ/issues/595) — scale-aware
> execution (G-b)**: it measures, per datasource, where DataQ's run path stops
> scaling and *how it fails* when it does.

## TL;DR

| Datasource | Execution model | Verified scale | Ceiling on a 2 Gi worker | Failure mode past ceiling |
|---|---|---|---|---|
| **Snowflake** | SQL pushdown | **200M rows** (50M / 100M / 200M all green) | none found — worker memory flat | n/a |
| **Flat file CSV** (ADLS) | full load into worker pandas | 2M rows (~121 MB CSV) | **2M → 5M** | prefork child SIGKILL |
| **Flat file Parquet** (ADLS) | full load into worker pandas | 5M rows (~131 MB parquet) | **5M → 10M** | 5M+: child SIGKILL; 10M killed the whole container |
| **Unity Catalog** | full load via SQL-warehouse `read_sql_table` | 1M rows | **1M → 2M** | child SIGKILL |
| **Apache Iceberg** (native, ADR 0030) | full snapshot via `pyiceberg` → Arrow | 2M rows | **2M → 5M** | **prod worker replica killed + recreated** |
| **AWS S3** | same code as ADLS (`flatfile.py` is shared) | not run live (no S3 credentials remain) | expect ≡ ADLS | ≡ ADLS |

Two sentences of conclusion:

1. **Pushdown is a different regime, not a faster version of the same one.** At
   200M rows Snowflake's wall time is 16.2s (vs 12.1s at 50M) and the worker
   never moves off its ~930 MiB baseline; every full-load runner dies between
   1M and 10M rows depending on format.
2. **Past the ceiling, today's failure is silent** — the OOM-killed run sits in
   `running` for up to 60 minutes until the stuck-run reaper (#309) fails it,
   with no memory-attributed reason ([#755](https://github.com/TheurgicDuke771/DataQ/issues/755)).
   #595's size-probe + hard-cap ("refuse with `error`, don't OOM") is the fix.

## Environment & method

| | |
|---|---|
| App code | `main` @ `e6b63fe1` (v1.1 W3) |
| Measurement rig | local docker-compose stack pinned to **prod parity**: worker at 1 CPU / 2 GiB / `celery --concurrency=4` (matched to the live ACA worker's startup banner), driven through the real REST API (dev-bypass) |
| Iceberg leg | run against **prod** (the local worker can't reach the SQL catalog's Postgres; the ACA worker can) — wall via REST, memory via ACA `WorkingSetBytes` |
| Worker memory sampling | `docker stats` at 1 Hz (local); 1-min max metric (prod) |
| Checks per rung | 5 expectations (not-null ×2, between ×2, unique ×1) + volume & freshness monitors where the type supports them (SQL/UC/Iceberg — flat files reject monitor kinds by design) |
| Data shape | 6-col order-lines (`line_id`, `order_id`, `sku_id`, `qty`, `unit_price`, `line_ts`) — same shape as #587 |

Data generation (all regenerable in seconds — nothing needs to be archived):

- **Snowflake**: `CREATE TABLE … AS SELECT SEQ8(), UNIFORM(…), … FROM TABLE(GENERATOR(ROWCOUNT => 200000000))`
  — 50M in 10.6s, 100M in 17s, 200M in 28.6s on XSMALL. `DATAQ_DB.PERF.ORDER_LINES_50M` is kept; the 100M/200M tables were dropped after the run.
- **UC**: `CREATE TABLE dataq_retail.perf.order_lines_1m AS SELECT … FROM range(1000000)` via the SQL Statements API (schema dropped after).
- **Flat files**: numpy → CSV + Parquet at 1/2/5/10M rows, uploaded to `landing/perf/` (deleted after).
- **Iceberg**: 1M-row Arrow batches appended to a dedicated `perf.order_lines` by the harness `iceberg-writer` ACA job with a command override (namespace dropped after; job re-suspended).

The whole campaign — including generating 350M+ Snowflake rows — burned
**~0.46 Snowflake credits** and roughly nothing anywhere else.

## Snowflake — pushdown ramp

| Rung | Wall (trigger → terminal) | Checks | Worker memory |
|---|---|---|---|
| 1.2M (#587, W1) | 12.2 s | 6 + volume, pass | < 50 MB delta |
| **50M** | **12.1 s** (repeat: 12.1 s) | 7/7 pass | baseline 923 → peak 926 MiB |
| **100M** | **16.2 s** | 7/7 pass | flat (≤ +2 MiB) |
| **200M** | **16.2 s** | 7/7 pass | flat (≤ +2 MiB) |

Column profiler, 4 columns (`COUNT/nulls/distinct/min/max/top-10`):

| Table | Cold | Warm repeat |
|---|---|---|
| 1.2M (#587) | 2.6 s | — |
| 50M | 15.7 s | 2.9 s |
| 200M | 24.6 s | 2.5 s |

(The warm numbers are the Snowflake result cache doing the work — the app adds
~2.5s of fixed overhead.)

Wall time is dominated by GX orchestration + connection setup exactly as #587
predicted: 165× more rows bought ~4s of extra wall. Cost scales with the
*warehouse*, not the worker — the 2 Gi replica never noticed 200M rows.

## Full-load runners — ramp to failure

All numbers from the prod-parity local rig except Iceberg (prod). "Fresh" =
freshly restarted worker (baseline ~750–870 MiB); "warm" = worker that had
already executed runs (see the creep finding below).

| Rung | Status | Wall | Worker peak |
|---|---|---|---|
| CSV 1M (60 MB) | pass | 4.0 s | 1186 MiB |
| CSV 2M (121 MB), warm | **child OOM** | — | killed at 1671 MiB |
| CSV 2M, fresh | pass | 6.1 s | 1211 MiB |
| CSV 5M (304 MB), fresh | **child OOM** | — | killed at 1838 MiB |
| Parquet 1M (26 MB) | pass | 4.0 s | 1295 MiB |
| Parquet 2M (53 MB) | pass | 6.0 s | 1666 MiB |
| Parquet 5M (131 MB), warm | **child OOM** | — | killed at 1915 MiB |
| Parquet 5M, fresh | pass | 8.1 s | 1508 MiB |
| Parquet 10M (263 MB), fresh | **container killed** | — | worker replica restarted mid-run |
| UC 1M | pass | 30.3 s | 1681 MiB |
| UC 2M | **child OOM** | — | killed seconds in |
| Iceberg 1M (prod) | pass | 6.8 s | 1218 MiB (ACA metric) |
| Iceberg 2M (prod) | pass | 12.5 s | 1408 MiB |
| Iceberg 5M (prod) | **container killed** | — | **prod worker replica killed + recreated at 07:37:58Z** |

Reading the table:

- **Format matters ~2–4×**: parquet's ceiling is a rung above CSV's for the same
  row count (Arrow-backed read, no text parse blow-up).
- **UC is the heaviest per row** — `pd.read_sql_table` over the SQL warehouse
  spent ~925 MiB on 1M rows; it also pays a ~20s warehouse round-trip, so it's
  the slowest *and* the hungriest path.
- **Iceberg materialises the whole current snapshot** (`scan().to_arrow()`);
  its monitors (volume = `scan().count()`, freshness = single-column scan) stayed
  cheap and passed at every size tested.

## Findings (filed)

1. **[#755](https://github.com/TheurgicDuke771/DataQ/issues/755) — OOM is a silent
   failure (P1, W6 with #595).** Child SIGKILL → `WorkerLostError` is never
   translated to the run: the run stays `running` until the stuck-run reaper's
   60-minute threshold, with no memory-attributed reason. Container-level OOM is
   worse: locally it produced a sustained crash loop (beat re-sending periodic
   tasks + redelivery into the same OOM, 25 restarts until the queue was purged);
   on prod it killed the shared worker replica (which also runs beat — polling,
   schedules and alerting all blink).
2. **Worker memory baseline creeps run-over-run** (956 → 1188 → 1666 MiB across
   three flat-file runs): prefork children never return pandas allocations, so
   the *effective* ceiling degrades with worker uptime — the same file that
   passes on a fresh worker OOMs on a warm one. `worker_max_memory_per_child`
   would recycle children (folded into #755).
3. **[#753](https://github.com/TheurgicDuke771/DataQ/issues/753) — deleting a
   connection with dependent suites 500s** (unhandled FK `IntegrityError`;
   connection-side sibling of #540).
4. **[#754](https://github.com/TheurgicDuke771/DataQ/issues/754) — Iceberg
   connection leaks the SQL-catalog credential (P1, security).** Option A's single
   secret slot forces the catalog DB password into non-secret `config.catalog_uri`,
   which `GET /connections` returns in plaintext to any user with connection read
   access. Includes the ops action to rotate the harness PG password.

## What this means for #595

- The guardrail should be a **size probe + configurable hard cap** *before*
  materialising (refuse with a clean `error`), plus immediate `WorkerLostError`
  → run-failure mapping as defence in depth. A static row cap is the wrong knob:
  the measured ceiling varies ~5× by format and degrades with worker uptime.
- Sampling/batching priorities by measured pain: **UC first** (lowest ceiling,
  pushable — monitors already push down; SQL-able expectation subsets should
  too), then CSV (worst expansion factor; per-file batching already exists in the
  flat-file runner seam), then Iceberg (snapshot scan → `row_filter`/limit
  pushdown in pyiceberg).
- Per-run overhead floor is unchanged from #587 (~10s Snowflake, ~4s flat-file,
  ~6s Iceberg-on-prod), so sampled runs will be *fast*, not just safe.

---

## Appendix — Snowflake 1.2M baseline (#587, 2026-07-04, historical)

The original W1 pushdown datum, captured days before the Snowflake subscription
was to lapse (the lapse was later reversed). Environment: `DATAQ_DB.PERF`
`ORDER_LINES` 1,199,854 rows / `ORDERS_HEADER` 400,000 rows (harness generator
`PERF` tier, `--seed 587`), local stack at `v1.0.0` + #602/#603, XSMALL
warehouse, `DATAQ_READER` role.

| Measurement | Value |
|---|---|
| Test-connection (`SELECT 1`) | 3.7 s |
| Suite run, 1.2M-row table — 6 expectations + volume | 12.2 s wall, all pass |
| Suite run, 400K-row table — freshness + 2 expectations | 8.1 s wall |
| Column profiler, 4 columns on 1.2M rows | 2.6 s |
| Worker memory delta during the 1.2M run | < 50 MB (idle 1.99 GiB → peak 2.04 GiB, 14-child unpinned worker) |
| Snowflake compute, whole session | 54 SELECTs, ~2.3 s total; slowest query 1.0 s |
| Credits burned | ~0.08 |

Slowest per-query attribution (from `QUERY_HISTORY`): 647 ms unexpected-count
aggregate (uniqueness), 484 ms profiler batched aggregate, 388 ms
unexpected-values sample fetch, 213 ms profiler top-10 on the ~1.2M-distinct
column; every other expectation aggregate ≤ 15 ms, monitor scalars sub-10 ms.
Per-check `duration_ms` stays NULL in v1 by design (`run_service.py` — per-check
timing is a deferred datum). Gaps found then and since tracked: #571
(`checks_total` cosmetic 0), #605 (failure reasons — since shipped).
