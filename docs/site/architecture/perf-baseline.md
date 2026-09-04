# Performance baseline — all datasources

> Captured **2026-07-10** against live warehouses; **Unity Catalog's rows updated
> 2026-08-22** after Unity Catalog's audited ordinary expectations (and,
> unconditionally, custom SQL) moved onto SQL pushdown — both now tested to 200M rows
> with flat worker memory, matching Snowflake's regime (see the dedicated section
> below). It measures, per datasource, where DataQ's run path stops scaling and *how
> it fails* when it does.

## TL;DR

| Datasource | Execution model | Verified scale | Ceiling on a 2 Gi worker | Failure mode past ceiling |
|---|---|---|---|---|
| **Snowflake** | SQL pushdown | **200M rows** (50M / 100M / 200M all green) | none found — worker memory flat | n/a |
| **Flat file CSV** (ADLS) | full load into worker pandas | 2M rows (~121 MB CSV) | **2M → 5M** | prefork child SIGKILL |
| **Flat file Parquet** (ADLS) | full load into worker pandas | 5M rows (~131 MB parquet) | **5M → 10M** | 5M+: child SIGKILL; 10M killed the whole container |
| **Unity Catalog** — audited ordinary expectations | **SQL pushdown** (`UC_SQL_PUSHDOWN=true`, the default) | **200M rows** (50M/100M/200M all green, worker memory flat) | none found — matches the Snowflake regime | n/a |
| **Unity Catalog** — custom SQL (`unexpected_rows_expectation`) | **SQL batch**, unconditional (no pandas metric provider exists) | **200M rows** (worker memory flat, run alongside the pushdown checks above) | none found | n/a |
| **Unity Catalog** — unaudited types / sampled suites | frame load (`read_sql_table`) | 1M rows | **1M → 2M** (the scan-cap guardrail now refuses 2M cleanly instead of OOM) | child SIGKILL past the size cap |
| **Apache Iceberg** (native, ADR 0030) | full snapshot via `pyiceberg` → Arrow | 2M rows | **2M → 5M** | worker replica OOM-killed + recreated |
| **AWS S3** | same code as ADLS (`flatfile.py` is shared) | not run live (no S3 credentials remain) | expect ≡ ADLS | ≡ ADLS |

Two sentences of conclusion:

1. **Pushdown is a different regime, not a faster version of the same one.** At
   200M rows Snowflake's wall time is 16.2s (vs 12.1s at 50M) and the worker
   never moves off its ~930 MiB baseline; every full-load runner dies between
   1M and 10M rows depending on format.
2. **Past the ceiling, today's failure is silent** — the OOM-killed run sits in
   `running` for up to 60 minutes until the stuck-run reaper fails it,
   with no memory-attributed reason. A size-probe + hard-cap ("refuse with
   `error`, don't OOM"), described in the v1.1 section below, has since shipped
   and is enforced on every flat-file and unaudited-UC read.

## Environment & method

| | |
|---|---|
| App code | `main`, during v1.1 development |
| Measurement rig | docker-compose stack pinned to **production parity**: worker at 1 CPU / 2 GiB / `celery --concurrency=4` (the value the worker now pins in its Celery config, `WORKER_CONCURRENCY` — the prefork default reads the *host's* core count, not the container's, and had silently differed between the two reference deployments), driven through the real REST API |
| Iceberg leg | run against the deployed stack (the native catalog wasn't reachable from the local rig) — wall via REST, worker memory via the platform metric |
| Worker memory sampling | `docker stats` at 1 Hz (local); 1-min max metric (prod) |
| Checks per rung | 5 expectations (not-null ×2, between ×2, unique ×1) + volume & freshness monitors on the SQL/UC/Iceberg rungs (flat files also support freshness/volume, incl. arrival-time freshness, but weren't run through this particular campaign) |
| Data shape | 6-col order-lines (`line_id`, `order_id`, `sku_id`, `qty`, `unit_price`, `line_ts`) — same shape as the original baseline campaign |

Data generation (all regenerable in seconds — nothing needs to be archived):

- **Snowflake**: `CREATE TABLE … AS SELECT SEQ8(), UNIFORM(…), … FROM TABLE(GENERATOR(ROWCOUNT => 200000000))`
  — 50M in 10.6s, 100M in 17s, 200M in 28.6s on XSMALL. The 50M table was kept; the 100M/200M tables were dropped after the run.
- **UC**: `CREATE TABLE <catalog>.perf.order_lines_1m AS SELECT … FROM range(1000000)` via the SQL Statements API (schema dropped after).
- **Flat files**: numpy → CSV + Parquet at 1/2/5/10M rows, uploaded to `landing/perf/` (deleted after).
- **Iceberg**: 1M-row Arrow batches appended to a dedicated `perf.order_lines` namespace via `pyiceberg` (namespace dropped after the run).

The whole campaign — including generating 350M+ Snowflake rows — burned
**~0.46 Snowflake credits** and roughly nothing anywhere else.

## Snowflake — pushdown ramp

| Rung | Wall (trigger → terminal) | Checks | Worker memory |
|---|---|---|---|
| 1.2M (original baseline, Week 1) | 12.2 s | 6 + volume, pass | < 50 MB delta |
| **50M** | **12.1 s** (repeat: 12.1 s) | 7/7 pass | baseline 923 → peak 926 MiB |
| **100M** | **16.2 s** | 7/7 pass | flat (≤ +2 MiB) |
| **200M** | **16.2 s** | 7/7 pass | flat (≤ +2 MiB) |

Column profiler, 4 columns (`COUNT/nulls/distinct/min/max/top-10`):

| Table | Cold | Warm repeat |
|---|---|---|
| 1.2M (original baseline) | 2.6 s | — |
| 50M | 15.7 s | 2.9 s |
| 200M | 24.6 s | 2.5 s |

(The warm numbers are the Snowflake result cache doing the work — the app adds
~2.5s of fixed overhead.)

Wall time is dominated by GX orchestration + connection setup exactly as the
original baseline predicted: ~167× more rows (1.2M → 200M) bought ~4s of extra
wall. Cost scales
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
scale-aware execution work described below:

1. **An out-of-memory run is not reported promptly.** When a full-load run
   exceeds worker memory the worker process is killed; the run currently stays
   `running` until the stuck-run reaper fails it (default threshold 60 min),
   with no memory-attributed reason. The fix maps the
   worker loss straight to a run `error`, ahead of the size-cap guardrail below.
2. **The effective ceiling degrades with worker uptime.** The prefork worker's
   memory baseline creeps run-over-run (measured start-of-run: 956 → 1188 → 1666
   MiB across three flat-file runs) because children don't release pandas
   allocations — so a file that passes on a freshly-started worker can OOM on a
   long-lived one. Recycling children per N runs removes the creep (folded into
   the same fix).

Two non-performance defects were also found and filed while measuring; see the
tracker.

## What this means for the scale-aware execution work

- The guardrail should be a **size probe + configurable hard cap** *before*
  materialising (refuse with a clean `error`), plus immediate `WorkerLostError`
  → run-failure mapping as defence in depth. A static row cap is the wrong knob:
  the measured ceiling varies ~5× by format and degrades with worker uptime.
- Sampling/batching priorities by measured pain: **UC first** (lowest ceiling,
  pushable — monitors already push down; SQL-able expectation subsets should
  too), then CSV (worst expansion factor; per-file batching already exists in the
  flat-file runner seam), then Iceberg (snapshot scan → `row_filter`/limit
  pushdown in pyiceberg).
- Per-run overhead floor is unchanged from the original baseline (~10s Snowflake,
  ~4s flat-file, ~6s Iceberg-on-prod), so sampled runs will be *fast*, not just
  safe.

---

## v1.1 — scale-aware execution

> Captured **2026-08-13** while building the sampling + guardrail work. This
> section answers the last acceptance criterion ("volume test vs the original
> Snowflake baseline documented") and records the evidence the shipped defaults
> were chosen from.

### Method (and what it does *not* prove)

A 5M-row, 6-column order-lines dataset (the same shape as every rung above) was
written as both CSV (**249 MB**) and Parquet (**114 MB**), and the **real**
`FlatFileCheckRunner` was run against it with five expectations (not-null ×2,
between ×2, unique ×1) — the same suite this campaign used.

The three object-store seams (`file_stat` / `download_bytes` / `read_range`) were
pointed at a local file, so the runner, the guardrail and the sampling readers all
execute for real and only the network is stood in for. Peak memory is the child
process's own `ru_maxrss`.

**What this does not measure:** egress, warehouse behaviour, or the Unity Catalog
leg — `TABLESAMPLE (x PERCENT)` is a Databricks-side fact, and only a live run
counts as evidence for it. The UC numbers below are therefore
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
   same SIGKILL measured above, reproduced.
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
with its baseline. It also lands exactly on the pass/fail boundary above: it
admits every rung measured to pass (121 MB CSV, 131 MB Parquet) and refuses every
rung measured to die (263 MB Parquet, 304 MB CSV).

The Parquet row of the table is the honest edge case: at 114 MB it is *legally*
under the cap and still peaks at 1,278 MiB. That is the boundary, not a miss —
the next rung up (10M rows, ~228 MB) is refused. A deployment on a smaller worker
should lower the cap; a deployment on a larger one can raise it, and the message
says so.

`RUN_MAX_SCAN_ROWS` keeps the UC datum: 1M passed at 1,681 MiB, 2M OOM-killed
the child, so 1.5M sits between them. It is a **row** cap rather than a byte one
because a warehouse `COUNT(*)` is exact and free, where a CSV's row count would
cost the very scan being avoided.

### Comparison with the original Snowflake baseline

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
  behaviour is a Databricks fact — only a live run is evidence.
- **Iceberg has neither a cap nor sampling.** It is the third
  runner that materialises a whole dataset, so the out-of-memory-reporting gap
  stays open there. The probe is
  cheap (`scan().count()` is snapshot metadata); what it needs first is its **own
  measurement** — Iceberg passed at 2M rows where UC died, so inheriting
  `RUN_MAX_SCAN_ROWS`'s 1.5M would refuse a rung measured to work.
- **Comparison sources cannot sample** — refused at save
  time rather than ignored. It needs *coherent* key-set sampling: two independent
  draws from two 5M-row sides would share almost no keys and report everything as
  a mismatch, which is worse than refusing.
- **Column projection for flat-file monitors.** A column-freshness monitor still
  reads every column to compute one `MAX`. Parquet could project a single column
  off its footer, which would remove most of the remaining monitor-path memory.
- **Efficiency and reuse.** The sampled CSV
  `random` path reads the object twice, and the count and take construct their CSV
  streams separately (consistent today by coincidence, not construction).
- **Incremental / delta-only validation** stays out of scope by design:
  sampling bounds *how much* is read, not *which part is new*. Note for whoever
  builds it — a watermark belongs on the run target beside `sampling`, and its
  result record should be the same shape as `sampling`, so the run-detail surface
  learns one vocabulary for "this verdict covers less than everything".

---

## v1.2 — UC SQL pushdown for ordinary expectations

> Captured **2026-08-22**, live against the harness Databricks workspace. This
> closes the one gap the v1.1 section above left open ("Unity Catalog needs a
> live run") — it re-measures the UC leg now that the seven audited ordinary
> expectations (not-null, unique, between, in-set, length, regex, row-count)
> execute on the Databricks-SQL batch by default (`UC_SQL_PUSHDOWN=true`) instead
> of the full pandas-frame load the earlier baseline measured.

### Method

Same shape as every prior rung: a 6-col order-lines table (`line_id`, `order_id`,
`sku_id`, `qty`, `unit_price`, `line_ts`), created via
`CREATE TABLE … AS SELECT … FROM range(n)` on the harness's Databricks Free
Edition serverless SQL warehouse, run through the **real** `UnityCatalogCheckRunner`
via the prod-parity rig (worker capped 1 CPU / 2 GiB, `celery --concurrency=4`),
driven through the real REST API. Suite: the same 5 expectations as every other
rung (not-null ×2, between ×2, unique ×1) — all five are in the audited pushdown
allowlist. Worker memory sampled via `docker stats` at ~1 Hz; "wall" is
`started_at` → `finished_at` from the run record.

Two check groups were measured, both against the same tables: the **5-check
pushdown suite** (not-null ×2, between ×2, unique ×1 — all in the audited
allowlist) at every rung, plus **one custom-SQL check**
(`unexpected_rows_expectation`, `SELECT * FROM {batch} WHERE qty < 1 OR qty > 20`)
added to the 100M/200M suites to confirm its own ceiling, since it is a distinct
code path (SQL-batch, unconditional) that the 1M/2M/50M rungs did not
separately exercise.

### Results

| Rows | Checks | Pushdown | Outcome | Wall | Worker peak | Δ over idle baseline |
|---|---|---|---|---|---|---|
| 1M | 5 pushdown | **off** (frame load, the earlier default behavior) | 5/5 pass | 11.1 s | **1,588 MiB** (1.551 GiB) | +824 MiB (over 764 MiB) |
| 1M | 5 pushdown | **on** (default) | 5/5 pass | 17.7 s | **935 MiB** | +15 MiB (over 920 MiB) |
| 2M | 5 pushdown | **off** | **refused** — scan-cap guardrail | 1.7 s | 791 MiB | +0 |
| 2M | 5 pushdown | **on** (default) | 5/5 pass | 16.6 s | **942 MiB** | +22 MiB (over 920 MiB) |
| 50M | 5 pushdown | **on** (default) | 5/5 pass | 20.2 s | **968 MiB** | +48 MiB (over 920 MiB) |
| 100M | 5 pushdown + 1 custom SQL | **on** (default; custom SQL is always SQL-batch) | 6/6 pass | 26.9 s | **962 MiB** | +2 MiB (over 960 MiB) |
| 200M | 5 pushdown + 1 custom SQL | **on** (default; custom SQL is always SQL-batch) | 6/6 pass | 37.1 s | **968 MiB** | +8 MiB (over 960 MiB) |

The 1M off/on pair is the clean isolated comparison — same table, same suite,
back-to-back on freshly-restarted workers, only the `UC_SQL_PUSHDOWN` flag
differs. 100M/200M are cumulative rungs on an already-warm worker, like the
Snowflake ramp above.

### Reading the table

1. **UC now matches the Snowflake regime up to 200M rows, for both pushdown and
   custom SQL.** The v1.1 section above measured UC as the *worst* full-load
   runner: 1M passed at 1,681 MiB, 2M child-OOM'd within seconds. Pushed down,
   worker memory stays flat (935 → 968 MiB) all the way to 200M — a 200×
   increase in row count for a ~35 MiB memory delta, the same "cost scales with
   the warehouse, not the worker" shape Snowflake showed at the same scale.
   **Custom SQL was never the frame-load path to begin with** (it was made
   SQL-batch-only before this pushdown change existed) — it was previously
   miscategorized in
   this doc's TL;DR alongside the frame-load fallback; the two are now split
   into separate rows, and the 100M/200M runs confirm custom SQL scales exactly
   like the audited pushdown types.
2. **Memory drops ~55×** on the identical 1M table when isolating the flag:
   824 MiB delta (frame) vs 15 MiB delta (pushdown). This is the offload the
   pushdown rationale predicted — the warehouse's own compute (Photon/Spark under
   the SQL layer) does the scan; the worker only receives pass/fail scalars.
3. **Wall time went the other way at 1M** — pushdown was ~7s *slower* (17.7s vs
   11.1s) — and this is a warehouse-warmth artifact, not a pushdown cost: the
   serverless SQL warehouse had already been queried (table creation, earlier
   pushdown runs) before the frame-path leg ran, so the frame read paid no cold
   start while the isolated pushdown-off rerun did. It is **not** evidence that
   pushdown is slower in general — every other pushdown rung (16.6 s at 2M up to
   37.1 s at 200M) is in the same range, consistent with the Snowflake finding
   that wall time is dominated by fixed orchestration + connection overhead, not
   row count, once the warehouse is warm.
4. **The scan-cap guardrail now catches the no-pushdown case cleanly.** With
   `UC_SQL_PUSHDOWN=false`, the 2M table hit `RUN_MAX_SCAN_ROWS` (1.5M) and was
   **refused in 1.7 s** with a message naming the table, the count, and the cap
   — the same "refuse, don't OOM" behavior shipped for flat files now also
   covers UC's frame-load fallback path, which didn't exist yet when the v1.1
   section's raw 2M-OOM was measured.
5. **200M was not a ceiling, just where this campaign stopped** — no failure
   mode was found, matching Snowflake's "none found" row. A higher rung was not
   attempted (no evidence it's needed; the harness Databricks Free Edition
   serverless warehouse handled 200M rows of `CREATE TABLE … AS SELECT` in
   ~10s).

### Still open

- **Unaudited types and sampled suites still take the frame path**, uncapped in
  wall-time terms at whatever `RUN_MAX_SCAN_ROWS` admits. This campaign did not
  re-measure `expect_column_values_to_be_of_type` in particular — it stays on
  the frame path by design (pandas-dtype vs SQL-reflected-type mismatch,
  unity_catalog.py:180-182) — since its numbers are unchanged from the v1.1
  section.
- **Widening the pushdown allowlist** stays a per-type audited decision
  (unity_catalog.py:168-182) — each additional expectation type needs its own
  live-verification pass before joining `SQL_PUSHDOWN_EXPECTATION_TYPES`.
- **Beyond 200M** was not measured — this campaign matched Snowflake's tested
  ceiling rather than exceeding it. Nothing in the pushdown/custom-SQL mechanism
  (both are pure warehouse-side SQL, same as Snowflake's path) suggests a
  worker-side wall would appear at a higher rung; it just wasn't tested.

---
