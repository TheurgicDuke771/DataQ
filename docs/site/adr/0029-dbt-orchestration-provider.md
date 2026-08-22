# ADR 0029 — dbt as a third OrchestrationProvider (artifact-poll + HMAC webhook)

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** solo-dev
- **Related:** [0004](0004-orchestration-abstraction.md) (orchestration seam), [0007](0007-airflow-callback-model.md) (the sibling callback model this mirrors), [0011](0011-extensibility-seams-for-deferred-integrations.md) (second-impl-deferred seams), [0010](0010-provider-agnostic-infrastructure-seams.md)/[0013](0013-marketplace-distribution-and-anti-lock-in.md) (anti-lock-in). Issue #611 (split from #609).

## Context

ADR 0004 established one `OrchestrationProvider` seam with ADF as the reference impl and Airflow as the second. dbt is a **third** orchestration layer — a genuine second-generation test of the seam (not dbt clubbed under the already-proven Airflow provider). The harness dbt project (#609) produces the runs to observe.

The problem: **dbt Core has no runs API.** dbt Cloud's free tier has no API/scheduler; dbt-on-Snowflake observes via Snowflake (dies with the trial); Databricks dbt tasks observe via the Jobs API. Binding to any of those couples DataQ to a host. What *every* dbt deployment produces, wherever it runs, is **artifacts** (`run_results.json`, `manifest.json`) and the ability to run a command after a build. So the provider must bind to that universal surface — neutrality by construction (ADR 0010/0013).

## Decision

Add a `dbt` provider that mirrors the Airflow callback model (ADR 0007) — HMAC-signed push webhook + a poll fallback — with the poll reading dbt **artifacts** instead of a REST API.

### Provider mapping & run grain

| Field | Value |
|---|---|
| `provider` | `dbt` |
| `resource_config_key` | `project_name` — resolves the run to a dbt connection (as `base_url` does for Airflow) |
| `pipeline_or_dag_id` | **job name** — the fine-grained trigger unit within a project (as `dag_id` is within an Airflow instance) |
| `provider_run_id` | dbt `metadata.invocation_id` (the `pipeline_runs` upsert idempotency key) |
| `status` | `succeeded` unless any node result status ∈ {`error`, `fail`, `runtime error`} → `failed` |

**Job-level grain (not project-level):** a dbt project can expose multiple logical jobs (e.g. distinct `--select` slices, or full-build vs incremental). The connection is the *project* (one artifacts deployment); a **job** is the pipeline_or_dag_id, so `trigger_bindings(provider='dbt', pipeline_or_dag_id=<job>, env)` binds a specific job's success to a suite. This is the direct analog of Airflow's instance→DAG structure and keeps `trigger_bindings` unchanged (provider-agnostic composite key). The operator labels the job via `DATAQ_DBT_JOB` (callback env) and lays artifacts out per job.

### Authentication (mirrors ADR 0007 exactly)

- **Push:** `POST /api/v1/orchestration/events/dbt`, HMAC-SHA256 over the **raw body** in `X-DataQ-Signature`, constant-time compared to an **app-level** signing key (`settings.dbt_webhook_secret_name` in the SecretStore — one key shared across dbt connections, exactly like `airflow_webhook_secret_name`). Uniform 401 for missing/invalid signature; 422 for a well-signed but malformed body. The signed callback is **authoritative** — `fetch_run_detail` is intentionally unimplemented (no enrichment), as for Airflow.
- **Poll credential:** the **per-connection** SecretStore secret is the *artifacts-store read credential* (ADLS SAS / S3 secret key / none for local) — the analog of Airflow's per-connection REST token. Distinct from the app-level signing key.

### Artifacts poll + location schemes

`list_recent_runs` reads `<artifacts_uri>/<job>/latest/run_results.json` for each configured job, maps it to one `RunUpdate`, and emits it when `metadata.generated_at >= since`. Rides the existing 10-min beat + gap-recovery sweep (`dbt` joins `ORCHESTRATION_PROVIDERS`); the `(provider, invocation_id)` upsert makes re-reading `latest/` idempotent (no double-fire). A stable `latest/` pointer plus timestamped `runs/<UTC-ts>/` copies are written by the producer (`upload_artifacts.py`).

Three host-neutral schemes behind one reader seam (reusing DataQ's existing ADLS/S3 auth shapes):

| Scheme | Reader | Credential |
|---|---|---|
| `adls://<account>/<container>/<prefix>` | `BlobServiceClient` (as `datasources/adls.py`) | SAS token (per-connection secret) |
| `s3://<bucket>/<prefix>` | `boto3` (as `datasources/s3.py`) | access-key-id (config) + secret key (secret) |
| `file:///<path>` | local filesystem | none (post-wind-down local compose) |

### Trigger dedup index

The partial unique index `uq_runs_suite_triggered_by` (#308) and its Python predicate mirror `_ORCH_TRIGGER_PREDICATE` currently cover `adf:%`/`airflow:%`. A migration **widens** both to add `dbt:%`. Backward-compatible (widening a partial index adds coverage; existing rows unaffected; no dbt markers exist yet). Reviewed by the migration-safety agent.

### Replay / freshness

Not enforced in v1, per ADR 0007 — the `(provider, invocation_id)` idempotency + trigger-marker dedup make a replayed delivery a no-op rather than a double-trigger. A stale re-POST of an old `invocation_id` lands on the existing row.

## Consequences

- **Positive:** the seam takes a third, structurally different provider (artifact-based, no REST API) with zero provider branching in service code — the strongest evidence yet that ADR 0004/0011 hold. Host-agnostic: the same contract works under dbt Cloud, dbt-on-Snowflake, Databricks dbt tasks, and local compose. `integrations/dbt/` ships the callback snippet like `integrations/airflow/`.
- **Negative / cost:** a new connection type (`dbt`, orchestration category — NOT a datasource, CLAUDE.md §4) + a migration + three storage-reader auth paths (only ADLS live-verified this cycle; S3/file exercised by unit fixtures). The job-name label is operator-supplied (dbt has no native job identity) — a documentation burden in the snippet.
- **Neutral:** dbt monitoring shares `pipeline_runs`/`trigger_bindings`/the 10-min beat with ADF/Airflow; no new tables.

## Alternatives considered

- **dbt under the Airflow provider** — rejected (user, 2026-07-04): Airflow's paths are already proven; nothing new tested. dbt deserves to exercise the seam independently.
- **Bind to a host API** (dbt Cloud / Snowflake tasks / Databricks Jobs) — rejected: each couples observation to one vendor, violating ADR 0010/0013. The artifact contract is the only universal surface.
- **Project-level grain** (one pipeline per project) — rejected (user): job-level matches Airflow's DAG granularity and lets one project bind multiple suites.

## Related

- Issue #611; depends on #609 (the dbt project producing runs).
- Sibling ADRs 0006 (ADF webhook), 0007 (Airflow callback).
