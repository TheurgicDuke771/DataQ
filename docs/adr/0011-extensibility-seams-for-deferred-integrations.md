# ADR 0011 — Extensibility seams for deferred connectors and integrations

- **Status:** Accepted
- **Date:** 2026-05-29
- **Deciders:** @TheurgicDuke771

## Context

Three forward-looking expansion questions were raised against the v1 architecture:

1. **More datasources** — additional RDBMS (MS-SQL, BigQuery) and non-RDBMS / streaming (Kafka) beyond the four v1 types.
2. **Test-management integration** — push DQ results to JIRA / TestRail / Xray after a suite run.
3. **dbt** — where it fits in the model.

None of these is in v1 scope (v1 is the four named datasources + two orchestration providers). The question is *timing*: take them up now, or after v1 — and how to avoid an expensive retrofit either way. The recurring answer is the same shape as ADR 0010: **build the seam in v1 where v1 work already touches it, defer the second implementation to post-v1.**

## Decision

**Defer all three builds to after v1. Shape the v1 work that already touches each area as a generic seam, so the later add is a small additive change rather than a retrofit.**

### 1. Datasource extensibility — generic runner dispatch (build the seam in v1)

- The `CheckRunner` Protocol (`datasources/base.py`) already isolates per-adapter logic. The gap is the worker: `worker/tasks.py` currently hardwires the Snowflake runner.
- **v1 action:** the `run_suite` dispatch must select the `CheckRunner` by `connection.type` (generic), not special-case Snowflake. This is required anyway to land the v1 flat-file and Unity Catalog run paths (Week 5).
- **Post-v1:** a new RDBMS datasource (MS-SQL, BigQuery, Postgres) is then a new `CheckRunner` implementation + a `CONNECTION_TYPES` enum value — a day or two each, no core changes.
- **Out of scope / not this seam:** **streaming (Kafka) is not "just another adapter."** GX is batch-only (ADR 0003); Kafka needs a *second DQ engine*, which is the DQX v1.1 decision (ADR 0003), not a connector add. Tracked there, not here.

### 2. Result publishing — `ResultPublisher` seam (build the seam in v1)

- v1 already ships an **outbound MS Teams alert** path (Week 6). Rather than hardcode Teams, build it behind a small `ResultPublisher` interface invoked at the run-completion point (after `run_service.execute_run`).
- **PII policy baked in from day one:** `results.sample_failures` may contain sensitive rows (it is exactly what the logger redacts — `core/logging.py`). Any publisher that exports results outside DataQ's trust boundary must apply a redaction / opt-in policy. This applies to the v1 Teams publisher too.
- **Post-v1:** JIRA / TestRail / Xray become additional `ResultPublisher` subscribers — no re-plumbing. Natural mapping: suite → test run/cycle, check → test case, passed/failed/skipped → test status; `observed_value` / `expected_value` populate the result body.

### 3. dbt — an orchestration provider, not a datasource or a second engine

- When/if added, dbt slots behind the existing `OrchestrationProvider` abstraction (ADR 0004) as a **third provider**: a `dbt build`/`dbt test` completion event hits `/api/v1/orchestration/events/dbt`, lands in `pipeline_runs`, and fires the matching `trigger_binding`. dbt Cloud has webhooks; dbt Core can POST from an `on-run-end` hook.
- dbt is **not** a datasource (do not add it to the connection/check/suite model — same anti-pattern ADR 0004 guards against) and **not** a second DQ engine for v1.
- **Timing:** depends on the `OrchestrationProvider` interface + events router, which are Week 2 work and do not exist yet. dbt rides on top once ADF + Airflow have proven the interface — realistically post-v1. No v1 action beyond keeping `OrchestrationProvider` provider-agnostic (already an ADR 0004 rule).

## Consequences

**Positive**
- Each deferred feature becomes a cheap additive change (new `CheckRunner`, new `ResultPublisher`, new `OrchestrationProvider`) instead of a retrofit, because the seam is built during v1 work we are doing regardless.
- No v1 scope creep — zero new v1 features, only "build the seam generically rather than as a one-off."
- PII handling for result export is settled once, at the seam, not per-integration.

**Negative**
- Slightly more deliberate design in three v1 touch-points (worker dispatch, Teams notifier, orchestration interface) than the quickest possible hardcoded version. Accepted — the generic form is barely more code and each is touched in v1 anyway.

## Alternatives considered

- **Ingest dbt's own native test results as DQ results** — rejected for v1. Collides with GX-only (ADR 0003): it introduces a second result shape and second check semantics, the exact thing the single-result-schema decision avoids. Revisit as a generic "external DQ result ingestion" feature later (shares machinery with the result-publishing seam).
- **Treat Kafka as just another `CheckRunner` adapter** — rejected. A batch engine cannot do streaming DQ; this is the DQX engine decision (ADR 0003), not a connector.
- **Build JIRA/TestRail integration during v1** — rejected. Net-new, on no roadmap, unblocks nothing in v1. Deferred, with only the `ResultPublisher` seam built now.

## Related

- ADR 0003 — GX-only for v1 (streaming/DQX boundary; single result schema).
- ADR 0004 — `OrchestrationProvider` abstraction (dbt as a third provider).
- ADR 0010 — provider-agnostic infrastructure seams (the infra-side counterpart).
- `datasources/base.py` (`CheckRunner`), `worker/tasks.py` (dispatch), `services/run_service.py` (completion hook point).
- v1 action items tracked in `docs/progress.md` (Week 5 generic dispatch, Week 6 `ResultPublisher` seam).
