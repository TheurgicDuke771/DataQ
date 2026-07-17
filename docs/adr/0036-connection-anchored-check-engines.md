# ADR 0036 — Connection-anchored check engines: GX universal; platform-native engines (DMF / DQX / Dataplex) unlocked per connection

- **Status:** Accepted (direction + seam shape; Snowflake DMF is the first native build, DQX/Dataplex trigger-gated — see §6)
- **Date:** 2026-07-17
- **Deciders:** @TheurgicDuke771
- **Amends:** ADR-0003 (its v1.1 engine-swap *shape* only — the suite-level `engine: gx | dqx` toggle moves to the check grain and engines become connection-anchored; 0003's core decision, GX-only for v1, stands)
- **Related:** [0003](0003-gx-only-for-v1.md) (GX-only v1; the suite-level `gx | dqx` toggle this ADR supersedes), [0011](0011-extensibility-seams-for-deferred-integrations.md) (`CheckRunner`/`ConnectionAdapter` seams), [0012](0012-monitor-kind-seam.md) (`check.kind` + `metric_value` — the axis §4 keeps orthogonal), [0030](0030-iceberg-native-read-path.md) (registry dispatch shape), [0031](0031-oss-byol-distribution-licensing.md) (license guardrail gating DQX), post-v1 roadmap gap **G-g** (GX-pin engine risk — this ADR is its abstraction answer). Issues: [#124](https://github.com/TheurgicDuke771/DataQ/issues/124) (dimensions/backend catalog), [#889](https://github.com/TheurgicDuke771/DataQ/issues/889) (scorecard consumer).

## Context

v1 is deliberately GX-only (ADR 0003): one result schema, one catalog, one editor. But the platforms DataQ monitors now ship their own DQ primitives — **Snowflake Data Metric Functions** (system + custom DMFs, Enterprise Edition), **Databricks Labs DQX** (rule engine over DataFrames/streams; the DLT/streaming cases batch-only GX can't serve), and **GCP Dataplex data-quality scans** (CloudDQ successor). Users on those platforms reasonably want DataQ to *use* the native capability rather than re-implement it — both for pushdown (checks run where the data lives, instead of pulling into the worker's pandas) and for meeting warehouse standards teams where they already are.

Two integration patterns must not be conflated:

1. **Synchronous evaluators** — DataQ calls the engine at run time and gets outcomes back (GX today; DQX; *ad-hoc* DMF invocation via `SELECT SNOWFLAKE.CORE.NULL_COUNT(...)`; Dataplex on-demand scans). This ADR.
2. **Warehouse-scheduled measurement ingest** — the platform runs checks on its own cadence (DMF schedules attached to tables, recurring Dataplex scans) and DataQ *provisions and ingests*. Structurally a pull provider (like orchestration polling / lineage pull), **not** a `CheckRunner`. Deferred to a future ADR; its open schema question is that `results.run_id` is NOT NULL and a warehouse-scheduled measurement has no DataQ run (synthetic runs vs a sibling measurements table).

ADR 0003 sketched DQX as a **suite-level** `engine: gx | dqx` toggle on UC suites. That shape predates this design and is superseded here (§2).

## Decision

### 1. Engines are capabilities of connections

There is no global engine setting and no engine config page. Every **datasource** connection offers `gx`; native engines are unlocked by connection type and validated per connection instance:

| Engine | Unlocked by connection type | Exists today? |
|---|---|---|
| `gx` | all datasource types | yes (sole engine) |
| `dmf` | `snowflake` | first native build (§6) |
| `dqx` | `unity_catalog` | trigger-gated (§6) |
| `dataplex` | `bigquery` (future type) | gated on BigQuery-as-datasource (§6) |

No Snowflake connection → no DMF anywhere in the product. This extends the dispatch shape that already exists: `registry.build_check_runner` keys on `connection.type`; it becomes two-key `(connection.type, engine)` with a capability map answering "which engines does this connection offer."

### 2. Engine selection is per **check**, not per suite

`check.engine` (TEXT, default `'gx'`, additive migration), validated on save against the suite's connection capability set. Rationale: the realistic suite is *mostly GX expectations plus a few native checks* — a suite-level toggle would force artificial suite splits. The run path already partitions one run's checks across evaluators (expectation checks → `CheckRunner`, monitor kinds → `MonitorRunner`); partitioning by engine is the same move, inside one run with one result set. **Supersedes** ADR 0003's suite-level toggle sketch (CLAUDE.md §5 updated in this PR).

### 3. Type gates the *offer*; the connection instance validates the *reality*

Native availability is not implied by type alone (DMFs need Enterprise Edition + `EXECUTE DATA METRIC FUNCTION` / `SNOWFLAKE.DATA_METRIC_USER`; DQX needs workspace job/serverless execution rights). The connection **test/re-auth flow probes** actual engine availability and stores a per-engine capability flag on the connection, **with an actionable remediation** when unavailable — what the limitation is and what access to request (e.g. "requires Enterprise Edition", "grant the DataQ role `EXECUTE DATA METRIC FUNCTION` + usage on the target database"). The stored reason is **classified guidance, never raw exception text** (the #828/#839 lesson — raw driver errors have carried credentials). Phasing: phase 1 may gate by type only, with run-time failures landing as classified `error` results; the capability flag + probe is phase 2, but the flag is designed into the schema from the start.

### 4. `kind` ⊥ `engine` — engines are alternate evaluators of existing kinds

`check.kind` says *what is measured*; `check.engine` says *who evaluates it*. Snowflake's `FRESHNESS`/`ROW_COUNT` system DMFs are alternate evaluators of the existing `freshness`/`volume` kinds — **never** new kinds (no `dmf_freshness`). This keeps the ADR 0012 seam, the scorecard's dimension mapping (#124/#889), and trend queries engine-agnostic. Each engine carries a **supported matrix** (which kinds/check types it can evaluate) — per-check information, reinforcing §2. Consequence: the expectation catalog (frontend-only today, by design) must become an **engine-aware backend catalog**; that is one story with #124's dimension classification, not two catalogs.

DMF outcomes are metric-shaped and ride the existing result semantics unchanged: scalar → `results.metric_value`, thresholds applied by the run service outside the runner (as today), severity tiers unchanged.

### 5. Lifecycle: the check's engine is tied to the connection — validated, never silent

- **Save-time:** creating/updating a check with an engine the suite's connection doesn't offer is a 422 naming the missing capability.
- **Run-time:** an engine that was available at authoring but isn't now (privilege revoked, edition downgrade, connection re-pointed) lands the check as the existing **`error` operational status** with a classified reason — never a silent skip, never counted in pass rates (`error` is already excluded from severity denominators).
- **Export/import:** a suite export carrying native-engine checks imports anywhere, but the user is warned (toast + per-check report) that N checks require platform-native capabilities the target connection may not offer; the same save-time validation marks them explicitly rather than dropping or silently converting them.
- **Connection delete** already cascades suites/checks (ADR 0020) — no new orphan class.

### 6. Build order and triggers

| Engine | Gate / trigger | Notes |
|---|---|---|
| **DMF** (Snowflake) | Build first — umbrella issue [#895](https://github.com/TheurgicDuke771/DataQ/issues/895) | Ad-hoc `SELECT` invocation of system DMFs (`NULL_COUNT`, `NULL_PERCENT`, `DUPLICATE_COUNT`, `UNIQUE_COUNT`, `ROW_COUNT`, `FRESHNESS`) mapped onto existing kinds; custom DMFs later. **Constraint:** needs a live Snowflake with Enterprise features — the current trial runs to ~2026-07-25; after that the build waits on a new subscription. |
| **DQX** (Unity Catalog) | A real Databricks/streaming user, **plus** two prereqs | (a) a remote-execution design — DQX's value is Spark-side evaluation, so DataQ must submit work to the workspace, a new architectural capability (today's UC runner pulls into pandas); (b) the Databricks **Labs license check** against ADR 0031's no-source-available guardrail *before* it stays on any roadmap. |
| **Dataplex** (BigQuery) | A BigQuery-as-datasource decision | BigQuery isn't a DataQ datasource; that decision dominates the cost and comes first. |
| **Scheduled-native ingest** (DMF schedules / Dataplex scans) | Separate future ADR | Pull-provider shape; must first settle synthetic-runs vs sibling-measurements-table for `results.run_id`. |

## Consequences

- Additive migration: `check.engine` default `'gx'`; per-engine capability flags on connections (phase 2 probe fills them). Backward-compatible; no existing row changes meaning.
- `registry` gains the two-key dispatch + capability map; run dispatch partitions a run's checks by engine.
- Backend engine-aware catalog lands with #124 (dimensions) as one story; check editor filters check types by the selected engine's supported matrix.
- The scorecard (#889) and all trend/aggregation SQL stay engine-agnostic — guaranteed by §4.
- G-g (GX-pin risk) is discharged from "watch item" to "decided abstraction": GX becomes one engine behind the seam rather than the definition of a check.

## Alternatives considered

- **Suite-level `engine` toggle (ADR 0003's sketch).** Rejected: the realistic suite is mostly GX expectations plus a few native checks — a suite-level engine forces artificial suite splits, and the per-engine supported matrix (§4) is per-check information anyway. The run path already partitions one run's checks across evaluators, so the check grain costs nothing structurally.
- **Native metrics as a monitor kind (`kind='native_metric'`) instead of an engine.** Seriously considered (2026-07-17 discussion): it would ship ad-hoc DMF calls on the existing ADR 0012 seam with no engine machinery at all. Rejected because it dead-ends — it cannot represent DQX/Dataplex (full evaluators, not metrics), blocks future warehouse-side attachment management, and violates §4 by encoding the evaluator into the kind axis.
- **Engine-specific kinds (`dmf_freshness`, `dqx_null_check`, …).** Rejected outright: forks the kind axis, so every consumer — scorecard (#889), dimension mapping (#124), trend SQL — would branch per engine forever.
- **A global/workspace engine setting.** Rejected: engine availability is a property of a *connection instance* (edition, grants, workspace rights), not of the workspace. A global toggle cannot answer "is DMF available on *this* connection" and would reintroduce the config-page indirection §1 deliberately avoids.
