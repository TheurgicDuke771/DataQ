# ADR 0046 — Pipeline DQ gate: an orchestrator asks DataQ whether to continue

- **Status:** Accepted (2026-09-29)
- **Date:** 2026-09-29
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0005](0005-severity-tier-weights.md) / [0016](0016-severity-derivation-semantics.md) (severity ladder), [0026](0026-auth-api-keys-and-principal-seam.md) (PATs), [0027](0027-suite-permission-model-workspace-admin.md) (per-suite `view`/`edit`), [0035](0035-request-rate-limiting.md) (rate limits); ADR [0004](0004-orchestration-abstraction.md) (the `OrchestrationProvider` contract this extends).

## Context

Orchestration providers (ADF, Airflow, dbt) have exactly three responsibilities in DataQ:
**monitor** runs, **detect failure**, and **trigger** bound suites when a pipeline run
**succeeds**. None of them lets a pipeline act on a DQ verdict. The proven feature in this
market is a *circuit breaker*: a pipeline stage asks "did the data pass?" and halts its own
downstream stages on a breach.

A read-only "did the suite triggered for my run pass?" endpoint would not work for the
common case. Suites are triggered when a pipeline run **completes**, so a stage in the
**middle** of a run would be waiting for a trigger that cannot fire until the run it is
blocking finishes.

## Decision

1. **A fourth responsibility: answer a gate request.** DataQ still never mutates a pipeline.
   The customer's DAG or pipeline chooses to call DataQ and to wait or fail on the answer.
   The gate is provider-agnostic: it is keyed by the existing `trigger_bindings`
   (`provider`, `pipeline_or_dag_id`, `env`) and has no provider-specific branch.
2. **One endpoint, two modes:** `POST /api/v1/orchestration/gate` with
   `provider`, `pipeline_or_dag_id`, `env`, `provider_run_id`, `fail_on`
   (`warn` | `fail` | `critical`, default `fail`) and `trigger` (default `true`).
   - **Trigger-and-wait** (`trigger: true`): DataQ ensures one run of every enabled bound
     suite exists for this pipeline run and returns its current state. The run carries the
     same `triggered_by` marker the success-event path uses
     (`<provider>:<pipeline_or_dag_id>:<provider_run_id>`), so the existing partial unique
     index makes it **idempotent**: repeated polls, and the run's own later success event,
     never start a second run. Polling the same request until a terminal state is the
     intended client loop.
   - **Status only** (`trigger: false`): DataQ creates nothing. It reports on runs that
     already exist for the marker, e.g. a downstream pipeline gating on an upstream run
     that the normal success event triggered.
3. **States, never a bare boolean:** `awaiting_trigger` (status-only, no run yet),
   `running` (any bound run queued or running), `passed`, `failed` (some result at or above
   `fail_on`), `error` (a run failed operationally or was cancelled, or a check could not be
   evaluated). The response lists each bound suite's run, status, worst severity and counts,
   plus a `retry_after_seconds` hint. `error` is distinct from `failed` so a client can
   choose; the shipped snippets **fail closed** on both.
4. **Authorization is the manual-trigger rule.** Starting a suite run is `edit` on the suite
   (as `POST /suites/{id}/run`), so trigger mode requires `edit` on every bound suite and
   status-only requires `view`. Callers use a PAT (`dq_live_…`). A binding with no enabled
   suites the caller can see is a 404 with no leak of which suites exist.
5. **Rate-limited** by the existing per-user API class. A gate poll is cheap once the run
   exists (the idempotent insert is a no-op), and the client honours `retry_after_seconds`.
6. **Clients:** a `dataq gate` command in the Python client/CLI (the universal client: any
   shell step, and dbt between `dbt build` invocations), an Airflow sensor snippet in
   `integrations/airflow/`, and an ADF pipeline snippet (Web activity inside an Until loop)
   in `integrations/adf/`. All exit or fail non-zero on `failed` and `error`, and time out
   on the client side.

## Consequences

- CLAUDE.md §4's contract becomes four responsibilities; the anti-patterns are unchanged,
  since the gate reads bindings and runs through generic code.
- No schema change: runs, results and the dedup index already carry everything the gate
  needs.
- Not in scope: MCP exposure (the gate is machine-to-machine), and DataQ pausing or failing a
  pipeline itself.
