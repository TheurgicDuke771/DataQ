# ADR 0005 — Severity tier weights (warn / fail / critical → health score)

- **Status:** Accepted
- **Date:** 2026-05-30
- **Deciders:** @TheurgicDuke771

## Context

v1 checks are not binary pass/fail. A check can carry up to three optional
thresholds (`warn_threshold`, `fail_threshold`, `critical_threshold`), and a run
result lands in one of four states: `pass`, `warn`, `fail`, `critical`. The
dashboard (Week 6) rolls these per-result states up into a single **health
score** stat card + 7-day trend, and alert routing (Week 6) keys off the same
tiers (`warn` quiet, `fail` standard, `critical` @channel).

This decision must land **before the Week-3 threshold migration** because two
things bake into schema and stored data:

1. The `status` value set (`pass | warn | fail | critical`) becomes a CHECK
   constraint on `results`.
2. The **weights** that map a tier to a health-score penalty determine how
   historical results aggregate. Once results are written and trends are drawn,
   re-weighting silently rewrites the meaning of every past data point. The
   roadmap calls this out explicitly: *"This affects the DB schema for run
   results and cannot be changed cheaply after data is written."*

The open question this ADR closes: **what are the weights, and what is the
health-score formula** — so the Week-6 dashboard and the Week-8 result-service
tests have a fixed target, and the Week-3 migration writes the right CHECK.

## Decision

**Adopt four ordered severity tiers with fixed penalty weights, and a single
normalised health-score formula that is SQL-aggregatable.**

### Tiers and weights

| Status | Penalty weight | Meaning | Alert behaviour (Week 6) |
|---|---|---|---|
| `pass` | `0.0` | check met its expectation | none |
| `warn` | `0.5` | soft threshold breached — flag only | quiet (no @mention) |
| `fail` | `1.0` | check failed | standard alert |
| `critical` | `2.0` | failed past the critical threshold | urgent (@channel) |

Weights are **not** stored per-result. Only the `status` string is persisted;
the weight is a fixed lookup applied at aggregation time. This keeps the weights
re-tunable in code *for forward-looking display* without a data migration — but
the **tier a result was assigned** is immutable history (it is what the
thresholds evaluated to at run time).

### Health-score formula

For any set of N check results in scope (a suite, an env, a time window):

```
penalty(status) = {pass:0.0, warn:0.5, fail:1.0, critical:2.0}

health_score = 100 × (1 − Σ penalty(statusᵢ) / (N × W_max))   where W_max = 2.0
```

- Range is a clean `[0, 100]`: all-`pass` → 100, all-`critical` → 0,
  all-`fail` → 50, all-`warn` → 75.
- `W_max = 2.0` (the `critical` weight) is the normaliser, so a suite that is
  entirely `fail` still scores above the floor — `critical` is meaningfully
  worse than `fail`, not collapsed to the same 0.
- It is a pure `SUM(...)/COUNT(*)` over the persisted `status` column → computes
  in one SQL pass for any filter (env / datasource / suite / date range), no
  per-check application code. This is the same aggregation discipline as the
  `metric_value` numeric column (ADR 0012): the dashboard never reduces JSONB
  in Python.

### Binary fallback

A check with no tier thresholds set behaves as plain pass/fail: it resolves to
`pass` (penalty 0) or `fail` (penalty 1.0). `warn` and `critical` are simply
never assigned. The formula is unchanged — binary checks are the N-tier formula
with two of four tiers unused.

## Consequences

**Positive**
- Week-3 migration writes a settled `status` CHECK (`pass | warn | fail |
  critical`); no re-migration when the dashboard lands.
- Week-6 health score and Week-8 result-service tests have an exact, testable
  target (e.g. `{fail, fail, pass, pass}` → 75.0).
- Weights live in one constant, aggregation is one SQL expression — severity-aware
  alert routing reads the same `status` column.

**Negative**
- Weights (0.5 / 1.0 / 2.0) are a judgement call, not derived from data. Accepted:
  they are display-time weights, re-tunable in code without touching stored
  history; only the *tier assignment* is immutable.
- A single global health score can mask a single catastrophic table inside an
  otherwise-green suite. Mitigated by the per-suite / per-check breakdowns the
  dashboard renders alongside the headline number (Week 6).

## Alternatives considered

- **Normalise by `W_max = 1.0` (the `fail` weight), clamp at 0.** All-`fail` → 0,
  all-`critical` → 0 (clamped). Rejected: collapses the fail/critical distinction
  at the aggregate floor, which is exactly the signal critical exists to carry.
- **Store the weight on each result row.** Rejected: bloats `results`, and makes
  a weight change a data migration instead of a constant edit, while gaining
  nothing the status string + lookup doesn't already give.
- **Binary pass/fail only for v1, tiers later.** Rejected: tiers are a Week-3
  roadmap deliverable and the threshold columns ride the one-shot Week-3 schema
  migration (ADR 0012). Adding `warn`/`critical` to the `status` CHECK later is a
  second backward-compat two-step — the precise retrofit this ADR exists to avoid.
- **Continuous severity (0.0–1.0 float per result) instead of four tiers.**
  Rejected for v1: no UI affordance to author a continuous severity, and alert
  routing needs discrete buckets anyway. The numeric `metric_value` (ADR 0012)
  already preserves the underlying scalar for anyone who wants finer analysis.

## Related

- ADR 0012 — monitor-kind seam (`check.kind` + `metric_value` / `duration_ms`);
  **rides the same Week-3 migration** as the threshold/status columns.
- `docs/progress-v1.md` — Week 3 "Severity threshold tiers" tasks; Week 6 health
  score + severity-aware alert routing; Week 8 result-service tests.
- `context/DataQ_platform_roadmap.md` — Week 3 severity tiers; post-v1 Theme A
  (auto-monitors emit the same tiers over `metric_value`).
- CLAUDE.md §9 (decision table), §5 (monitor-kind seam).
