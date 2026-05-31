# ADR 0012 — Monitor-kind seam (`check.kind` discriminator + numeric metric storage)

- **Status:** Accepted
- **Date:** 2026-05-30
- **Deciders:** @TheurgicDuke771

## Context

Every v1 check is a Great Expectations expectation (ADR 0003): a value-level
assertion on column/table data. But most real data-quality incidents are **not**
value-level — they are **freshness** (a table stopped loading) and **volume** (a
load delivered 10% of its usual rows). These are *monitors over a measured
scalar*, not expectations over row values. The post-v1 roadmap (Theme A) makes
this the leap from "a GX runner" to "a DQ platform": freshness, volume,
schema-drift, and anomaly auto-monitors.

v1 will **not** build those monitors. But the v1 check/result schema is written
in the Week-3 migration, and that migration is a **one-shot** for schema seams
(CLAUDE.md §10) — once results are written, changing the shape is a
backward-compatible two-step deploy (W5+ discipline). If v1 ships a schema that
assumes "a check is always a GX expectation, a result is always binary + a JSONB
`observed_value`," then every auto-monitor in v1.x forces a check/result schema
rewrite that ripples into the suite, check, run, and result layers.

Two specific gaps:

1. **No discriminator on `checks`.** The run path hardwires "expectation → GX."
   A freshness monitor has no expectation_type; it has an interval and a
   timestamp column. Without a `kind`, there's nowhere to branch on *what kind of
   monitor this is* (a question orthogonal to *which datasource* — that's the
   `CheckRunner` / `ConnectionAdapter` seam).
2. **Metrics only live in JSONB `observed_value`.** A freshness monitor measures
   "hours since last load = 26.5"; a volume monitor measures "row count =
   1,203,847." These are scalars you must `AVG()` / `STDDEV()` for Week-6 trend
   charts and v1.1 anomaly baselines. **You cannot aggregate a value buried in a
   JSONB blob in SQL** — the dashboard would have to pull every row and reduce in
   Python.

This decision must land **before the Week-3 threshold migration** so both
columns ride that single migration.

## Decision

**Add the monitor-kind seam in v1 — schema + dispatch only — and implement
`expectation` as the sole live kind. Reserve the other kinds; do not build them.**

This seam is **orthogonal** to the datasource seams (`CheckRunner`,
`ConnectionAdapter`, ADR 0011): those vary behaviour by *datasource type*; this
varies by *monitor kind*. A freshness monitor on Snowflake and a freshness
monitor on Unity Catalog are the same `kind`, different `CheckRunner`.

### 1. `check.kind` discriminator (schema)

- Add `kind TEXT NOT NULL DEFAULT 'expectation'` to `checks`, with a CHECK
  constraint over `('expectation', 'freshness', 'volume', 'schema_drift',
  'anomaly')`.
- v1 only ever writes `'expectation'`. The other four are **reserved**:
  constraint-valid so a v1.x monitor is a pure additive row, but no code produces
  or consumes them yet.
- Kind-specific parameters reuse the existing `checks.config` JSONB (a freshness
  monitor stores `{column, interval_hours}` there) — no per-kind columns.

### 2. Run-path dispatch by `kind`

- The run path dispatches on `check.kind`: `'expectation'` → the GX `CheckRunner`
  (existing path); any other kind raises `NotImplementedError` until its v1.x
  impl lands.
- This is a `match`/dispatch at the same layer that already selects the
  `CheckRunner` by `connection.type` (the Week-5 generic dispatch, ADR 0011) —
  the two dispatches compose (`kind` chooses the *monitor*, `connection.type`
  chooses the *adapter*). v1 has exactly one cell of that matrix populated.

### 3. Numeric metric storage (schema)

- Add to `results`:
  - `metric_value NUMERIC NULL` — the SQL-aggregatable scalar the monitor
    measured. For an `expectation`, this is the natural numeric the check yields
    where one exists (e.g. unexpected-row count / percent); NULL where a check
    has no meaningful scalar. For freshness/volume/anomaly (v1.x) it is *the*
    measurement.
  - `duration_ms INT NULL` — per-check runtime, for the Week-6 cost/perf surface
    (post-v1 Theme E).
- **`metric_value` is the aggregatable mirror of `observed_value`, not a
  replacement.** Rich/structured detail stays in JSONB `observed_value`; the one
  scalar worth trending/baselining is *also* written to `metric_value` so Week-6
  trends and v1.1 anomaly baselines are a `SELECT AVG(metric_value) ...` — never
  a JSONB reduction in Python (same discipline as the health-score SQL in
  ADR 0005).

## Consequences

**Positive**
- v1.x auto-monitors (freshness, volume, schema-drift, anomaly — Theme A) slot in
  as a new `kind` value + a new dispatch branch + a `CheckRunner`-style monitor
  impl. **No check / result / suite schema rewrite, no second two-step migration.**
- Week-6 trend charts and v1.1 anomaly baselines read one indexed NUMERIC column.
- The seam is ~two columns + one CHECK + one dispatch branch — almost free,
  because the Week-3 migration is being written anyway.

**Negative**
- `metric_value` is nullable and, for v1 expectations, often redundant with
  `observed_value`. Accepted: a nullable NUMERIC is cheap, and the redundancy is
  the whole point — one column is aggregatable, the JSONB is not.
- A reserved CHECK value set that v1 never emits looks like dead schema. Accepted
  and documented here: it is deliberate forward-compat, not an oversight.

## Alternatives considered

- **Defer the whole seam to v1.x.** Rejected: the check/result schema is written
  now; adding `kind` + `metric_value` after results exist is a backward-compat
  two-step that ripples through the check/result/suite layer — the exact retrofit
  CLAUDE.md §10 flags as a one-shot to avoid.
- **Store metrics only in JSONB `observed_value`.** Rejected: not SQL-aggregatable.
  Week-6 trends and anomaly baselines would pull every result row and reduce in
  app code, which doesn't scale and duplicates logic the DB does in one pass.
- **Build the freshness/volume monitors in v1.** Rejected: net-new scope, not in
  the 8-week plan, and GX-only (ADR 0003) means each is a non-GX monitor impl.
  Only the *seam* is built now; the monitors are post-v1 Theme A.
- **A separate `metrics` table keyed by result.** Rejected for v1: a join for one
  scalar per result, when a nullable column on `results` is simpler and indexes
  the same. Revisit only if a single result needs many named metrics.
- **Model `kind` as a separate per-kind check table (table-per-kind).** Rejected:
  multiplies the CRUD/seam surface; the JSONB `config` + a discriminator column
  already carries kind-specific params without new tables.

## Related

- ADR 0003 — GX-only for v1 (why non-expectation kinds are reserved, not built;
  DQX/streaming boundary).
- ADR 0005 — severity tier weights; **rides the same Week-3 migration** and shares
  the SQL-aggregation discipline (`status` rollup ↔ `metric_value` trends).
- ADR 0011 — datasource extensibility seams (`CheckRunner` by `connection.type`);
  the dispatch this composes with. `kind` ⟂ datasource.
- `docs/progress.md` — Week 3 "Monitor abstraction & metric storage" tasks.
- `context/DataQ_platform_roadmap.md` — post-v1 Theme A (auto-monitors).
- CLAUDE.md §5 (monitor-kind seam), §10 (Week-3 one-shot migration).
