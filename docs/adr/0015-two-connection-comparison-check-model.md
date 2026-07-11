# ADR 0015 — Two-connection comparison check model (suite = target under test; source ref on the check)

- **Status:** Accepted
- **Date:** 2026-07-11
- **Deciders:** @TheurgicDuke771

## Context

[ADR 0014](0014-reconciliation-comparison-check-kind.md) reserved `check.kind = 'comparison'` (cross-dataset reconciliation, reusing the FastAPI_DataComparison diff engine) and deferred the one genuine design decision to this ADR: **how a comparison check carries two connection refs** when everything in DataQ binds a check to exactly *one* connection through its suite. Today the kind is constraint-valid but unbuildable — `run_service._run_outcomes` raises `NotImplementedError` for it, and there is nowhere to put a second connection.

The single-connection invariant is not incidental. `Suite.connection_id` + `Suite.target` is what the platform's whole "dataset under test" lens hangs off: suite authz (ADR 0027/0033), asset resolution + run stamping (ADR 0034), incidents, lineage, the run-target resolver, and the runner registry (`build_check_runner` builds **one** runner per run from the suite's connection). A model that weakens it ripples everywhere.

A second candidate consumer exists at a different grain: [ADR 0030](0030-iceberg-native-read-path.md) deferred its "Option B" (an `iceberg` connection *referencing* an `adls_gen2`/`s3` connection for storage credentials) to this ADR's settlement — a **connection→connection** ref, versus 0014's **check→connection** ref. This ADR must also settle whether those become one generalized mechanism.

## Decision

### 1. The suite stays single-connection and supplies the **target** side; a comparison check adds exactly **one** new ref — the **source** (baseline)

A comparison check does not carry two connection refs. It reads: *"this suite's dataset (the target under test) must match baseline dataset Y."*

- **Target side = the suite's existing binding** — `suite.connection_id` + the resolved `suite.target` (including flat-file batch materialization), identical to every other check in the suite. Asset stamping, incidents, authz, and run history keep working unchanged, and a comparison result is correctly a quality statement *about the suite's dataset*.
- **Source side = new on the check**: `checks.source_connection_id` (FK → `connections.id`) + a source dataset spec in `checks.config["source"]`, shaped exactly like `Suite.target` (`table`/`schema`/`catalog`/`path`/`file_format`/batch) and validated by the same pure `run_target.resolve_target` against the *source* connection's type.
- **Either side may instead be a read-only SQL query** (SQL datasources only): `config["source"]["query"]`, and `config["target_query"]` for a target-side projection/filter — the target query always runs **on the suite's connection**. Both reuse ADR 0019's read-only custom-SQL validation (single statement, no writes/DDL), so a comparison can diff query results (FDC's native shape), not just whole tables.
- **Join keys**: `config["keys"]` — an ordered list of key columns; entries may be per-side-mapped (`{"source": col, "target": col}`) when names differ.
- FDC bucket naming maps as: suite side = FDC *target*, check ref = FDC *source*.

Cross-**env** comparison is explicitly allowed (source and suite connections may differ in `env`) — DEV-vs-QA parity and migration validation are headline use cases. The source may be any **datasource** type (all five produce DataFrames); orchestration provider types are rejected at validation (CLAUDE.md §4).

### 2. Schema: a real FK column, not JSONB; presence tied to the kind; RESTRICT on delete

- `checks.source_connection_id UUID NULL REFERENCES connections(id) ON DELETE RESTRICT`, plus `CHECK ((kind = 'comparison') = (source_connection_id IS NOT NULL))` and an index on the column. A real column (not a UUID buried in `config`) buys referential integrity, "what references this connection" queryability, and the delete guard — the same don't-bury-structure-in-JSONB reasoning as `metric_value` (ADR 0012).
- **RESTRICT, with a service-level pre-check returning a friendly 409** listing dependent comparison checks. A deleted source would otherwise leave a permanently broken check (`SET NULL` fail-soft was rejected — see Alternatives); this matches the de-facto posture of `suites.connection_id` (NO ACTION), while upgrading the UX from a raw FK error to an explanatory 409.
- `check_versions` snapshots `source_connection_id` as a **plain UUID column, no FK** — snapshots are self-contained history and must not block deleting a connection an old version once pointed at (same reasoning as ADR 0020's no-credential-snapshot rule).
- Export/import (`suite_io_service`) serializes the source ref portably as `(connection name, env)`, resolved on import — a raw UUID would never survive a workspace move. Unresolvable → clean import error.
- Migration is **additive-only** (nullable column + constraint that all existing rows already satisfy) — backward-compatible per the working agreements, no two-step needed.

### 3. Execution: a `DatasetReader` seam + the ported FDC engine; capped, fail-fast, never silently truncated

- **New `DatasetReader` seam in the registry**, per connection type (datasources only): resolve a target spec → pandas DataFrame. It grows out of plumbing that already exists — the profiler's per-type engine access for SQL sources, `flatfile`'s object read, `pyiceberg`'s scan — rather than a new subsystem. `build_check_runner` and the one-runner-per-suite shape are untouched; the worker builds source readers *additionally*, only for suites containing comparison checks.
- **The FDC diff engine is ported, not copied verbatim** (per ADR 0014: engine yes, app no) as a frame diff under `backend/app/datasources/` — join on `config["keys"]`, optional column subset/mapping, producing matched / mismatched / additional-in-source / additional-in-target buckets. The port has explicit latitude to **optimize beyond FDC's whole-frame load** — e.g. batched/chunked reads via the `DatasetReader` (Arrow batch readers exist for Iceberg; `chunksize` for SQL reads), sort/hash-merge over batches, key-only first pass — as long as bucket semantics stay exactly FDC's. No report files, no result cache, DataQ tooling (Black/mypy/pytest + the adversarial battery).
- **Row-cap discipline:** a configurable `max_rows` (config override, settings default). For SQL sources a `COUNT(*)` preflight runs first; **either side over the cap → an operational `error` result** ("dataset exceeds comparison row cap"), never a diff over truncated frames — a truncated diff produces confidently wrong mismatch buckets, which is worse than no answer. Batching raises how far the cap can sit; pushdown/hash-based comparison beyond it is the scale follow-up (G-b), not this model.
- Dispatch composes exactly like the monitor kinds: `_run_outcomes` gains a `comparison` branch beside the `expectation`/monitor branches — `kind` picks the monitor, `connection.type` picks the adapter (ADR 0012).

### 4. Results ride the existing seams (reaffirming ADR 0014 §3, now concrete)

- `expectation_type` is the canonical `comparison:records` (mirroring `monitor:<kind>`); a per-column grain `comparison:columns` (FDC's second mode) is reserved, not first-build.
- `metric_value` = **mismatch-%** (non-matching rows over the comparable universe) — a badness scalar, so ADR 0016's severity banding and the existing warn/fail/critical thresholds work unchanged; no thresholds → plain pass/fail (success = zero non-matching rows).
- Bucket counts → `observed_value`; capped per-bucket row samples → `sample_failures`, through the suite's column policy + blanket redaction like any failing sample.
- **Report files are derived, never stored.** FDC's disk reports (`comparison_reports.py`) are replaced by an **on-demand download** on the result: CSV/XLSX (format chosen at download time, not check config) generated from the persisted **redacted** buckets. A stored full-mismatch file would bypass the `sample_failures` redaction path, escape the PII-minimisation retention sweep, and assume an object store a BYOL deploy may not have. Opt-in full-report export to a user-designated flat-file connection (ADLS/S3, redaction applied) is a recorded follow-up, not first-build.

### 5. Authoring UX: side-by-side comparison editor

The check editor gains a comparison layout: **left pane = source** (connection picker → table picker or read-only SQL → key columns), **right pane = target** (connection **pre-filled and locked to the suite's connection** — the model's §1 invariant made visible — → the suite's target with optional SQL projection → key columns), **bottom = common options** (comparison type `records` now / `columns` reserved, row cap, severity thresholds). The locked target connection is deliberate UX: it teaches that a comparison check asserts something *about this suite's dataset*.

### 6. Scope guard: **no** generalized connection→connection mechanism

The check-level source ref settles 0014's question only. ADR 0030's Option B (a connection referencing another connection for credentials) is a different grain with different lifecycle semantics and still has **zero shipped consumers** — generalizing now would be speculative machinery (ADR 0011's second-impl-deferred discipline). Option B remains deferred to its own future ADR if a real need lands.

## Consequences

**Positive**
- The single-connection suite invariant survives intact — authz, assets, incidents, lineage, and the runner registry need no changes; comparison lands as *one nullable FK + a run-path branch*, the same additive shape as the monitor kinds.
- Heavy reuse: `resolve_target` validates both sides, severity banding and results/redaction/alerting seams work unmodified, and the diff engine arrives proven (FDC is unit-tested, MIT, same author).
- A comparison check is an explicit cross-dataset edge — a future *lineage signal* for ADR 0034 (source → target edge with a quality facet), deferred.

**Negative / accepted**
- In-memory diff is memory-bound like the flat-file/UC/Iceberg paths; the row cap makes large tables *honestly unsupported* rather than slow-and-wrong until G-b pushdown work.
- RESTRICT means a source connection used by comparison checks cannot be deleted until those checks are repointed/deleted (surfaced as a clear 409). Accepted as the price of never having zombie checks.
- Duplicate join-key rows make bucket semantics ambiguous — the engine port must define (and test) explicit behaviour, e.g. error the check on non-unique keys.
- The suite-level `column_policy` describes the *target's* columns; source samples reuse it on the reconciliation assumption that both sides share a logical schema. Unlisted columns still default-redact, so the posture can't regress.

## Alternatives considered

- **Two connection refs on the check (source + target both)** — rejected: decouples the check from its suite's dataset, orphaning authz/asset/incident/lineage anchoring, and makes the suite's own connection meaningless for that check. The suite already *is* one side.
- **Kind-specific side table (`comparison_specs`)** — rejected: cleaner column-nullability at the cost of a join in every CRUD/version/export/run path, for exactly one column plus config that fits the existing JSONB. Reconsider only if comparison config outgrows `config`.
- **Generic `check_connections(check_id, role, connection_id)` join** — rejected: N-ref generality with no second consumer at the check grain; speculative (ADR 0011).
- **Source ref as a UUID inside `config` (no column)** — rejected: no referential integrity, no delete guard, invisible to SQL.
- **A "comparison suite" binding two connections at suite level** — rejected: breaks the suite invariant for *all* checks in the suite, forces single-check suites, and confuses the asset lens.
- **`ON DELETE SET NULL` (fail-soft like `asset_id`)** — rejected: `asset_id` is derivable metadata a sweep may legitimately remove; a source connection is load-bearing config whose silent loss turns a passing suite into a permanently erroring one.
- **Generalize to connection→connection refs now (fold in 0030 Option B)** — rejected: different grain, no consumer, speculative machinery (see Decision §6).
- **Persist FDC-style report files at run time** — rejected for the first build: bypasses redaction, escapes the retention sweep, assumes an object store (see Decision §4); replaced by the derived on-demand download, with connection-targeted export as the recorded follow-up.

## Related

- [ADR 0014](0014-reconciliation-comparison-check-kind.md) — reserved the `comparison` kind and deferred this model decision here.
- [ADR 0012](0012-monitor-kind-seam.md) — the kind-dispatch seam this branch composes with; `metric_value`.
- [ADR 0016](0016-severity-derivation-semantics.md) — badness-% banding reused for mismatch-%.
- [ADR 0011](0011-extensibility-seams-for-deferred-integrations.md) — second-impl-deferred discipline (Decision §5); post-v1 RDBMS adapters that widen comparison's reach.
- [ADR 0020](0020-history-and-audit-strategy.md) — snapshot self-containment (versions carry the UUID, no FK).
- [ADR 0030](0030-iceberg-native-read-path.md) — Option B (connection→connection) stays deferred; not generalized here.
- [ADR 0033](0033-workspace-roles-rbac.md) — connections are workspace-visible; referencing one as a source needs no new authz surface (mutations stay Admin-only).
- Source engine: `github.com/TheurgicDuke771/FastAPI_DataComparison` (MIT) — `data_comparison/{record_comparison,column_comparison,get_dataset}.py`.
