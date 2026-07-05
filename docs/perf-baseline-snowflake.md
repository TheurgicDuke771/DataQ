# Snowflake scale baseline (#587) — the G-b pushdown reference datum

> Captured **2026-07-04**, days before the harness Snowflake subscription lapsed
> (v1.1 W1). This is the **pushdown-path reference** the v1.1 W6 scale-aware
> execution work ([#595](https://github.com/TheurgicDuke771/DataQ/issues/595) —
> flat-file/UC sampling + OOM guardrails) is compared against: on a SQL-pushdown
> datasource, DataQ's cost scales with the *warehouse's* ability to aggregate,
> not with row volume through the worker. The environment is gone (subscription
> lapse, #588/#590); the numbers survive here.

## Environment

| | |
|---|---|
| Datasource | Snowflake Standard (AWS us-west-2), warehouse `DATAQ_WH` (**XSMALL**, auto-suspend) |
| Data | `DATAQ_DB.PERF.ORDER_LINES` — **1,199,854 rows** (6 cols) · `DATAQ_DB.PERF.ORDERS_HEADER` — **400,000 rows** (11 cols, `ORDER_TS` timestamp) |
| Data source | harness mock-data generator, new `PERF` volume tier (400K orders → ~1.2M order lines), loaded via `write_pandas` (ORDER_LINES 11.6s, ORDERS_HEADER 8.2s) |
| App | local docker-compose stack (api + Celery worker + Postgres + Redis) at `v1.0.0` + #602/#603, driven through the real REST API (dev-bypass) — same code path as prod |
| Access role | `DATAQ_READER` (least-privilege SELECT), password auth |

## Headline numbers

| Measurement | Value |
|---|---|
| Test-connection (`SELECT 1`) | 3.7 s |
| **Suite run, 1.2M-row table** — 6 expectations + 1 volume monitor | **12.2 s** wall (trigger → terminal), `succeeded`, all pass |
| **Suite run, 400K-row table** — freshness monitor + 2 expectations | **8.1 s** wall, `succeeded` (freshness metric: age 10.6 h) |
| **Column profiler, 4 columns on 1.2M rows** (row count, nulls, distinct, min/max, top-10) | **2.6 s** |
| **Worker memory delta during the 1.2M-row run** | **< 50 MB** (idle 1.99 GiB → peak 2.04 GiB, sampled every 2 s) |
| Snowflake compute, entire session (2 loads + 3 suite runs + profiler + reruns) | 54 SELECTs, **~2.3 s total execution time**, slowest single query **1.0 s** |
| Credits burned (metering, XSMALL) | **~0.08 credits** across the session's two hour-buckets (0.024 + 0.056) |

## Per-query attribution (slowest, from `QUERY_HISTORY`)

Per-check `duration_ms` stays NULL in v1 by design (see `run_service.py` — per-check
timing is a deferred datum), so per-check cost below is attributed from the
warehouse's own query history:

| ms | Query (GX-generated / profiler) |
|---|---|
| 647 | unexpected-count aggregate (`SUM(CASE …)`) — the uniqueness/row-condition check on 1.2M rows |
| 484 | profiler batched aggregate: `COUNT(*)`, nulls, `COUNT(DISTINCT)`, min/max on `LINE_ID` |
| 388 | unexpected-values sample fetch (`LINE_ID` uniqueness) |
| 213 | profiler top-10 `GROUP BY` on `LINE_ID` (the ~1.2M-distinct worst case) |
| 26–52 | remaining profiler top-10s (`QTY`, `UNIT_PRICE`, `SKU_ID`) |
| ≤ 15 | each between/not-null expectation aggregate; monitor scalars (`MAX(ts)`, `COUNT(*)`) are sub-10ms |

## What this means for #595 (W6 scale-aware execution)

- **Pushdown keeps the worker out of the data path**: 1.2M rows cost the worker
  <50 MB and the wall time is dominated by GX orchestration + connection setup
  (~10 s of the 12.2 s; actual warehouse compute ≈ 1.5 s). The flat-file/UC
  runners, which pull data *into* the worker, are the paths that need sampling +
  OOM guardrails; this table shape (1.2M × 6 cols ≈ 60 MB CSV) is the reference
  workload to replay there.
- **The per-run overhead floor matters more than per-check cost** at this scale:
  each additional expectation added ~15–650 ms of warehouse time against a ~10 s
  fixed overhead (ephemeral GX context + engine + validation plumbing per run).
- **Cost of a realistic schedule is negligible on pushdown**: at ~0.01–0.02
  credits per suite-run session on XSMALL, even a 15-minute schedule is ~1–2
  credits/day; the auto-suspend window, not the checks, drives spend.
- Gaps found while measuring (already tracked): per-check `duration_ms` NULL in
  v1 (deferred by design); `checks_total` cosmetic 0 on the run list
  ([#571](https://github.com/TheurgicDuke771/DataQ/issues/571)); run failure
  reasons not surfaced ([#605](https://github.com/TheurgicDuke771/DataQ/issues/605)).

## Method / reproducibility

Generator: `python -m mockdata backfill --tier PERF --seed 587 --no-issues`
(harness repo, ADR 0021 — the `PERF` tier was added for this baseline). Load:
`write_pandas` into `DATAQ_DB.PERF` (note: pandas `datetime64` needs
`use_logical_type=True` or timestamps land as epoch `NUMBER` and freshness
monitors error with "not a date/timestamp"). Measurement scripts drove the real
API: create connection → test → two suites (targets `PERF.ORDER_LINES` /
`PERF.ORDERS_HEADER`) → checks incl. `monitor:volume` + `monitor:freshness` →
`POST /suites/{id}/run` → poll to terminal → results; worker memory sampled via
`docker stats`; credits via `INFORMATION_SCHEMA.WAREHOUSE_METERING_HISTORY`,
query timings via `INFORMATION_SCHEMA.QUERY_HISTORY`.
