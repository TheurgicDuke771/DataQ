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
- **The FDC diff engine is ported, not copied verbatim** (per ADR 0014: engine yes, app no) as a frame diff under `backend/app/datasources/` — join on `config["keys"]`, optional column subset/mapping, producing matched / mismatched / additional-in-source / additional-in-target buckets. The port has explicit latitude to **optimize beyond FDC's whole-frame load** (batched/chunked reads via the `DatasetReader`, and similar) as long as bucket semantics stay exactly FDC's; technique choices are build-time detail, not this ADR. No report files, no result cache, DataQ tooling (Black/mypy/pytest + the adversarial battery).
- **Row-cap discipline:** a configurable `max_rows` (config override, settings default). For SQL sources a `COUNT(*)` preflight runs first; **either side over the cap → an operational `error` result** ("dataset exceeds comparison row cap"), never a diff over truncated frames — a truncated diff produces confidently wrong mismatch buckets, which is worse than no answer. Batching raises how far the cap can sit; pushdown/hash-based comparison beyond it is the scale follow-up (G-b), not this model.
- Dispatch composes exactly like the monitor kinds: `_run_outcomes` gains a `comparison` branch beside the `expectation`/monitor branches — `kind` picks the monitor, `connection.type` picks the adapter (ADR 0012).

### 4. Results ride the existing seams (reaffirming ADR 0014 §3, now concrete)

- `expectation_type` is the canonical `comparison:records` (mirroring `monitor:<kind>`); a per-column grain `comparison:columns` (FDC's second mode) is reserved, not first-build.
- `metric_value` = **mismatch-%** (non-matching rows over the comparable universe) — a badness scalar, so ADR 0016's severity banding and the existing warn/fail/critical thresholds work unchanged; no thresholds → plain pass/fail (success = zero non-matching rows).
- Bucket counts → `observed_value`; capped per-bucket row samples → `sample_failures`, through the suite's column policy + blanket redaction like any failing sample.
- **Report files are derived, never stored.** FDC's disk reports (`comparison_reports.py`) are replaced by an **on-demand download** on the result: CSV/XLSX (format chosen at download time, not check config) generated from the persisted **redacted** buckets. A stored full-mismatch file would bypass the `sample_failures` redaction path, escape the PII-minimisation retention sweep, and assume an object store a BYOL deploy may not have. Opt-in full-report export to a user-designated flat-file connection (ADLS/S3, redaction applied) is a recorded follow-up, not first-build.

### 5. Authoring UX: side-by-side comparison editor

The check editor gains a side-by-side comparison layout — **left = source** (connection picker, table or read-only SQL, key columns), **right = target** (connection **pre-filled and locked to the suite's** — the §1 invariant made visible — with optional SQL projection), common options below (mode, row cap, thresholds). Exact layout is build-time detail; the locked target connection is the one binding piece of this section.

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

## Amendment — 2026-09-16: comparison sources do not support `sampling`

**Background:** the platform's opt-in `sampling` block on a suite's target (a runner-side, `head`/`random` row-*position* draw) went live earlier in the reconciliation feature's life. Because a comparison check's `config.source` is validated by the same `resolve_target` a suite target is, it *accepted* a `sampling` block that the comparison run path then silently dropped when building the internal dataset spec — a save that looked successful and a run that materialized the whole side anyway. That honesty gap was closed with a 422 refusal at `check_service.validate_comparison_check`. This amendment settles the follow-on question the refusal deferred — build coherent sampling, or record that it is out of scope. **Decision: out of scope, not built.**

**Why two independent draws are confidently wrong, not merely imprecise.** A comparison diffs by key: it reads the source, reads the target, and joins them on `config.keys`. The suite-target `sampling` mechanism is a *positional* draw local to one dataset — "materialize fewer rows of this one target," honoured entirely inside one `CheckRunner`/`DatasetReader`, with no notion of what the other side of a diff contains. Apply that unmodified to both sides of a comparison and the two draws share almost no keys by construction: a 100k-row uniform sample of a 5M-row source and an independent 100k-row uniform sample of a 5M-row target overlap in roughly `100k² / 5M ≈ 2,000` keys, so on the order of 98% of both samples would report as "additional in source" / "additional in target." That is not a noisy estimate of the mismatch-rate — it is a full-authority reconciliation result (mismatch-%, severity banding, `sample_failures`) asserting the two datasets disagree almost completely, quite possibly while they agree perfectly. A refusal tells the author nothing ran; a coherently-wrong diff tells them something false with all of §4's result machinery behind it, which is worse.

**Why this needs a different mechanism, not a bigger version of the existing one.** The suite-target `sampling` mechanism is single-dataset and read-shape-local: it never needs to know what any other reader is doing. Sampling a comparison *correctly* means the same key set must land on both sides — draw a key set (from one side, a hash partition, or similar) and then fetch **exactly those keys** from both readers before diffing. That is a join-aware operation spanning two connections and two adapters, and — for SQL sources — a second targeted round trip per side (a keyed `WHERE key IN (...)` fetch, or equivalent) that positional `head`/`random` sampling has no shape for at all. Building it means designing a new `DatasetSpec`-adjacent mechanism, not extending the existing `sampling` block's semantics to a second dataset.

**The enforced contract stays exactly the existing refusal, and it is total, not partial.** `check_service.validate_comparison_check` 422s any `sampling` block under `config.source` unconditionally. One option on the table was to lift the refusal only for the shapes that turn out to be genuinely supported by a partial implementation — since none does, the refusal is not narrowed for any shape (source query, whole-table, cross-env, or otherwise). Nothing about the refusal's mechanism changes here; this amendment is the recorded rationale the refusal's own error message points at.

**Sanctioned alternatives for a comparison over a large dataset**, both already supported by this ADR's §1 and §3:
- **`COMPARISON_MAX_ROWS` fail-fast** (default 100,000 rows per side, overridable per check via `config.max_rows`, validated in `validate_comparison_check`) — either side over the cap ends the run in an operational `error` naming the cap, never a diff over truncated frames (Decision §3).
- **Narrow the source or target with a filter, not a statistical sample.** `config.source.query` (source) and `config.target_query` (target, always run on the suite's own connection) are read-only SQL projections (Decision §1, reusing ADR 0019's custom-SQL validation) — reduce the comparison to the date range, partition, or key subset an author actually needs reconciled. This is a deliberate, author-controlled narrowing of *what* is compared, not an approximation of the whole standing in for it.

**Reopen trigger.** This is not a permanent technical ceiling — it is "not built because no consumer has needed it yet, and building it wrong is worse than refusing." Reopen when a real user has a reconciliation over more than `COMPARISON_MAX_ROWS` rows per side that a query filter genuinely cannot narrow, with a concrete key-set design (how the key set is drawn, how both readers are told to fetch exactly it, and how both sides' `sampling` records land on the result, matching the existing sampling record's own shape) — the design work belongs to whoever picks it up with a real dataset shape in hand, not to this decision record.
