# ADR 0004 — Unified `OrchestrationProvider` abstraction for ADF and Apache Airflow

- **Status:** Accepted
- **Date:** 2026-05-24
- **Deciders:** @TheurgicDuke771

## Context

DataQ integrates with Azure Data Factory (ADF) and Apache Airflow. The original product framing listed ADF as a "5th datasource type," which conflates orchestration with data storage:

- **Datasources** (Snowflake, ADLS Gen2, S3, Unity Catalog) are stores against which DQ checks are written.
- **Orchestrators** (ADF, Airflow) are workflow engines whose pipeline/DAG runs we observe and react to. We do not write DQ checks against the orchestrator itself.

Treating orchestrators as datasources pollutes the suite/check editor, mixes unrelated concerns in the data model, and makes the upcoming Airflow integration look like a one-off rather than a sibling of ADF.

## Decision

**Treat ADF and Apache Airflow as orchestration integrations behind a single `OrchestrationProvider` abstraction. They are NOT datasources and never appear in the connection editor / check editor / suite model as a queryable target.**

### Responsibilities (identical across both providers)

1. **Monitor** pipeline/DAG runs → stored in `pipeline_runs` table with a `provider` column (`adf` | `airflow`) and `provider_run_id`.
2. **Detect failure** in near-real-time via provider-specific webhook channels (see table below).
3. **Trigger suite execution on successful completion** via `trigger_bindings`. The table has a surrogate primary key (`id`) and a non-unique composite **lookup index** on (`provider`, `pipeline_or_dag_id`, `env`) — one pipeline/DAG can map to many suites (one row per binding). Failure events alert the user but do NOT trigger suite runs (no point running checks on data that wasn't loaded).

### Interface

```
from collections.abc import Mapping

class OrchestrationProvider:
    def parse_event(self, payload: bytes, headers: Mapping[str, str]) -> RunUpdate: ...
    def fetch_run_detail(self, provider_run_id: str) -> RunDetail: ...
    def list_recent_runs(self, since: datetime) -> list[RunUpdate]: ...
```

Both `AdfProvider` and `AirflowProvider` implement this interface. Service code routes by `provider` enum, never by provider-specific branching.

**Retry/backoff is an implementation detail**, not part of this interface. The polling fallback (`list_recent_runs`) may retry with backoff on transient provider-API errors; the interface guarantees no double-emit because ingestion is idempotent on (`provider`, `provider_run_id`) plus the run's `last_updated_at` — a re-delivered or re-polled event for an already-current run is a no-op upsert, not a duplicate `pipeline_runs` row.

### Event channels per provider

| Provider | Failure / success channel | Auth | Polling fallback |
|---|---|---|---|
| ADF | Azure Monitor alert rule → webhook `POST /api/v1/orchestration/events/adf` | Shared-secret header (Azure Monitor's only auth mode) | ADF REST API every 10 min |
| Airflow | DAG `on_success_callback` / `on_failure_callback` → webhook `POST /api/v1/orchestration/events/airflow` | HMAC-signed payload (signing key in Key Vault) | Airflow REST API `dagRuns` every 10 min |

### Single-orchestrator-per-`(provider, env)` assumption

`trigger_bindings` is keyed by (`provider`, `pipeline_or_dag_id`, `env`) → `suite_id`. This composite key **cannot disambiguate two instances of the same provider in the same environment** — e.g. two separate `airflow` deployments both serving `env=dev`. The agreed v1 shape therefore assumes **exactly one orchestrator instance per (`provider`, `env`) pair**.

**Consequence:** to keep the binding lookup unambiguous, one of two things must hold:

1. Connection-level CRUD enforces **uniqueness on (`type`, `env`) for orchestrator-typed connections** (an `adf`/`airflow` connection is singular per env), OR
2. `trigger_bindings` grows a `connection_id` column so a binding names a specific orchestrator instance.

**v1 decision:** take option 1 — document the assumption (this section) and **enforce `(type, env)` uniqueness at the connection-CRUD layer in Week 2** for orchestrator-typed connections. The schema bump (option 2) is **deferred to v1.1** and only revisited if multi-instance-per-env becomes a real requirement. Without this guard, a future contributor adding a second same-env Airflow connection would hit a silent binding collision rather than a validation error.

### UI surface

Orchestrators appear ONLY in:
- The orchestration status panel on the results dashboard.
- The trigger-binding configuration UI.

They do NOT appear in the connection editor's "pick a datasource" list or the check editor's table picker.

## Consequences

**Positive**
- Adding a third orchestration provider in the future (Prefect, Dagster, etc.) is a new `OrchestrationProvider` implementation — no data-model or UI churn.
- `pipeline_runs` stays clean as a single events stream regardless of provider.
- `trigger_bindings` is provider-agnostic from day one.
- Forces correct mental model — orchestrator is not a datasource.

**Negative**
- Slightly more abstraction upfront in Week 2 vs hardcoding ADF, then refactoring when Airflow is added. Accepted because Airflow is in-scope for v1, not a hypothetical future.
- Airflow callback adoption is voluntary on the user side (we cannot mutate their DAGs). Mitigated by documenting the polling fallback and providing a copy-paste `dataq_airflow_callback` snippet.

## Alternatives considered

- **Treat ADF as a 5th datasource (original framing)** — rejected. Conflates orchestration with storage; pollutes the check editor and the data model.
- **Hardcode ADF in v1, abstract later when Airflow is added** — rejected. Refactoring after `pipeline_runs` has data is more expensive than designing the abstraction once upfront.

## Related

- `pipeline_runs` and `trigger_bindings` schema lands in Week 1/2.
- `OrchestrationProvider` interface lands in Week 2 alongside both implementations.
- The single-orchestrator-per-`(provider, env)` assumption (above) was surfaced in [PR #41](https://github.com/TheurgicDuke771/DataQ/pull/41) review and tracked in [#72](https://github.com/TheurgicDuke771/DataQ/issues/72); the `(type, env)` uniqueness guard is a Week 2 connection-CRUD requirement.
- Future: ADR 0006 (ADF webhook auth) and ADR 0007 (Airflow callback model) will detail the provider-specific event channels.
