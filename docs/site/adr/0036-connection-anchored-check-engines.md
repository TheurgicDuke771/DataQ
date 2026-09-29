# ADR 0036 — Connection-anchored check engines: GX universal; platform-native engines (DMF / DQX / Dataplex) unlocked per connection

- **Status:** Accepted (direction + seam shape; Snowflake DMF is the first native build, DQX/Dataplex trigger-gated — see §6)
- **Date:** 2026-07-17
- **Deciders:** @TheurgicDuke771
- **Amends:** ADR-0003 (its v1.1 engine-swap *shape* only — the suite-level `engine: gx | dqx` toggle moves to the check grain and engines become connection-anchored; 0003's core decision, GX-only for v1, stands)
- **Related:** [0003](0003-gx-only-for-v1.md) (GX-only v1; the suite-level `gx | dqx` toggle this ADR supersedes), [0011](0011-extensibility-seams-for-deferred-integrations.md) (`CheckRunner`/`ConnectionAdapter` seams), [0012](0012-monitor-kind-seam.md) (`check.kind` + `metric_value` — the axis §4 keeps orthogonal), [0030](0030-iceberg-native-read-path.md) (registry dispatch shape), [0031](0031-oss-byol-distribution-licensing.md) (license guardrail gating DQX), post-v1 roadmap gap **G-g** (GX-pin engine risk — this ADR is its abstraction answer).

## Context

v1 is deliberately GX-only (ADR 0003): one result schema, one catalog, one editor. But the platforms DataQ monitors now ship their own DQ primitives — **Snowflake Data Metric Functions** (system + custom DMFs, Enterprise Edition), **Databricks Labs DQX** (rule engine over DataFrames/streams; the DLT/streaming cases batch-only GX can't serve), and **GCP Dataplex data-quality scans** (CloudDQ successor). Users on those platforms reasonably want DataQ to *use* the native capability rather than re-implement it — both for pushdown (checks run where the data lives, instead of pulling into the worker's pandas) and for meeting warehouse standards teams where they already are.

Two integration patterns must not be conflated:

1. **Synchronous evaluators** — DataQ calls the engine at run time and gets outcomes back (GX today; DQX; *ad-hoc* DMF invocation via `SELECT SNOWFLAKE.CORE.NULL_COUNT(...)`; Dataplex on-demand scans). This ADR.
2. **Warehouse-scheduled measurement ingest** — the platform runs checks on its own cadence (DMF schedules attached to tables, recurring Dataplex scans) and DataQ *provisions and ingests*. Structurally a pull provider (like orchestration polling / lineage pull), **not** a `CheckRunner`. Deferred to a future ADR; its open schema question is that `results.run_id` is NOT NULL and a warehouse-scheduled measurement has no DataQ run (synthetic runs vs a sibling measurements table). **Snowflake-scheduled DMFs are out of scope for DataQ (decided 2026-09-29):** DataQ evaluates DMFs ad hoc and does not attach, schedule or ingest Snowflake-managed DMF runs. `ROW_COUNT`, `ACCEPTED_VALUES` and `SCHEMA_CHANGE_COUNT`, which exist only in the attached model, stay unsupported on the DMF engine; the GX engine covers row count, accepted values and schema drift on Snowflake.

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

Native availability is not implied by type alone (DMFs need Enterprise Edition + `EXECUTE DATA METRIC FUNCTION` / `SNOWFLAKE.DATA_METRIC_USER`; DQX needs workspace job/serverless execution rights). The connection **test/re-auth flow probes** actual engine availability and stores a per-engine capability flag on the connection, **with an actionable remediation** when unavailable — what the limitation is and what access to request (e.g. "requires Enterprise Edition", "grant the DataQ role `EXECUTE DATA METRIC FUNCTION` + usage on the target database"). The stored reason is **classified guidance, never raw exception text** (raw driver errors have carried credentials before — a standing lesson). Phasing: phase 1 may gate by type only, with run-time failures landing as classified `error` results; the capability flag + probe is phase 2, but the flag is designed into the schema from the start.

### 4. `kind` ⊥ `engine` — engines are alternate evaluators of existing kinds

`check.kind` says *what is measured*; `check.engine` says *who evaluates it*. Snowflake's `FRESHNESS` system DMF is an alternate evaluator of the existing `freshness` kind — **never** a new kind (no `dmf_freshness`). *(Amended 2026-08-22, slice-2 build: `ROW_COUNT` was named here as the `volume` evaluator, but it **cannot be invoked ad hoc** — Snowflake documents "You can't call this function directly", live-confirmed in every argument spelling — so `volume` stays GX-only; its `COUNT(*)` pushes down identically. `FRESHNESS` additionally accepts only `DATE`/`TIMESTAMP_LTZ`/`TIMESTAMP_TZ` bare-column arguments — a `TIMESTAMP_NTZ` column gets classified run-time guidance pointing at the GX freshness monitor.)* This keeps the ADR 0012 seam, the scorecard's dimension mapping, and trend queries engine-agnostic. Each engine carries a **supported matrix** (which kinds/check types it can evaluate) — per-check information, reinforcing §2. Consequence: the expectation catalog (frontend-only today, by design) must become an **engine-aware backend catalog**; that is one story with the dimension-classification work, not two catalogs.

DMF outcomes are metric-shaped and ride the existing result semantics unchanged: scalar → `results.metric_value`, thresholds applied by the run service outside the runner (as today), severity tiers unchanged.

### 5. Lifecycle: the check's engine is tied to the connection — validated, never silent

- **Save-time:** creating/updating a check with an engine the suite's connection doesn't offer is a 422 naming the missing capability.
- **Run-time:** an engine that was available at authoring but isn't now (privilege revoked, edition downgrade, connection re-pointed) lands the check as the existing **`error` operational status** with a classified reason — never a silent skip, never counted in pass rates (`error` is already excluded from severity denominators).
- **Export/import:** a suite export carrying native-engine checks imports anywhere, but the user is warned (toast + per-check report) that N checks require platform-native capabilities the target connection may not offer; the same save-time validation marks them explicitly rather than dropping or silently converting them.
- **Connection delete** already cascades suites/checks (ADR 0020) — no new orphan class.

### 6. Build order and triggers

| Engine | Gate / trigger | Notes |
|---|---|---|
| **DMF** (Snowflake) | Build first | Ad-hoc `SELECT` invocation of system DMFs (`NULL_COUNT`, `NULL_PERCENT`, `DUPLICATE_COUNT`, `UNIQUE_COUNT`, `FRESHNESS`, `BLANK_COUNT`, `FUTURE_TIMESTAMP_PERCENT`) mapped onto existing kinds; custom DMFs shipped 2026-09-29 (amendment below). `ROW_COUNT` was on this list and is out — it has no ad-hoc form (2026-08-22 amendment in §4). `ACCEPTED_VALUES` and `SCHEMA_CHANGE_COUNT` are out for the same reason (Snowflake documents both as "can't call this function directly"; live-confirmed 2026-09-27: a direct call rejects `ACCEPTED_VALUES`'s lambda argument as a syntax error, and without it — or `SCHEMA_CHANGE_COUNT` with no argument — returns the sentinel `-1`, never a measurement. They need the attached, scheduled DMF model — warehouse-side attachment management, not yet built). Ad-hoc DMF arguments must be a bare column of a table or view: an expression, a `VALUES` list or a CTE is refused (`Invalid argument types` / `only supports table-like objects`). **Constraint:** needs a live Snowflake with Enterprise features to build against. |
| **DQX** (Unity Catalog) | A real Databricks/streaming user, **plus** two prereqs | (a) a remote-execution design — DQX's value is Spark-side evaluation, so DataQ must submit work to the workspace, a new architectural capability (today's UC runner pulls into pandas); (b) the Databricks **Labs license check** against ADR 0031's no-source-available guardrail *before* it stays on any roadmap. |
| **Dataplex** (BigQuery) | A BigQuery-as-datasource decision | BigQuery isn't a DataQ datasource; that decision dominates the cost and comes first. |
| **Scheduled-native ingest** (DMF schedules / Dataplex scans) | Separate future ADR | Pull-provider shape; must first settle synthetic-runs vs sibling-measurements-table for `results.run_id`. |

## Consequences

- Additive migration: `check.engine` default `'gx'`; per-engine capability flags on connections (phase 2 probe fills them). Backward-compatible; no existing row changes meaning.
- `registry` gains the two-key dispatch + capability map; run dispatch partitions a run's checks by engine.
- Backend engine-aware catalog lands with the dimension-classification work as one story; check editor filters check types by the selected engine's supported matrix.
- The scorecard and all trend/aggregation SQL stay engine-agnostic — guaranteed by §4.
- G-g (GX-pin risk) is discharged from "watch item" to "decided abstraction": GX becomes one engine behind the seam rather than the definition of a check.

## Alternatives considered

- **Suite-level `engine` toggle (ADR 0003's sketch).** Rejected: the realistic suite is mostly GX expectations plus a few native checks — a suite-level engine forces artificial suite splits, and the per-engine supported matrix (§4) is per-check information anyway. The run path already partitions one run's checks across evaluators, so the check grain costs nothing structurally.
- **Native metrics as a monitor kind (`kind='native_metric'`) instead of an engine.** Seriously considered (2026-07-17 discussion): it would ship ad-hoc DMF calls on the existing ADR 0012 seam with no engine machinery at all. Rejected because it dead-ends — it cannot represent DQX/Dataplex (full evaluators, not metrics), blocks future warehouse-side attachment management, and violates §4 by encoding the evaluator into the kind axis.
- **Engine-specific kinds (`dmf_freshness`, `dqx_null_check`, …).** Rejected outright: forks the kind axis, so every consumer — scorecard, dimension mapping, trend SQL — would branch per engine forever.
- **A global/workspace engine setting.** Rejected: engine availability is a property of a *connection instance* (edition, grants, workspace rights), not of the workspace. A global toggle cannot answer "is DMF available on *this* connection" and would reintroduce the config-page indirection §1 deliberately avoids.

## Amendment (2026-09-28): DQX shipped, executed remotely

DQX is now the second native engine, offered on `unity_catalog` connections. The user chose to
build it ahead of retiring the Databricks environment. Both §6 prerequisites are settled:

- **(b) The licence check failed for bundling.** `databricks-labs-dqx` is published under the
  proprietary Databricks License, which permits use only with Databricks services. It is
  therefore never a DataQ dependency (ADR 0031, CONTRIBUTING rule 40). DataQ neither imports,
  installs nor redistributes it.
- **(a) Remote execution is the design.** For each run, DataQ uploads a fixed notebook to the
  connection user's workspace folder and submits **one** serverless job (Jobs API
  `runs/submit`) carrying every DQX check of the run. The notebook installs a pinned DQX
  version, validates each rule (an invalid rule errors only itself), applies all valid rules
  in one Spark pass, and returns **failing-row counts per rule**, never row values. The run
  path gained an optional runner hook, `run_native_checks`, so an engine with per-job start-up
  cost is batched. DMF stays per check.

**A security property this engine forced:** DQX evaluates bare string arguments as Spark SQL
(`check_funcs.get_limit_expr` → `F.expr`), live-confirmed when the list value `'SM CASE'`
resolved as a column named `SM`. DataQ therefore builds every rule from a closed vocabulary
of eight types, with typed arguments:
- columns are allowlisted identifiers, backtick-quoted
- list values are emitted as escaped SQL literals
- limits are numbers or ISO dates validated by DataQ

A user string never reaches DQX unquoted. The authoring gate and the run path share one rule
builder, so they cannot disagree.

**Capability probe:** phase 1, gated by connection type only. A missing jobs or workspace
permission lands as a classified per-check error at run time, as §2 permits. A probe would
itself cost a job start-up.

## Amendment (2026-09-29): custom DMFs shipped

§6's "custom DMFs later" is built. A Snowflake check of type `dmf:custom` runs a data metric
function the customer created (`CREATE DATA METRIC FUNCTION`), on the existing `dmf` engine.

- **Shape.** `config` is `{"function": "DATABASE.SCHEMA.FUNCTION", "columns": [...]}`. The
  name must be fully qualified. Each part goes through the shared SQL-identifier allowlist and
  is quoted by the usual folding rule (all-lower-case stays bare, anything else is quoted). The
  `SNOWFLAKE` database is refused, because the system DMFs have their own types and some return
  a sentinel `-1` when called this way.
- **Invocation.** The system functions' ad-hoc form, with 1 to 20 bare columns of the suite
  target, in the function's signature order:
  `SELECT <db>.<schema>.<dmf>(SELECT <col>[, …] FROM <target>)`. The ad-hoc rule stands: no
  expressions, no second table. Authoring and the run path share one statement builder, so a
  config that saves is exactly one that runs.
- **Result.** The return value is `metric_value`, banded by the check's thresholds
  (higher = worse, a positive fail or critical threshold required, like the other banded DMF
  types). It is shown unmasked, like every DMF metric. That is safe only because the function
  returns a number the customer chose to compute, so the user guide tells authors to return a
  count or a percentage, never a data value. The dimension is never derived (ADR 0038): NULL
  unless the author sets one.
- **Discovery.** The connection-test probe runs `SHOW DATA METRIC FUNCTIONS IN ACCOUNT` and
  stores the non-`SNOWFLAKE` results in `engine_capabilities.dmf.custom_functions` (capped at
  200) for the editor to suggest. `SHOW` was chosen over `INFORMATION_SCHEMA.FUNCTIONS`
  because it needs no warehouse and spans every database the role can see, not just the
  connection's. The list doubles as the `USAGE` check the issue asked for: Snowflake lists a
  custom DMF only to a role holding `USAGE` on it.
- **Errors.** A DMF the role cannot use raises the same `Unknown user-defined function` as one
  that does not exist, so both get one message naming the grants needed. A column list that
  doesn't match the signature, an unknown column and a missing schema each get their own fixed
  message; driver text never reaches the result.

**Live-verified 2026-09-29** on the Enterprise account with the existing least-privileged
connection role, against a throwaway schema:
- the probe listed exactly the custom DMFs the role held `USAGE` on;
- a suite run through the worker path and a dry-run evaluated a one-column and a two-column
  DMF with the right values and warn / fail / critical bands;
- an unknown DMF, an ungranted DMF, a wrong column count and an unknown column each landed as
  a classified per-check error;
- injection-shaped names and columns were refused at authoring, and the run path refuses them
  without issuing any SQL.

Scheduled (attached) DMFs stay out of scope, as recorded in §6 and the Context.

## Amendment (2026-09-29): a stream mode for DQX

Streaming is why DQX sits beside batch-only GX. A DQX check now has an optional
`mode`: `snapshot` (the default, as before) or `stream`.

**How a check targets a stream.** The target is unchanged: the suite's
`catalog.schema.table`. That can be a Lakeflow streaming table or any Delta table that grows by
appends. `stream` mode evaluates only the rows appended since the check's last persisted
result.

**Where evaluation happens.** Evaluation stays in the same one serverless job per run. The
notebook reads the table with `readStream` and an `availableNow` trigger, applies the same
DQX rules, and sums failing rows per rule from the stream's observed metrics. A `noop` sink
means nothing is written. The stream stops once it has caught up. Stream checks that resume
from the same version share one stream.

**What DataQ persists.** Failing-row counts only, as in batch mode. Each result also records
a small stream report: the Delta version range it read, the rows it evaluated, the table's
Delta id, and the number of rewrite commits it skipped. **The resume point is that recorded
version, held by DataQ and not by a Spark checkpoint.** The next run starts from the latest
evaluated result's `next_version`. A job whose result is never saved is therefore re-read,
not skipped. A persistent checkpoint would already have advanced past rows whose counts were
lost. The checkpoint Spark requires is a throwaway folder in a UC volume named on the
connection (`dqx_checkpoint_volume`). It is deleted after each run. Serverless rejects both
temporary and DBFS checkpoint locations, which was confirmed live.

**It runs on the existing run and schedule path.** There is no new ingest subsystem, table,
migration or beat task. The batch hook gains a `previous` argument, the latest evaluated
`observed_value` per check, read from `results`.

**Invariants kept.** DQX is still installed pinned in the workspace only. Rules still come
only from the closed vocabulary, through the same builder, and `mode` never reaches DQX. The
job still returns only counts: its `collect()` calls read grouped counts, table history, or
a `limit(0)` pre-flight, never rows.

**Honesty rules.**
- A run with no new rows is `skip`, not `pass`.
- A stream report DataQ cannot trust makes that check error, so it never becomes a resume
  point.
- A replaced table (its Delta id changed) or an expired history restarts the stream from the
  whole table, and the result says `restarted`.
- Rewrite commits (`UPDATE`/`DELETE`/`MERGE`/overwrite) are skipped with `skipChangeCommits`
  and counted in `change_commits_skipped`, since the mode is defined over appends.

**A platform fact that shaped the notebook:** a streaming query that fails fails the whole
Databricks command, even when the Python exception is caught, and even with
`dbutils.notebook.exit` in the handler (live). So predictable stream failures are prevented
up front instead of caught:
- a resume version past the latest commit starts no stream
- each rule is pre-flighted on an empty read
- rewrite commits are skipped rather than raised

A stream failure that still occurs errors every DQX check in that run.

**Rejected alternatives.**
- *Reading a Lakeflow pipeline's expectation metrics from its event log.* It covers only
  pipelines the user already runs and only the expectations defined inside them. It needs
  pipeline-id configuration and a second evaluator vocabulary, and it cannot evaluate DataQ's
  own rules.
- *Provisioning a DQX step inside the user's pipeline.* That is pipeline management:
  DataQ would edit user infrastructure, which the orchestration model (monitor and trigger
  only) rules out.
- *A persistent checkpoint per check.* It is simpler to state, but it loses counts on a failed
  persist, as described above.

Both rejected designs are heavier, and neither evaluates a stream with DataQ's rules.

**Live-verified 2026-09-29 (Databricks Free Edition, serverless).** The runs went through
`execute_run`, the UC runner and the real job:
- an append-only Delta table: a full first read, an increment-only second read, a
  no-new-rows skip, and an `UPDATE` skipped and counted
- a Lakeflow streaming table (`CREATE STREAMING TABLE … AS SELECT * FROM STREAM …`): a first
  read, then only the rows a `REFRESH` appended
