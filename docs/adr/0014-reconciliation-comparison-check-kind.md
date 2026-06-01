# ADR 0014 — Cross-dataset reconciliation as a `comparison` check kind (reuse FastAPI_DataComparison engine)

- **Status:** Accepted
- **Date:** 2026-06-01
- **Deciders:** @TheurgicDuke771

## Context

We evaluated folding the **FastAPI_DataComparison** tool (`github.com/TheurgicDuke771/FastAPI_DataComparison`, MIT, same author) into DataQ. FDC is a pandas-based **source-vs-target reconciliation** engine: row-level and column-level diff joined on primary keys, producing matched / mismatched / additional-in-source / additional-in-target buckets, across SQLite, CSV, PostgreSQL, MySQL, MSSQL, Oracle, and Snowflake.

Reconciliation answers a question Great Expectations (ADR 0003) structurally does not: **"does dataset A match dataset B?"** — migration validation, ETL output checks, "did the copy land correctly." It is **complementary, not redundant**: GX asserts rules over *one* dataset; reconciliation diffs *two*. That makes it a genuine capability add rather than an overlap.

Three facts shape how it can integrate:

1. **DataQ has no plugin runtime.** "Plugin" here means integrating through the existing seams, not a dynamically-loaded module. The right seam already exists: the **monitor-kind seam** ([ADR 0012](0012-monitor-kind-seam.md)) was built for exactly this shape — a non-expectation check dispatched by `check.kind`, orthogonal to the datasource (`CheckRunner` / `ConnectionAdapter`) seams.

2. **Only FDC's engine is reusable; its app is not.** FDC's connection store (SQLite `connections.db`, passwords hex-encoded plaintext), auth (bearer token, local-only `127.0.0.1`), UI (Jinja templates), and result storage (disk files) all conflict with DataQ's hardened equivalents (Postgres, `SecretStore`/Key Vault, Azure AD, React, `results` table). The pure diff engine — `data_comparison/{column_comparison,record_comparison,get_dataset}.py` — is the part worth porting.

3. **A comparison check references *two* connections** (source + target), which breaks DataQ's invariant that a check/suite binds to **one** connection. This is the one genuine design decision and is deferred to a future ADR (see Decision §5).

## Decision

**Reconciliation enters DataQ as a new reserved `check.kind = 'comparison'` on the ADR-0012 monitor-kind seam — reserved now, built post-v1. We reuse FDC's diff engine, not its app.**

1. **Reserve `comparison` as a `check.kind` value.** Add it to the Week-3 `checks.kind` CHECK set defined in [ADR 0012](0012-monitor-kind-seam.md) §1, alongside `freshness / volume / schema_drift / anomaly`. v1 never emits it — constraint-valid, no producer or consumer — exactly the reserved-kind pattern 0012 established. This is the only v1 action: one enum value in a migration being written anyway.

2. **Reuse the engine as a future `ComparisonCheckRunner`, dispatched by kind** (the `check.kind` dispatch composes with the Week-5 `CheckRunner`-by-`connection.type` dispatch, ADR 0011 — `kind` picks the *monitor*, `connection.type` picks the *adapter*). Explicitly **dropped and replaced** by DataQ equivalents: SQLite metadata → Postgres; hex-plaintext secrets → `SecretStore`/Key Vault; bearer-token → Azure AD; Jinja → React; disk `results/` files → `results` table; FDC's 60s `result_cache` → DataQ run history. New RDBMS datasources (postgres / mysql / mssql / oracle) arrive as **additive `ConnectionAdapter`s** (the post-v1 RDBMS note in ADR 0011); Snowflake overlaps with what DataQ already has.

3. **Map outputs onto existing seams, no new result shape:** match-% / mismatch-count → `results.metric_value` (the SQL-aggregatable scalar ADR 0012 reserved); matched/mismatched/additional samples → `sample_failures` via the `ResultPublisher` seam (ADR 0011), inheriting PII redaction; execution via the Celery `run_suite` path. Reconciliation produces a natural `metric_value` (e.g. match rate), so it slots into Week-6 trend charts for free.

4. **Build is post-v1 (v1.x), not v1 scope.** Reserving the kind keeps the door open at near-zero cost — the same "pay nothing during v1 to keep the option live" discipline as [ADR 0010](0010-provider-agnostic-infrastructure-seams.md) / [ADR 0013](0013-marketplace-distribution-and-anti-lock-in.md). No reconciliation code, UI, or runner ships in v1.

5. **The two-connection check model is deferred to a future ADR (0015, pending).** *How* a `comparison` check carries source + target connection refs — a dedicated kind-specific table, two columns on `checks`, or a small join — stresses the single-connection check/suite invariant and is the real design decision. It is written when the build starts, not now. **Reserving the kind does not make a comparison check buildable in v1's schema** — that is deliberate (reservation ≠ buildability).

## Consequences

**Positive**
- Adds reconciliation — a capability GX cannot provide — with no new subsystem, sidecar, or plugin loader; it rides the monitor-kind dispatch already being built.
- Reserving the kind costs one enum value in the Week-3 migration; the runner slots in later with **no check/result/suite schema rewrite and no second two-step migration** (the whole point of ADR 0012).
- We port a **proven, unit-tested** engine rather than inventing diff logic.

**Negative**
- The two-connection invariant break is **unresolved** until ADR 0015 — a `comparison` check cannot actually be modeled in v1's single-connection check schema. Accepted: this ADR reserves the seam and records the decision; the model decision is intentionally deferred.
- Porting cost is real: the engine must conform to DataQ's tooling (conda + Black + mypy + pytest + Bandit) where FDC is venv + ruff + unittest.
- FDC loads full DataFrames into memory — fine for a local tool, risky for large tables on a shared platform. The runner needs row caps / pushdown (FDC's `_wrap_with_limit` + `inline_row_limit` hooks are the starting point).

## Alternatives considered

- **Run FDC as a sidecar service DataQ calls over HTTP** — rejected: duplicates auth, secret storage, and result persistence; two security postures; comparison results live outside the unified dashboard.
- **MCP federation only** (mount FDC's 9 MCP tools at `/mcp`) — rejected *as the integration*; acceptable as an interim AI-surface convenience. It unifies nothing below the tool layer (UI, results, auth) and leaves reconciliation outside the platform.
- **Add `comparison` as a new datasource type** — rejected: it is a monitor *kind*, not a datasource (CLAUDE.md §4); it is orthogonal to `ConnectionAdapter`, exactly the `kind` ⟂ datasource split ADR 0012 draws.
- **Build it in v1** — rejected: net-new scope outside the 8-week plan, and the two-connection model needs its own ADR first.
- **Don't reserve the kind now** — rejected: adding `comparison` to the CHECK set after results exist is a backward-compat two-step — the precise retrofit ADR 0012 was written to avoid.

## Related

- [ADR 0012](0012-monitor-kind-seam.md) — monitor-kind seam; `comparison` joins its reserved `check.kind` CHECK set and reuses `metric_value`.
- [ADR 0011](0011-extensibility-seams-for-deferred-integrations.md) — `ResultPublisher` + post-v1 RDBMS `ConnectionAdapter`s the comparison datasources need.
- [ADR 0003](0003-gx-only-for-v1.md) — GX-only for v1 (why reconciliation is a separate kind, not a GX expectation).
- **ADR 0015 (pending)** — two-connection check model (how a `comparison` check carries source + target connection refs).
- Source engine: `github.com/TheurgicDuke771/FastAPI_DataComparison` (MIT) — `data_comparison/{column_comparison,record_comparison,get_dataset}.py`.
- CLAUDE.md §4 (datasource vs orchestration distinction), §5 (monitor-kind seam reserved kinds).
