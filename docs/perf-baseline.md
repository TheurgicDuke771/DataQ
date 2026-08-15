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
| **Apache Iceberg** (native, ADR 0030) | full snapshot via `pyiceberg` → Arrow | 2M rows | **2M → 5M** | worker replica OOM-killed + recreated |
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
| Measurement rig | docker-compose stack pinned to **production parity**: worker at 1 CPU / 2 GiB / `celery --concurrency=4` (matched to the deployed worker), driven through the real REST API |
| Iceberg leg | run against the deployed stack (the native catalog wasn't reachable from the local rig) — wall via REST, worker memory via the platform metric |
| Worker memory sampling | `docker stats` at 1 Hz (local); 1-min max metric (prod) |
| Checks per rung | 5 expectations (not-null ×2, between ×2, unique ×1) + volume & freshness monitors where the type supports them (SQL/UC/Iceberg — flat files reject monitor kinds by design) |
| Data shape | 6-col order-lines (`line_id`, `order_id`, `sku_id`, `qty`, `unit_price`, `line_ts`) — same shape as #587 |

Data generation (all regenerable in seconds — nothing needs to be archived):

- **Snowflake**: `CREATE TABLE … AS SELECT SEQ8(), UNIFORM(…), … FROM TABLE(GENERATOR(ROWCOUNT => 200000000))`
  — 50M in 10.6s, 100M in 17s, 200M in 28.6s on XSMALL. `DATAQ_DB.PERF.ORDER_LINES_50M` is kept; the 100M/200M tables were dropped after the run.
- **UC**: `CREATE TABLE dataq_retail.perf.order_lines_1m AS SELECT … FROM range(1000000)` via the SQL Statements API (schema dropped after).
- **Flat files**: numpy → CSV + Parquet at 1/2/5/10M rows, uploaded to `landing/perf/` (deleted after).
- **Iceberg**: 1M-row Arrow batches appended to a dedicated `perf.order_lines` namespace via `pyiceberg` (namespace dropped after the run).

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
predicted: ~167× more rows (1.2M → 200M) bought ~4s of extra wall. Cost scales
with the *warehouse*, not the worker — the 2 Gi replica never noticed 200M rows.

## Full-load runners — ramp to failure

All numbers from the prod-parity rig except Iceberg (deployed stack). "Fresh" =
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
| Iceberg 1M (deployed) | pass | 6.8 s | 1218 MiB (platform metric) |
| Iceberg 2M (deployed) | pass | 12.5 s | 1408 MiB |
| Iceberg 5M (deployed) | **container killed** | — | worker replica OOM-killed + recreated |

Reading the table:

- **Format matters ~2–4×**: parquet's ceiling is a rung above CSV's for the same
  row count (Arrow-backed read, no text parse blow-up).
- **UC is the heaviest per row** — `pd.read_sql_table` over the SQL warehouse
  spent ~925 MiB on 1M rows; it also pays a ~20s warehouse round-trip, so it's
  the slowest *and* the hungriest path.
- **Iceberg materialises the whole current snapshot** (`scan().to_arrow()`);
  its monitors (volume = `scan().count()`, freshness = single-column scan) stayed
  cheap and passed at every size tested.

## Known limitations at the ceiling

Two performance characteristics of the full-load path, both tracked for the
scale-aware execution work ([#595](https://github.com/TheurgicDuke771/DataQ/issues/595)):

1. **An out-of-memory run is not reported promptly.** When a full-load run
   exceeds worker memory the worker process is killed; the run currently stays
   `running` until the stuck-run reaper fails it (default threshold 60 min),
   with no memory-attributed reason. Tracked in
   [#755](https://github.com/TheurgicDuke771/DataQ/issues/755) — the fix maps the
   worker loss straight to a run `error`, ahead of the #595 size-cap guardrail.
2. **The effective ceiling degrades with worker uptime.** The prefork worker's
   memory baseline creeps run-over-run (measured start-of-run: 956 → 1188 → 1666
   MiB across three flat-file runs) because children don't release pandas
   allocations — so a file that passes on a freshly-started worker can OOM on a
   long-lived one. Recycling children per N runs removes the creep (folded into
   #755).

Two non-performance defects were also found and filed while measuring
([#753](https://github.com/TheurgicDuke771/DataQ/issues/753),
[#754](https://github.com/TheurgicDuke771/DataQ/issues/754)); see the tracker.

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

## v1.1 W6 — scale-aware execution ([#595](https://github.com/TheurgicDuke771/DataQ/issues/595))

> Captured **2026-08-13** while building the sampling + guardrail work. This
> section answers the issue's last acceptance criterion ("volume test vs the #587
> Snowflake baseline documented") and records the evidence the shipped defaults
> were chosen from.

### Method (and what it does *not* prove)

A 5M-row, 6-column order-lines dataset (the same shape as every rung above) was
written as both CSV (**249 MB**) and Parquet (**114 MB**), and the **real**
`FlatFileCheckRunner` was run against it with five expectations (not-null ×2,
between ×2, unique ×1) — the same suite the W3 campaign used.

The three object-store seams (`file_stat` / `download_bytes` / `read_range`) were
pointed at a local file, so the runner, the guardrail and the sampling readers all
execute for real and only the network is stood in for. Peak memory is the child
process's own `ru_maxrss`.

**What this does not measure:** egress, warehouse behaviour, or the Unity Catalog
leg — `TABLESAMPLE (x PERCENT)` is a Databricks-side fact and, per the #953 rule,
only a live run counts as evidence for it. The UC numbers below are therefore
absent rather than estimated.

The **floor** — interpreter + GX + pyarrow, measured on the refused case, which
loads nothing — is **329 MiB**. Deltas below are over that floor, because it is
the part sampling cannot remove.

### Results — 5M rows, five expectations

| Object | Mode | Outcome | Wall | Peak RSS | Δ over floor |
|---|---|---|---|---|---|
| CSV 249 MB | full read, cap **off** | 5/5 pass | 4.26 s | **2,210 MiB** | +1,881 |
| CSV 249 MB | full read, cap **on** | **refused** — `ScanTooLargeError` | **0.00 s** | 329 MiB | +0 |
| CSV 249 MB | `head`, 100k rows | 5/5 pass, `sampled: true` | 0.15 s | **411 MiB** | +82 |
| CSV 249 MB | `random`, 100k rows | 5/5 pass, `sampled: true` | 1.98 s | **550 MiB** | +221 |
| Parquet 114 MB | full read, cap off | 5/5 pass | 2.11 s | 1,290 MiB | +961 |
| Parquet 114 MB | full read, cap on | 5/5 pass (under the cap) | 2.07 s | 1,278 MiB | +949 |
| Parquet 114 MB | `head`, 100k rows | 5/5 pass, `sampled: true` | 0.10 s | **405 MiB** | +76 |
| Parquet 114 MB | `random`, 100k rows | 5/5 pass, `sampled: true` | 0.18 s | **468 MiB** | +139 |

Reading the table:

1. **Without the cap, a 249 MB CSV needs 2.2 GiB.** That is over the deployed 2 GiB
   worker on its own, before the ~0.9 GiB baseline it already carries — i.e. the
   #755 SIGKILL, reproduced.
2. **With the cap, it is refused in 0.00 s** with a message naming the file, both
   numbers and the knob — instead of a dead child and a run stuck `running` for
   60 minutes with no memory-attributed reason.
3. **With sampling, the same five checks run under half a gigabyte**: 23× less
   memory delta and 28× less wall time for CSV `head`. `random` costs more than
   `head` because it has to learn the population size first (a streamed CSV scan;
   for Parquet that is a footer read, which is why the Parquet `random` case is
   nearly as cheap as `head`).
4. **Sampled-ness is recorded, and the record is honest about what it knows.**
   `head` reports `total_rows: null` — it stopped reading rather than pay for a
   count — while `random` reports `total_rows: 5000000`, because it needed the
   population size anyway.

### Why the defaults are what they are

`RUN_MAX_SCAN_BYTES = 128 MiB` and `RUN_MAX_SCAN_ROWS = 1,500,000`.

The **first draft was 256 MiB and was wrong**, which is the useful part of this
measurement: the 249 MB CSV passes a 256 MiB cap and *still* peaks at 2,210 MiB.
A cap that admits the case it exists to prevent is a guardrail that does not
guard. At the measured expansion (~8× object bytes for CSV, ~9× for Parquet),
128 MiB puts a full read at roughly 1.2–1.3 GiB, which fits the deployed worker
with its baseline. It also lands exactly on the W3 pass/fail boundary above: it
admits every rung measured to pass (121 MB CSV, 131 MB Parquet) and refuses every
rung measured to die (263 MB Parquet, 304 MB CSV).

The Parquet row of the table is the honest edge case: at 114 MB it is *legally*
under the cap and still peaks at 1,278 MiB. That is the boundary, not a miss —
the next rung up (10M rows, ~228 MB) is refused. A deployment on a smaller worker
should lower the cap; a deployment on a larger one can raise it, and the message
says so.

`RUN_MAX_SCAN_ROWS` keeps the W3 UC datum: 1M passed at 1,681 MiB, 2M OOM-killed
the child, so 1.5M sits between them. It is a **row** cap rather than a byte one
because a warehouse `COUNT(*)` is exact and free, where a CSV's row count would
cost the very scan being avoided.

### Comparison with the #587 Snowflake baseline

The point of the comparison is that these are still two different regimes, and
sampling narrows the gap without closing it:

| | Snowflake (pushdown) | Flat file, full read | Flat file, sampled |
|---|---|---|---|
| Rows the worker holds | none | all of them | the sample |
| 5M rows, peak worker RSS | flat (≤ +2 MiB at 200M) | 1,290–2,210 MiB | 405–550 MiB |
| Scales with | the warehouse | the dataset | the sample |
| Answer is | complete | complete | **a sample — and says so** |

Pushdown remains strictly better where it is available, which is why
`SAMPLING_CAPABLE_TYPES` excludes Snowflake outright rather than offering a knob
that would stamp "sampled" on a result that was not.

### Still open after this work

- **Unity Catalog needs a live run.** The pushdown SQL is DataQ's own
  construction and is unit-pinned, but `TABLESAMPLE (x PERCENT) REPEATABLE (seed)`
  behaviour is a Databricks fact — #953's rule says only a live run is evidence.
- **Iceberg has neither a cap nor sampling**
  ([#1328](https://github.com/TheurgicDuke771/DataQ/issues/1328)). It is the third
  runner that materialises a whole dataset, so #755 stays open there. The probe is
  cheap (`scan().count()` is snapshot metadata); what it needs first is its **own
  measurement** — Iceberg passed at 2M rows where UC died, so inheriting
  `RUN_MAX_SCAN_ROWS`'s 1.5M would refuse a rung measured to work.
- **Comparison sources cannot sample**
  ([#1331](https://github.com/TheurgicDuke771/DataQ/issues/1331)) — refused at save
  time rather than ignored. It needs *coherent* key-set sampling: two independent
  draws from two 5M-row sides would share almost no keys and report everything as
  a mismatch, which is worse than refusing.
- **Column projection for flat-file monitors.** A column-freshness monitor still
  reads every column to compute one `MAX`. Parquet could project a single column
  off its footer, which would remove most of the remaining monitor-path memory.
- **Efficiency and reuse batches**
  ([#1329](https://github.com/TheurgicDuke771/DataQ/issues/1329),
  [#1330](https://github.com/TheurgicDuke771/DataQ/issues/1330)) — the sampled CSV
  `random` path reads the object twice, and the count and take construct their CSV
  streams separately (consistent today by coincidence, not construction).
- **Incremental / delta-only validation** stays out of scope by design (#595):
  sampling bounds *how much* is read, not *which part is new*. Note for whoever
  builds it — a watermark belongs on the run target beside `sampling`, and its
  result record should be the same shape as `sampling`, so the run-detail surface
  learns one vocabulary for "this verdict covers less than everything".

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

Reproducing the historical datum (generator `python -m mockdata backfill --tier
PERF --seed 587 --no-issues`, harness repo per ADR 0021): load via pandas
`write_pandas` with **`use_logical_type=True`** — without it, `datetime64`
columns land as epoch `NUMBER` and freshness monitors error with "not a
date/timestamp". Credits/timing were read from
`INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY` / `QUERY_HISTORY`.
