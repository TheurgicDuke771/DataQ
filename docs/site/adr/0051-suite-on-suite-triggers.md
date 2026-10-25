# ADR 0051 — Suite-on-suite triggers: a suite's own success as a trigger provider

- **Status:** Accepted (2026-10-25)
- **Date:** 2026-10-25
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0004](0004-orchestration-abstraction.md) (the `OrchestrationProvider` contract), [0011](0011-provider-agnostic-trigger-bindings.md) (`trigger_bindings` as the provider-agnostic trigger key), [0029](0029-dbt-orchestration-provider.md) (the last time the provider value-set widened), [0046](0046-pipeline-dq-gate.md) (the gate, which this does **not** extend), [0027](0027-suite-permission-model-workspace-admin.md) (per-suite `view`/`edit`).

## Context

A suite runs today on a schedule, by hand, or when a bound **orchestration** pipeline or DAG
succeeds. The long-deferred ask is the fourth source: run suite B when suite A has run and passed —
a downstream dataset's checks only make sense once the upstream dataset has been verified, and
two suites bound to the same pipeline run in parallel, not in order.

The reason it was deferred stands: DataQ is not an orchestrator. Sequencing work is the
orchestrator's job (CLAUDE.md §4), and a suite that fans out into a graph of suites is a
mini-DAG DataQ would then have to schedule, retry and visualise. The customer's orchestrator
already does all of that. What it cannot do is express "when **DataQ** says the upstream is
good"; the gate (ADR 0046) covers that for a pipeline stage, but not for a suite that has no
pipeline of its own — a suite on a flat-file landing zone, a reconciliation suite that should
follow the two suites it reconciles.

`trigger_bindings` is `(provider, pipeline_or_dag_id, env) → suite_id` and is already
provider-agnostic; the only provider-specific part of a trigger is who **observes** the
completion. For an orchestrator that is a webhook or a poll; for a suite it is DataQ itself,
at the moment the run reaches its terminal state. Nothing else in the chain —
`insert_triggered_run`, the `triggered_by` marker, the dedup index, dispatch — cares where the
event came from.

## Decision

1. **A suite is a trigger provider, not an orchestration provider.** `provider = 'suite'`
   joins a new `TRIGGER_PROVIDERS` value-set (`adf`, `airflow`, `dbt`, `suite`) that governs
   `trigger_bindings.provider`, binding validation and the `triggered_by` dedup predicate.
   `ORCHESTRATION_PROVIDERS` is **unchanged**: there is no `suite` connection type, no
   `pipeline_runs` row, no poller, no webhook, no health signal, and the gate endpoint
   (ADR 0046) refuses `provider='suite'` — a suite chain has no pipeline stage to answer.
2. **The binding key.** `pipeline_or_dag_id` holds the **upstream suite's id**; `env` must
   equal the upstream suite's connection env and is filled in by the server, so the firing
   lookup stays the one every provider uses: `(provider, pipeline_or_dag_id, env, enabled)`.
3. **When it fires: success only, by default and for now.** A run fires its bindings when it
   reaches `succeeded` with no check at `fail` or `critical` (a `warn` passes, an `error`ed or
   skipped check does not block — the run still verified what it could). A run that `failed`,
   was `cancelled`, or has a breach fires nothing; the downstream suite is not run against
   data the upstream just said was bad. A per-binding `fire_on` predicate is deliberately not
   added until someone asks for it with a case: the "run B on A's failure" shape is an alert,
   which notifications already deliver.
4. **The marker.** The downstream run carries `triggered_by = 'suite:<upstream_suite_id>:<upstream_run_id>'`.
   Both ids are UUIDs, so unlike every orchestrator marker this one **can** be split — but
   readers still go through `orchestration.markers`, and the partial unique index
   `uq_runs_suite_triggered_by` (predicate widened to `suite:%`) makes a re-fired completion
   (a worker retry, a reaper flipping a stuck run) a no-op, exactly as for pipeline events.
5. **Guards, at authoring and at firing.**
   - *Self:* a suite cannot be bound to itself (422).
   - *Cycle:* creating a binding that would make the upstream reachable from the downstream
     through enabled **or disabled** suite bindings is refused (422, naming the path). Disabled
     ones count because `enabled` is a toggle, not a deletion, and re-enabling must not be the
     moment a cycle appears.
   - *Depth:* at firing time a chain deeper than `SUITE_TRIGGER_MAX_DEPTH` (5) stops and logs
     `suite_trigger_depth_exceeded` instead of running — the depth is read back through the
     markers, so a cycle that slipped past authoring (two bindings created in the same instant)
     is bounded as well. Fan-out width is unbounded: one upstream may trigger many suites.
   - *Access:* creating or enabling the binding needs `edit` on the downstream suite (today's
     rule) **and** `view` on the upstream suite — you cannot chain onto a suite you cannot
     see, which would otherwise be an enumeration channel for suite ids.
6. **Where it fires.** In the worker, after the run's terminal commit and the alert dispatch
   (`run_suite` in `worker/tasks.py`): `suite_trigger_service.fire_for_run`, fail-soft like
   the hooks beside it (a broken trigger never fails the run that completed). It reuses
   `insert_triggered_run` + `run_dispatch.dispatch_or_fail`, so the downstream run is queued
   through the same admission and the same broker-down handling as a pipeline-triggered one.
7. **What the user sees.** The Triggers panel offers "Another suite (on success)" beside the
   orchestrators, with a suite picker (suites the user can see) in place of the free-text
   pipeline id and the env filled from the chosen suite. A triggered run's label reads
   "Suite — <upstream suite name>" when the viewer can see that suite, and "Suite" alone when
   they cannot (a run they may read must not leak a suite name they hold no grant on). MCP
   `create_trigger_binding`
   / `list_trigger_bindings` accept and return `provider='suite'`, with the docstrings saying
   what fires it and what never does.

## Consequences

- No migration beyond widening two value-sets: the `trigger_bindings` provider CHECK and the
  `uq_runs_suite_triggered_by` predicate (the same shape as ADR 0029's dbt widening, and
  reversible until the first `suite` binding exists).
- DataQ gains a one-hop dependency, not a DAG: it never retries, reorders or visualises a
  chain, and the depth cap keeps a mistaken loop from becoming a runaway. Anyone who needs a
  real DAG keeps using the orchestrator, which can now **gate** on DataQ (ADR 0046) and bind
  suites to its stages; this ADR is for the suites that have no stage.
- `incident_evidence`'s upstream-pipeline layer stays pipeline-only (`ORCHESTRATION_PROVIDERS`);
  a `suite:` marker yields no pipeline layer, which is correct — there is no pipeline.
- Env near-miss detection (the ambiguous-URL warning on orchestrator bindings) does not apply:
  the env is derived, never typed.
- The key is stored as the canonical `str(uuid)` whatever spelling was sent, since firing and
  the cycle walk compare against that form; and because the key is text rather than a foreign
  key, deleting the upstream suite removes the suite bindings keyed on it — nothing is left
  behind that is enabled and can never fire.
- Suite-binding creates serialise on a transaction-scoped advisory lock, so two concurrent
  creates cannot both pass the cycle check and commit a loop.

## Alternatives considered

- **Document the orchestrator-chaining pattern and build nothing.** Rejected for the suites
  that have no orchestrator stage (landing-zone files, reconciliation suites). The pattern
  is still the recommendation when a stage exists, and the guide says so.
- **Add `suite` to `ORCHESTRATION_PROVIDERS`.** Rejected: eighteen call sites would then
  treat it as a connection type with a poller, a webhook secret, a health signal and a
  `pipeline_runs` history, none of which exist.
- **A `fire_on` predicate per binding (`passed` | `any_terminal`).** Deferred (decision 3).
- **Cycle checks at firing time only.** Rejected: a user should learn about a loop when they
  make it, not when the chain runs at 03:00; firing-time depth remains as the backstop.
