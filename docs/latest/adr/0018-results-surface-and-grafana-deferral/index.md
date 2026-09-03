# ADR 0018 — Results surface is an in-app page; Grafana deferred to optional ops add-on

- **Status:** Accepted
- **Date:** 2026-06-11
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0005](0005-severity-tier-weights.md) / [0016](0016-severity-derivation-semantics.md) (severity), [0012](0012-monitor-kind-seam.md) (`metric_value`/`duration_ms` seam), [0011](0011-extensibility-seams-for-deferred-integrations.md) (`ResultPublisher` redaction seam)

## Context

DataQ now persists DQ run outcomes — `runs` + `results` (severity tier, `metric_value`, `observed/expected`, `sample_failures`) and orchestration `pipeline_runs`. Users need to **see** them: which suites ran, pass/fail/severity, per-check detail, trends, and the orchestration monitoring feed. Two shapes were on the table:

1. **In-app React page** backed by the DataQ REST API (`runs.py` read endpoints shipped in PR-C0b — `GET /runs`, `GET /runs/{id}`, `GET /pipeline_runs`).
2. **Grafana** (or any BI tool) pointed straight at PostgreSQL.

Grafana is attractive — free panels, alerting, ad-hoc SQL — and the `metric_value`/`duration_ms` columns were deliberately built SQL-aggregatable (ADR 0012) partly with dashboards in mind. But pointing a dashboard at the database **bypasses two boundaries that only exist in the application layer**:

- **Per-suite sharing (authz).** Suite visibility is "owner or `shares` row" (`suite_authz`). A raw DB connection has no concept of the requesting user, so a Grafana viewer sees **every** suite's results regardless of who they're shared with. The whole sharing model — the v1 access story — evaporates.
- **PII redaction.** `results.sample_failures` holds raw failing rows, which can contain sensitive data (CLAUDE.md: redaction is the logger's job; `gx_runner` treats sample keys as redactor-only). A direct DB read serves them unredacted. The API layer is where row-level redaction / opt-in policy can live (the same trust-boundary concern ADR 0011 flags for `ResultPublisher`).

## Decision

**The primary results surface is an in-app React page (`/results`) backed by the DataQ API.** It is the only surface that enforces suite-level sharing and can apply PII redaction, so it is the one users get.

**Grafana is deferred to an optional, post-v1 *ops* add-on** — a read-only operational dashboard for an operator/admin persona (fleet health, run volumes, latency), provisioned with its own read-only reporting DB role and explicit grants, **never** the per-user product surface and never a substitute for the authz/redaction the API owns. It is additive: nothing in v1 depends on it, and it can be added later without schema change (the aggregatable columns are already there).

Concretely for v1 (PR-C1): a runs table (suite, status, timing, duration) with status filtering, a run-detail view with per-check results (severity tag, `metric_value`, observed/expected), and a `pipeline_runs` monitoring tab — all through `runs.py`, all suite-scoped. `sample_failures` stays **withheld** from the API until row-level redaction lands (tracked separately); the page shows the redaction-safe summary fields first.

## Consequences

**Positive**
- Suite-level sharing holds on the results surface — a user sees results only for suites they own or are shared on, identical to suites/checks.
- PII redaction has a home (the API boundary); raw sample rows never leave the trust boundary unredacted.
- Domain-native UX (Connections / Suites / Checks / **Results**) consistent with the rest of the app; deep-linkable, auth-gated, same error envelope.
- The SQL-aggregatable `metric_value`/`duration_ms` seam still pays off — for both the in-app trend charts (Week 6) and a future Grafana ops board.

**Negative / watch**
- We build + maintain the results UI rather than getting Grafana's panels for free. Accepted: the authz/PII cost of the DB-direct shortcut is not payable in v1.
- Rich ad-hoc exploration (arbitrary SQL, custom panels) isn't available in-app v1; that's exactly the gap the optional ops Grafana add-on fills later for the operator persona.
- Exposing redacted `sample_failures` on the drill-down is a follow-up (needs the row-level redaction policy first).

## Validation

PR-C0b's `runs.py` enforces the gate (suite-scoped list + `require_permission(view)` on detail; `pipeline_runs` auth-only) with DB-backed tests proving cross-suite isolation. PR-C1 builds the page on top; the seeded demo runs/results/pipeline-runs exercise it end-to-end (browser E2E).
