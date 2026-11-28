# ADR 0016 — Severity derivation semantics (band the unexpected-%, thresholds override GX success)

- **Status:** Accepted
- **Date:** 2026-06-04
- **Deciders:** @TheurgicDuke771

## Context

[ADR 0005](0005-severity-tier-weights.md) settled the severity *tiers* (`pass / warn / fail / critical`), their weights, and the health-score formula, and [ADR 0012](0012-monitor-kind-seam.md) added the numeric `results.metric_value`. Neither specified the **derivation algorithm**: given a check's three optional thresholds and a GX result, *which* number do the thresholds band, in which direction, and how does that interact with GX's binary `success`?

This must be settled before severity post-processing writes results, because — per ADR 0005 — **the tier a result was assigned is immutable history**. The stored `metric_value` and `status` are written once and trended/aggregated forever; their meaning cannot be silently re-pointed after data exists.

What a GX `CheckOutcome` actually provides per check: a binary `success`, an `observed_value` scalar, and — for column-value expectations (the v1 majority) — an **unexpected fraction** (`unexpected_percent` / `unexpected_count`).

## Decision

**Approach A — thresholds band the unexpected-percent (a "higher = worse" badness scalar); when set, they fully determine the tier, overriding GX's binary success.**

1. **`metric_value` = GX `unexpected_percent` (0–100)** — how badly the rule was violated; `0` = clean, higher = worse. It is the SQL-aggregatable badness scalar (ADR 0012), extracted at run time and persisted to its own column so it survives the later `sample_failures` retention purge. Stored via `Decimal(str(...))` so a float `0.5` lands as exact `0.5` in the NUMERIC column. Aggregate checks (e.g. `expect_table_row_count_*`) produce no unexpected fraction → `metric_value` is NULL.

2. **Tier derivation** (ordered thresholds, any unset skipped = +∞):
   - `metric ≥ critical_threshold` → `critical`
   - `metric ≥ fail_threshold` → `fail`
   - `metric ≥ warn_threshold` → `warn`
   - else → `pass`

3. **Thresholds override GX success.** When a check carries thresholds, they are the user's explicit severity policy and they alone decide the tier. A check with `mostly=0.99` that GX *fails* at 0.5 % unexpected resolves to `pass` if the user's `warn` is 1 % — their stated tolerance wins, and the stored `metric_value` (0.5) keeps it transparent.

4. **Binary fallback (ADR 0005).** No thresholds set, **or** thresholds set but no bandable metric (aggregate checks) → `pass`/`fail` from GX `success`.

5. **`duration_ms` stays NULL in v1** — per-check timing isn't separable from GX's single suite-level `validate()`; it's a reserved seam (ADR 0012, post-v1 Theme E).

The derivation + extraction live in one pure module (`services/severity.py`), so the semantics — and any future change to them — are localized to one place.

## Consequences

**Positive**
- Covers the column completeness / uniqueness / validity majority with a monotonic, intuitive policy ("warn at 1 %, fail at 5 %, critical at 20 %").
- `metric_value` is a genuine SQL-aggregatable DQ KPI (mean unexpected-%), and the model generalizes to v1.x freshness/volume monitors (also "higher = worse" scalars).
- Thresholds-as-policy gives users one obvious knob; GX's `mostly` becomes an implementation detail rather than a second, conflicting severity source.

**Negative**
- A GX `success=False` can resolve to `pass` under a lenient threshold. Accepted and made transparent by the persisted `metric_value`; it is the user's stated policy.
- Aggregate-scalar checks can't be tier-graded in v1 (no unexpected fraction) → binary only. Accepted; raw-value banding is approach B below.

## Reversibility (A → B later)

**Approach B** would band the *raw observed value* (row count, mean, …) instead of the unexpected-%. The switch is **bounded and non-destructive**, because the full raw `observed_value` JSONB is retained on every result regardless of approach:

- *Forward:* add a nullable `checks.direction` column (higher-worse / lower-worse) — an **additive, backward-compatible** migration — and change the one `severity` module. No two-step, no destructive change.
- *History:* `metric_value`'s meaning would differ across the cutover (unexpected-% before, raw scalar after). Resolve by **backfilling** `metric_value` from the retained `observed_value` (possible — nothing was discarded) or segmenting trends at the cutover date. The historical **tier** is *not* rewritten — ADR 0005 already declares it immutable, so that's an accepted invariant, not a switching penalty.

So A is the low-risk default that keeps B cheaply reachable — the same "keep the option open at near-zero cost" posture as ADR 0010 / 0013.

## Alternatives considered

- **Approach B (band the raw observed value) now** — rejected for v1: needs a per-check comparison-direction field (extra migration + scope) and is ambiguous for the dominant column-check case, for value that lands later. Reachable cheaply later (above).
- **Tier escalates GX failure only (never downgrades a GX fail to pass)** — rejected: re-introduces two conflicting severity sources (GX `mostly` *and* thresholds) and surprises users who set an explicit tolerance. Thresholds-as-sole-policy is simpler and predictable.
- **`metric_value` = unexpected fraction 0–1 instead of percent 0–100** — rejected: percent matches how users express tolerances ("1 %"); purely cosmetic, and the column is unit-agnostic anyway.

## Related

- [ADR 0005](0005-severity-tier-weights.md) — tiers, weights, health score, binary fallback (this ADR is its derivation algorithm).
- [ADR 0012](0012-monitor-kind-seam.md) — `metric_value` / `duration_ms`; the scalar this bands.
- `services/severity.py` (`extract_metric`, `derive_status`), `services/run_service.py` (wiring).
- CLAUDE.md §9 (decision table).
