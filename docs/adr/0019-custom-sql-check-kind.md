# ADR 0019 — Custom-SQL checks ride `kind='expectation'` via GX `UnexpectedRowsExpectation`

- **Status:** Accepted — **amended 2026-08-08** (see the amendment section below: the Unity Catalog half of the gating decision had never worked and needed a runner branch, [#1179](https://github.com/TheurgicDuke771/DataQ/issues/1179))
- **Date:** 2026-06-14
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0003](0003-gx-only-for-v1.md) (GX-only v1), [0012](0012-monitor-kind-seam.md) (the `check.kind` seam — and why custom-SQL is *not* a new kind), [0016](0016-severity-derivation-semantics.md) (binary fallback when there's no bandable metric), [0010](0010-provider-agnostic-infrastructure-seams.md) (least-privilege connection roles)

## Context

The Week-4 plan calls for a Monaco custom-SQL check editor, and the progress ledger flagged it **backend-blocked: "need a custom-SQL check kind."** That assumption predates a close look at GX Core.

GX Core ships [`UnexpectedRowsExpectation`](https://docs.greatexpectations.io/docs/reference/api/expectations/UnexpectedRowsExpectation_class): a Batch Expectation that runs a user-supplied SQL query and **fails if the query returns one or more rows** (success = zero rows). The query references the run target with a `{batch}` placeholder. Crucially, it rides the machinery we already have:

- `Check` already has `expectation_type: str` (free-form, title-cased to a GX class at run time) + `config: JSONB` (free-form GX kwargs). Check CRUD validates neither — they are pass-through.
- `gx_runner._to_gx_expectation` is generic: `getattr(gxe, PascalCase(expectation_type))(**config)`. `unexpected_rows_expectation` → `gxe.UnexpectedRowsExpectation(unexpected_rows_query=...)` needs **no runner change**.
- The run / result / severity / dry-run path is `kind='expectation'`-shaped and already exercised.

A de-risk run through the real path (an `UnexpectedRowsExpectation` against the dev Postgres) confirmed it end-to-end: a 0-row query → `success=True`, `observed_value=0`; a row-returning query → `success=False`, `observed_value=<unexpected row count>`; `{batch}` substitution and `to_suite_outcome` mapping both work, with **zero changes** to `gx_runner`.

A distinct `kind='custom_sql'` (the original assumption) would mean a migration to add the kind **and** relax `expectation_type NOT NULL`, plus kind-based runner dispatch — all to re-implement what `UnexpectedRowsExpectation` already gives under `kind='expectation'`. The `check.kind` seam (ADR 0012) exists for **auto-monitors** (freshness / volume / …) that are *not* expectations and produce a measured scalar instead of a row-level assertion. Custom-SQL **is** a row-level assertion expressed in SQL — it belongs under `expectation`, not as a sibling kind.

## Decision

**A custom-SQL check is a GX `UnexpectedRowsExpectation`, persisted as a normal `kind='expectation'` check:** `expectation_type='unexpected_rows_expectation'`, `config={"unexpected_rows_query": "<SQL with {batch}>"}`. No new `kind`, no migration, no `gx_runner` change. The progress-ledger "needs a new kind" note is hereby revised.

What v1 **does** add — guardrails, because this is the first path that executes user-authored SQL against a live warehouse:

1. **Read-only, single-statement validation** (app layer). The query must be a single statement and read-only: `SELECT`/`WITH` only; reject DML/DDL/DCL (`INSERT/UPDATE/DELETE/MERGE/TRUNCATE/DROP/ALTER/CREATE/GRANT/REVOKE/…`) and statement-chaining (a stray `;` with a trailing statement). Enforced in `check_service` create/update **and** suite import, so it can't be smuggled in through any authoring path.
2. **Datasource gating.** Custom-SQL is offered only for **SQL-queryable** datasources — Snowflake and Unity Catalog. Flat-file stores (ADLS / S3) are GX DataFrame assets, not SQL, so a custom-SQL check there can never run; reject it at author time against the suite's connection type.
3. **Defense-in-depth, not app-layer-only.** App-layer parsing is best-effort (it is not a SQL firewall); the real boundary is the **connection's least-privilege role** (ADR 0010). The ADR + connection docs state that a connection used for custom-SQL should authenticate as a read-only role, and warehouse statement timeouts bound runaway queries. Where the lexer can't be sure it **fails closed**: an unterminated string/comment (the rest of the query was swallowed as literal text) is rejected, not accepted; and backtick is *not* treated as a string quote (Snowflake/UC don't quote with it, so a `` ` ``-span would be a hiding spot — backtick content is scanned as code). The guardrail runs on **every** path that authors or executes the query — check create/update, suite import, **and dry-run** (the path that actually runs the SQL before save).
4. **Binary pass/fail in v1.** `UnexpectedRowsExpectation` emits an unexpected **row count** (`observed_value`), not an unexpected-percent, so `severity.extract_metric` finds no bandable metric and the check resolves binary `pass`/`fail` (ADR 0016 fallback) — exactly right for "this query should return no rows." Banding severity on the row count (populating `metric_value` from the count) is a deferred enhancement, not a v1 need.

## Consequences

**Positive**
- Smallest possible surface: no migration, no new `kind`, no runner branch. The custom-SQL check flows through the existing run/result/severity/dry-run path unchanged — the GX-only-v1 architecture (ADR 0003) pays off again.
- Security is concentrated in one validation module reused by every authoring path, with the connection role as the real backstop.
- Forward-compatible: if count-based severity is wanted later, it's an additive change in `severity.extract_metric` (read `observed_value` for this expectation_type), no schema change.

**Negative / watch**
- `expectation_type` stays free-form (no server allowlist), so the SQL guardrail — not a type allowlist — is the control for this path. A general `expectation_type` allowlist is noted as optional later hardening (it would also catch typo'd expectation types).
- App-layer read-only parsing can be fooled by exotic SQL; we accept that and lean on the least-privilege role. We do **not** claim the parser is a security boundary.
- `{batch}` is a GX-owned placeholder; the editor/docs must teach it, and a query that forgets it (a bare table name) runs against whatever the author typed, not the suite's run target — a correctness footgun the dry-run preview helps surface.

## Amendment — 2026-08-08 ([#1179](https://github.com/TheurgicDuke771/DataQ/issues/1179)): the Unity Catalog half needed a runner branch after all

Status: **Accepted.** The decision above stands for Snowflake exactly as written. Two of its claims were wrong for Unity Catalog, and stayed wrong from this ADR's date until #1179 — every UC custom-SQL check errored, in a run *and* in a dry-run, for the whole of that period. Nobody noticed because no test paired the two: the round-trip test (`test_custom_sql_gx.py`) stands Postgres in "for the warehouse (which has no live connect in CI)", and every UC runner test used ordinary expectations, which work fine on a DataFrame.

**What was wrong.**

1. §Decision says *"no `gx_runner` change"* and §Consequences says *"no runner branch"*. True of `gx_runner` — it is still untouched — but **not** of the runners. It holds for Snowflake because `SnowflakeCheckRunner` already builds a **SQL** batch (`add_snowflake` → `add_table_asset`). `UnityCatalogCheckRunner` builds a **pandas DataFrame** batch (the DQX swap-in shape, CLAUDE.md §5), and `UnexpectedRowsExpectation` depends on `unexpected_rows_query.table` / `.row_count`, both declared `@metric_value(engine=SqlAlchemyExecutionEngine)` with **no pandas provider**. So GX raised `No provider found for unexpected_rows_query.table using PandasExecutionEngine` before any query ran.

2. §Decision 2 (*datasource gating*) reasons that custom SQL is offered on "SQL-queryable datasources — Snowflake and Unity Catalog", because "flat-file stores are GX DataFrame assets, not SQL". The premise was applied to the **connection** when the thing that decides is the **batch the runner builds**. By that test UC was, at the time, in the same position as ADLS/S3.

**What changed.** `UnityCatalogCheckRunner.run_checks` now partitions: custom-SQL checks run against a GX **Databricks-SQL** batch over the same table; every other expectation keeps the DataFrame batch; outcomes merge back in submission order. Deliberately GX's own SQL datasource rather than a hand-rolled COUNT/LIMIT, so the semantics are identical to the Snowflake path *by construction* — and so no new SQL-string interpolation is introduced, leaving the guardrails in §Decision 1–3 inherited unchanged rather than re-implemented.

**What did not change.** The persisted shape (`kind='expectation'`, `expectation_type='unexpected_rows_expectation'`, `config={"unexpected_rows_query": …}`), the guardrail module and every authoring path that calls it, `gx_runner`, and §Decision 4's binary pass/fail (the row count is still not a bandable metric; count-based severity remains the deferred enhancement, now tracked as [#1202](https://github.com/TheurgicDuke771/DataQ/issues/1202)).

**One new constraint, UC only.** GX's `DatabricksDsn` requires `catalog` *and* `schema` on the connection URL, and an unqualified name would resolve against the session default — a wrong table read rather than an error. So a UC suite target with **no schema** makes its custom-SQL checks error (its other checks are unaffected). Snowflake is unchanged: its schema comes from the connection config.

**The lesson, which is the reason this amendment exists rather than a silent fix:** the original validation section below records a de-risk run "against the dev Postgres" and calls it confirmation of the decision. It confirmed the decision for *a* SQL backend. The gating list it justified named two datasources, and only one of them had ever been executed. That is the #953 shape — a capability declared from reasoning about a connection rather than from running the code path. See `docs/feature-matrix.md` footnote ᶜ for the live numbers that now back the UC tick.

## Validation

De-risk script confirmed the GX round-trip (pass/fail + `observed_value` + `{batch}`) through the unchanged `gx_runner` against the dev Postgres. PR 1 lands the validation module + datasource gating + an adversarial SQL battery (DML / multi-statement / comment-smuggled / empty) and the GX round-trip test; the dry-run path (PR 2) lets the editor preview before save; the Monaco editor (PR 3) authors `unexpected_rows_query` and mirrors the read-only validation client-side.
