# DataQ v1 — Progress tracker

> Mirrors [context/DataQ_platform_roadmap.md](../context/DataQ_platform_roadmap.md) (the 100-task roadmap) with execution status.
> **Updated at the end of every PR** — the PR template has a checkbox to enforce.
> Source of truth for "what's done vs. what's left." CLAUDE.md §13 carries only the headline.

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Done — PR merged to `main` |
| 🟡 | In progress — open PR or partially shipped |
| ⬜ | Not started |
| 🔵 | Deferred / scope-changed (with note) |

---

## Snapshot

| | |
|---|---|
| **Active since** | 2026-05-24 |
| **Current week** | Week 3 of 8 — Suite & check API (backend) |
| **Roadmap tasks done** | 34 ✅ + 6 🟡 / 155 (~22%) |
| **Out-of-roadmap PRs landed** | 5 bundles (governance, tooling lock, Entire CLI, Dependabot triage round 1, PR-3 cleanup) + ADRs 0005/0006/0007/0012 |
| **Week-1 exit gate** | A logged-in user can hit a FastAPI endpoint that triggers GX against Snowflake DEV and persists a result row. — **met** (plumbing complete via PR 4a–4c; live-Snowflake run fails-soft pending DEV creds — deferred smoke) |
| **Next milestone** | ADF/Airflow polling fallback (`list_recent_runs` + 10-min Celery beat → succeeded-run detection → trigger) + run_suite dispatch wiring once Week-3 target-table lands (Week 5) |
| **Open issues** | 7 (#92 + governance polish #20/#19/#18/#17/#10/#8) |
| **Open PRs** | none |
| **Design gates** | ADR 0005 (severity weights) + ADR 0012 (monitor-kind seam) **both accepted** — Week-3 migration unblocked |

---

## Out-of-roadmap work (foundation that's not in the 100-task list)

These were preconditions for executing the roadmap. Listed for completeness.

| Bundle | PRs | Status |
|---|---|---|
| **PR 0 governance** | #1–#24, #44, #55 — `.gitignore`, CLAUDE.md, CODEOWNERS, ADRs 0001–0004 + 0009, PR / issue templates, architecture diagram, Claude Code agents/skills/hooks/MCP, Entire CLI hooks + allowlist | ✅ |
| **PR 1 tooling lock** | #37 — conda env, Black, Ruff, mypy, pre-commit, CI workflow, Dependabot, `scripts/setup.sh`, frontend tooling | ✅ |
| **PR 2 hygiene follow-ups** | #44 (mermaid + tsconfig + ADR 0009 + CLAUDE.md status), #47 (precommit reorder), #48 (mermaid fix #2), #49 (precommit mypy deps), #52 (markdown linebreak preservation) | ✅ |
| **Architecture Q&A ADRs** | ADR 0010 (provider-agnostic infra seams — cloud portability) + ADR 0011 (extensibility seams — more datasources, `ResultPublisher`, dbt-as-orchestration-provider). Records the now-vs-post-v1 timing per seam; threads v1 action items into W2/W5/W6/W7 above | 🟡 (`claude/dreamy-fermat-mwyqm`) |

---

## Week 1 — Foundation, auth & project scaffold

**Exit gate:** Azure AD auth working, GX + FastAPI connected to Snowflake DEV, Swagger live.

### Setup (4 tasks — 4/4 ✅)
- [x] ✅ Monorepo scaffold — FastAPI backend + React/Vite frontend + Celery + Redis — #37 + [PR 2a](https://github.com/TheurgicDuke771/DataQ/pull/39) + [PR 3c](https://github.com/TheurgicDuke771/DataQ/pull/63)
- [x] ✅ Docker Compose dev environment (all services wired up) — [PR 2a](https://github.com/TheurgicDuke771/DataQ/pull/39)
- [x] ✅ PostgreSQL DB + Alembic migrations baseline — [PR 2c](https://github.com/TheurgicDuke771/DataQ/pull/41)
- [x] ✅ Core data model: connections, suites, checks, runs, results, shares — [PR 2c](https://github.com/TheurgicDuke771/DataQ/pull/41) _(also added `users`, `pipeline_runs`, `trigger_bindings` per ADR 0004)_

### Auth (3 tasks — 1.5/3 ✅, 1.5/3 🟡)
- [x] ✅ Azure AD SSO — MSAL token validation in FastAPI + login flow in React — [PR 3a](https://github.com/TheurgicDuke771/DataQ/pull/53) + [PR 3c](https://github.com/TheurgicDuke771/DataQ/pull/63)
- [ ] 🟡 Session timeout + silent token refresh handling — `acquireTokenSilent` wired in PR 3c interceptor; `InteractionRequiredAuthError` fallback path pending (real-AAD smoke test in Week 7)
- [ ] 🟡 Key Vault integration for all credential storage — abstraction landed in [PR 3b](https://github.com/TheurgicDuke771/DataQ/pull/56); real Azure vault provisioning deferred to Week 7

### GX baseline & API conventions (3 tasks — 2/3 ✅, 1/3 🟡)
- [x] ✅ GX Core wired to Snowflake datasource (DEV env) — `SnowflakeCheckRunner` (GX 1.17) behind the `CheckRunner` seam — [PR 4b](https://github.com/TheurgicDuke771/DataQ/pull/76) + [PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79) _(live run against DEV fails-soft pending creds — deferred smoke)_
- [x] ✅ GX result serialisation to DB (runs, results) — `run_service.execute_run` + NaN→null sanitizer — [PR 4c-i](https://github.com/TheurgicDuke771/DataQ/pull/78) + [PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)
- [ ] 🟡 Configure FastAPI with Pydantic models, route tags, and Swagger (`/docs`) + ReDoc (`/redoc`) — FastAPI + Pydantic wired; `/me` + `/_probe/*` have response models, tags, summaries; formal "disable in prod" gate still pending

**Week 1 total: 7.5 / 10 ✅** _(exit gate met; remaining: silent-token-refresh, real Key Vault, prod-docs gate — all deferred to Week 7)_

---

## Week 2 — Connection manager — all datasource types (backend)

**Exit gate:** All connection types configurable and testable via API; credentials stored in Key Vault.

> **Auth-boundary discipline (per [ADR 0010](adr/0010-provider-agnostic-infrastructure-seams.md)):** new connection-CRUD endpoints (and every protected route from here on) depend on a generic internal "current user" dependency that returns DataQ's own `User` — they must NOT read MSAL token claims directly in route/service code. Cheap now; expensive to retrofit once dozens of endpoints exist. No new abstraction layer required, just the boundary.

### Snowflake & ADF (4 tasks — 4/4)
- [x] ✅ API: CRUD for Snowflake connections (DEV / QA / UAT), connection test endpoint — [PR 5](https://github.com/TheurgicDuke771/DataQ/pull/85) _(also introduced the `ConnectionAdapter` seam + registry per [ADR 0011](adr/0011-extensibility-seams-for-deferred-integrations.md), and `SecretStore.set` write-through — so PRs 6-8 are pure adapter additions)_
- [x] ✅ API: CRUD for ADF connections (subscription ID + service principal) — PR 6 _(`ADFConnectionAdapter` in the new `orchestration/` package — NOT `datasources/`, per CLAUDE.md §4; `test()` does SP token + factory GET via httpx. Enforces `(type, env)` uniqueness for orchestrator rows via a **partial unique index** `WHERE type IN ('adf','airflow')` per [#72](https://github.com/TheurgicDuke771/DataQ/issues/72) / ADR 0004 — datasources excluded, so Snowflake stays many-per-env. CRUD/API reused unchanged: pure adapter + registry + migration addition.)_
- [x] ✅ Connection re-auth endpoint — refresh expired Key Vault token — `POST /connections/{id}/reauth` (`svc.reauth_connection`): rotates the credential through `SecretStore.set` **and** verifies it via the same adapter probe as `/test`, in one step (the gap PATCH+`/test` leave open). Rotation persists before the probe, so a bad new credential surfaces as 502 `connection_test_failed`; a store-write failure is 502 `connection_secret_write_failed` with the old credential untouched. 6 TestClient tests (rotate+verify ok, failed-verify-but-rotation-persists, write-fail 502, 404, secret-required 422). Type-agnostic — applies to all six connection types
- [x] ✅ Review `connections.secret_ref` nullability — decide based on Airflow basic-poll / unauthenticated S3 cases ([PR #41 nit](https://github.com/TheurgicDuke771/DataQ/pull/41)) — **decision: keep nullable.** It's NULL for the transient flush→secret-write window (create), for credential-less auth (managed-identity/IAM-role, W7, ADR 0010/0011), and for unauthenticated sources. v1 types are all secret-bearing, but presence is enforced in the **service layer** (`test_connection` → 502 without a credential), not the schema — so W7 credential-less modes need no later migration. Recorded as a comment on the `secret_ref` column in `db/models.py`

### ADF webhook receiver (Azure Monitor → DQ platform) (5 tasks — 3 ✅ / 1 🟡 / 1 ⬜)
- [x] ✅ `POST /api/v1/orchestration/events/adf` — receive Azure Monitor payload, validate shared secret (constant-time, ADR 0006), return 200 — PR 7 _(unified `OrchestrationProvider` seam landed: `orchestration/base.py` Protocol + `RunUpdate` DTO + provider registry; ADF reference impl per ADR 0004 — service code dispatches by provider, never branches on ADF)_
- [x] ✅ Parse webhook payload — `AdfProvider.parse_event` extracts `factoryName`/`pipelineName`/`runId`/`status`/`firedDateTime` → `RunUpdate`, ADF→`PIPELINE_RUN_STATUSES` normalisation — PR 7 _(exact Common-Alert-Schema field mapping validated at Week-7 deploy smoke)_
- [x] ✅ Follow-up ADF REST API call on webhook receipt — fetch run details — PR 8 _(`AdfProvider.fetch_run_detail` GETs the ARM `pipelineruns/{runId}` for authoritative status/timing/message; `orchestration_service.ingest_event` enriches **best-effort** before upsert — any failure (no creds, transport) falls back to the parsed event so a valid webhook is never dropped)_
- [ ] 🟡 Upsert pipeline run status into `pipeline_runs`; correlate with suite run — idempotent upsert (PR 7) + **trigger-on-success skeleton** (PR 8): a `succeeded` run matching enabled `trigger_bindings` creates queued `Run` rows (`triggered_by="<provider>:<pipeline>:<run_id>"`, idempotent on replay); failures never trigger (ADR 0004). **`run_suite` dispatch is gated** until checks carry a target table (Week 3); `trigger_bindings` CRUD is Week 4/5 (bindings seeded in tests). `list_recent_runs` + 10-min polling beat → Week 5.
- [x] ✅ Shared secret config in Key Vault → `ADF_WEBHOOK_SECRET` env var — `settings.adf_webhook_secret_name` resolved via `SecretStore` (→ `KV_SECRET_ADF_WEBHOOK_SECRET` in dev) — PR 7

### Airflow orchestration (added per ADR 0004; not in original roadmap) (3 tasks — 3/3)
- [x] ✅ `POST /api/v1/orchestration/events/airflow` — `AirflowProvider.parse_event` (signed-callback JSON → `RunUpdate`, state→`PIPELINE_RUN_STATUSES`) + **HMAC-SHA256** auth over the raw body (`X-DataQ-Signature`, [ADR 0007](adr/0007-airflow-callback-model.md)); reuses `ingest_event`. Generalised `_resolve_connection` to match on a provider-declared `resource_config_key` (`base_url` for Airflow, `factory_name` for ADF — no provider branching); enrichment is skipped for Airflow (callback is authoritative). `airflow-webhook-secret` config added
- [x] ✅ Airflow `on_success_callback` / `on_failure_callback` helper snippet for users' DAGs — stdlib-only, fail-safe `dataq_airflow_callback.py` under `integrations/airflow/` (+ setup README): HMAC-signs the raw body → `X-DataQ-Signature` and POSTs `dag_id`/`run_id`/`state`/`base_url` to the receiver. Round-trip tests assert the snippet's signature **and** payload are accepted by `_authenticate_airflow` + `AirflowProvider.parse_event` (producer↔consumer agreement) — completes the Airflow event-receiver loop (callback producer ↔ HMAC receiver)
- [x] ✅ Airflow connection type — webserver URL + token/basic auth (token v1 default) — `AirflowConnectionAdapter` (REST `dagRuns`-probe `test`), one-line registry add; orchestrator `(type,env)` guard already covers it ([ADR 0007](adr/0007-airflow-callback-model.md))

### Flat file — ADLS Gen2 & S3 (4 tasks — 2/4)
- [x] ✅ API: CRUD for ADLS Gen2 connections — `AdlsConnectionAdapter` (account URL + container; SAS auth, container-properties `test` via `azure-storage-blob`), one-line registry add. **SAS only in v1**; `managed_identity` config rejected with a "deferred to Week 7" message (needs an ambient Azure identity + the `secret_ref`-nullability change) — [PR #100](https://github.com/TheurgicDuke771/DataQ/pull/100)
- [x] ✅ API: CRUD for S3 connections — `S3ConnectionAdapter` (bucket + region; access-key auth, `head_bucket` `test` via `boto3`), one-line registry add. **Access-key only in v1**; `iam_role` config rejected with a "deferred to Week 7" message (per the same `secret_ref`-nullability decision)
- [ ] ⬜ GX `pandas_abs` / `pandas_s3` datasource wiring — connect, list containers, list files
- [ ] ⬜ File asset config model: container, batching regex, file format (CSV / Parquet / JSON)

### Unity Catalog / Databricks (3 tasks — 2/3)
- [x] ✅ API: CRUD for Databricks connection — `UnityCatalogConnectionAdapter` (workspace URL + warehouse id + PAT; `SELECT 1` `test` via `databricks-sql-connector`), one-line registry add. PAT-only (secret-bearing, no `secret_ref`-nullability deferral). The `UnityCatalogCheckRunner` (DQX swap-in, CLAUDE.md §5) is the Week-3 run path, not built here
- [ ] ⬜ GX Spark / JDBC datasource wiring for Unity Catalog — connect, list catalogs / schemas / tables
- [x] ✅ UC auth test endpoint — validate PAT + SQL Warehouse reachability — the `SELECT 1` probe in `UnityCatalogConnectionAdapter.test`, surfaced through the generic `POST /connections/{id}/test`

**Week 2 total: 15 / 19** _(ADF webhook receiver: endpoint+auth, payload parse, secret config, REST `fetch_run_detail` enrichment; upsert+correlate 🟡 — trigger-on-success skeleton landed, run_suite dispatch gated to Week 3; polling → Week 5. **Airflow group complete (3/3)** — HMAC receiver + connection adapter + DAG callback snippet, so the producer↔receiver loop is closed end-to-end. All six connection types now have adapters: Snowflake + ADF + Airflow + ADLS Gen2 + S3 + Unity Catalog. Remaining W2 tail: connection re-auth endpoint + `secret_ref` nullability note; the rest are Week-3/5 GX run-path tasks)_

---

## Week 3 — Suite & check API — all datasource types (backend)

**Exit gate:** Full check CRUD API across Snowflake, flat files and Unity Catalog; column profiler live.

### Suite & check backend (4 tasks — 1/4 ✅)
- [x] ✅ API: CRUD for suites and GX expectations (Snowflake path) — **suites** (PR-B1): `suite_service` + `/suites` CRUD (`connection_id` validated then immutable; delete cascades to checks). **checks** (PR-B2): `check_service` + nested `/suites/{id}/checks` CRUD surfacing `kind` + `warn/fail/critical_threshold` + GX `expectation_type`/`config`. v1 monitor-kind guard (only `expectation`; reserved kinds → 422, ADR 0012); checks scoped to their suite (cross-suite access → 404); thresholds are `Decimal` in (exact `Numeric` storage) / `float` out (clean JSON). 24 TestClient tests; all four modules 100%. Share-based access filtering deferred to the suite-sharing task; **DQ-dimension classification** deferred + tracked ([#124](https://github.com/TheurgicDuke771/DataQ/issues/124))
- [ ] ⬜ API: suite sharing — assign users with owner / editor / viewer roles
- [ ] ⬜ API: suite export to JSON + import from JSON
- [ ] ⬜ API: check dry-run endpoint — validate against live data, return preview result

### Severity threshold tiers (warn / fail / critical) (4 tasks — 4/4)
> **Day 1 design decision: severity weights — ✅ settled in [ADR 0005](adr/0005-severity-tier-weights.md) (warn 0.5 / fail 1.0 / critical 2.0; health = 100×(1−Σpenalty/(N×2.0))).**
- [x] ✅ Add `warn_threshold`, `fail_threshold`, `critical_threshold` fields to check model — nullable `Numeric` columns on `Check` (NULL → plain pass/fail) — migration `9c59b6a44f33`
- [x] ✅ Alembic migration — threshold columns + `status` enum (`pass`, `warn`, `fail`, `critical`) **+ the monitor-kind / metric columns below (one migration)** — the one-shot Week-3 schema seam `9c59b6a44f33` (tested up→down→up; `results.status` retargeted from `passed/failed/skipped` with a data-update-before-CHECK-swap; `run_service` now writes `pass`/`fail` binary-fallback per ADR 0005). `alembic check` clean (no model drift)
- [x] ✅ Post-processing in GX result handler — derive `warn` / `fail` / `critical` from observed value (PR-C) — `services/severity.py` (`extract_metric` + `derive_status`), wired into `run_service._build_result`. Thresholds band the GX **unexpected-%** as `metric_value` (higher=worse, ordered, unset-tier skipped); thresholds-as-policy override GX `success`; binary fallback when no thresholds / no metric. **Settled in [ADR 0016](adr/0016-severity-derivation-semantics.md)** (incl. A→B reversibility: raw `observed_value` retained → switch is additive `direction` column + backfill, never destructive). `duration_ms` stays NULL (per-check timing not separable from GX's suite-level `validate()`). 16 unit + 1 integration test; both modules 100%
- [x] ✅ Update check CRUD + run result response schemas with threshold fields + status values — check-CRUD thresholds (PR-B2) + result response now carries `status` (`pass`/`warn`/`fail`/`critical`) + `metric_value` (probe `CheckResultResponse`, PR-C)

### Monitor abstraction & metric storage — do-now seams (3 tasks — 3/3)
> **Day 1 design decision: `check.kind` discriminator + numeric metric storage — ✅ settled in [ADR 0012](adr/0012-monitor-kind-seam.md); rides the same migration.** Keeps v1.x auto-monitors (freshness / volume / schema-drift / anomaly — post-v1 Theme A) from forcing a check/result schema rewrite. v1 implements `expectation` only.
- [x] ✅ Add `kind` discriminator to check model (`'expectation'` default; `freshness`/`volume`/`schema_drift`/`anomaly` reserved) — `checks.kind` `NOT NULL DEFAULT 'expectation'` + CHECK over the 6 reserved kinds (incl. `comparison`, ADR 0014) — migration `9c59b6a44f33`
- [x] ✅ Generalise run path to dispatch by `check.kind` (`expectation` → GX `CheckRunner`; others raise `NotImplementedError`) — PR-D: `run_service._specs_for_checks` dispatches by kind; a non-`expectation` check raises `NotImplementedError` → the run goes terminal `failed` **without invoking the adapter** (never silently run as a GX expectation). Composes with the Week-5 `connection.type` runner selection (`kind` picks the monitor, type picks the adapter). `run_service` 100%; test fixtures now set `kind` to mirror DB rows
- [x] ✅ Add `metric_value` (NUMERIC) + `duration_ms` (INT) to results — SQL-aggregatable metric for Week-6 trends + v1.1 anomaly; per-check runtime for cost surface — nullable columns on `Result` — migration `9c59b6a44f33`

### Column profiler (3 tasks — 0/3)
- [ ] ⬜ Column profiler endpoint (Snowflake) — nulls, distinct count, min / max, top values
- [ ] ⬜ Column profiler endpoint (ADLS / S3) — same stats via Pandas on sampled file
- [ ] ⬜ Column profiler endpoint (Unity Catalog) — via Databricks SQL Warehouse

### Flat file check specifics (2 tasks — 0/2)
- [ ] ⬜ Check types for flat files: schema validation, row count, null checks, freshness by filename date
- [ ] ⬜ Batch resolution — resolve batching regex to matched files, pick latest or specific batch

### Unity Catalog check specifics (2 tasks — 0/2)
- [ ] ⬜ UC table check path — `spark.read.table()` → GX DataFrame datasource → run suite
- [ ] ⬜ Integration tests across all three datasource types

**Week 3 total: 8 / 18**

---

## Week 4 — Connection manager UI + check editor UI (frontend)

**Exit gate:** Users can configure any connection type and author checks end-to-end in the UI.

### Frontend tooling coordinated bumps (added — not in original roadmap) (1 task — 1/1)
- [x] ✅ Vite 8 coordinated bump — `vite` ^6→^8.0.16 + `@vitejs/plugin-react` ^5→^6.0.2 + `vitest` ^3→^4.1.8 + `@vitest/coverage-v8` ^4.1.8 in lockstep — [PR #119](https://github.com/TheurgicDuke771/DataQ/pull/119) (Fixes [#65](https://github.com/TheurgicDuke771/DataQ/issues/65); supersedes Dependabot #111 + closed [#57](https://github.com/TheurgicDuke771/DataQ/pull/57)). plugin-react v6 peers `vite ^8`, vitest v4 peers `vite ^6||^7||^8` — done early to drain the frontend dep backlog; format/lint/typecheck/test/build all green

### Frontend polish from PR-3c review (added — not in original roadmap) (3 tasks — 0/3)
- [ ] ⬜ Wrap `MsalProvider` subtree in an antd `AntApp` / React error boundary so MSAL render-time failures don't fall back to plain text ([PR #63 worth-noting](https://github.com/TheurgicDuke771/DataQ/pull/63))
- [ ] ⬜ Bundle code-splitting — `React.lazy` per route + `manualChunks` for antd (689 KB pre-gzip warning, defer until more routes exist) ([PR #63 perf](https://github.com/TheurgicDuke771/DataQ/pull/63))
- [ ] ⬜ Tighten `Settings.model_config` `extra="ignore"` → `"forbid"` once compose-only vs app-only `.env` are split ([PR #39 nit](https://github.com/TheurgicDuke771/DataQ/pull/39))

### Connection manager UI (6 tasks — 0/6)
- [ ] ⬜ Connection cards — Snowflake (3 envs), ADF, ADLS/S3, Databricks sections with status badges
- [ ] ⬜ Add connection drawer — type-specific form fields per connection type
- [ ] ⬜ Connection health page — bulk test, live status, re-auth surface
- [ ] ⬜ Connection re-auth UI — surface expired tokens, inline refresh action
- [ ] ⬜ ADLS/S3 connection form — account URL, container browser, managed identity / SAS toggle
- [ ] ⬜ Databricks connection form — workspace URL, PAT, SQL Warehouse picker

### Check editor UI (9 tasks — 0/9)
- [ ] ⬜ Suite list + detail two-panel layout, environment badge on each suite
- [ ] ⬜ Form-based check editor (Snowflake) — database / schema / table picker, check type dropdown, threshold
- [ ] ⬜ Flat file check editor — container picker, batching regex input, file format selector, check type
- [ ] ⬜ Unity Catalog check editor — catalog / schema / table three-level picker
- [ ] ⬜ Monaco SQL editor for custom check (all datasource types, Snowflake keyword autocomplete)
- [ ] ⬜ Column profiler panel — inline in check editor, loads on table / file selection
- [ ] ⬜ Check dry-run button — show preview pass / fail inline before saving
- [ ] ⬜ Check version history drawer — see previous config before overwriting
- [ ] ⬜ Severity tier toggle in check editor — three-threshold UI when enabled

### Access & admin UI (3 tasks — 0/3)
- [ ] ⬜ Suite sharing panel — add / remove users, assign roles inline
- [ ] ⬜ Admin page — list all suites, all users, access overview
- [ ] ⬜ Suite export / import UI (download JSON, upload JSON)

**Week 4 total: 1 / 22**

---

## Week 5 — Execution engine + scheduling

**Exit gate:** Async runs with live progress across all datasource types; scheduling operational.

### Async execution backend (7 tasks — 1/7 ✅, early)
- [x] ✅ Celery + Redis background task runner for GX scan execution — `run_suite` task + `run_service` — landed early via [PR 4a](https://github.com/TheurgicDuke771/DataQ/pull/74) + [PR 4c-i](https://github.com/TheurgicDuke771/DataQ/pull/78) + [PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)
- [ ] ⬜ Generalise `run_suite` worker dispatch — select the `CheckRunner` by `connection.type` (replaces the Snowflake-hardcoded wiring in `worker/tasks.py`); prerequisite for the flat-file / UC run paths below, and the seam that makes post-v1 RDBMS adapters (MS-SQL, BigQuery) a drop-in _(added per [ADR 0011](adr/0011-extensibility-seams-for-deferred-integrations.md))_
- [ ] ⬜ Run progress API — poll endpoint returning per-check live status
- [ ] ⬜ Cancel run endpoint — gracefully terminate in-progress Celery task
- [ ] ⬜ Run history retention policy — configurable purge of results older than N days
- [ ] ⬜ Flat file run path — resolve batch, load via Pandas, execute GX suite
- [ ] ⬜ UC run path — submit job to Databricks SQL Warehouse, execute GX suite

### ADF + Airflow hybrid monitoring (webhook + polling fallback) (4 tasks — 0/4)
- [ ] ⬜ Celery beat task — poll ADF REST API every 10 min for **succeeded** runs (skip pipelines updated by webhook recently)
- [ ] ⬜ Celery beat task — poll Airflow REST API `dagRuns` every 10 min _(added per ADR 0004; not in roadmap)_
- [ ] ⬜ Gap recovery logic — on startup + every 30 min, fetch last hour of run statuses
- [ ] ⬜ `GET /api/v1/orchestration/pipelines` — latest status per pipeline/DAG, provider-agnostic

### Execution UI (4 tasks — 0/4)
- [ ] ⬜ Run now panel — suite picker, env / datasource, notification target
- [ ] ⬜ Live run progress UI — check-by-check status with spinner + cancel button
- [ ] ⬜ Scheduled runs table — create, pause, delete cron schedules
- [ ] ⬜ Recent runs audit table with drill-down link to results

**Week 5 total: 0 / 15**

---

## Week 6 — Results dashboard + alerting

**Exit gate:** Full results dashboard live across all source types; alerts firing with suppression.

### Results dashboard (10 tasks — 0/10)
- [ ] ⬜ Health score stat cards + 7-day trend chart
- [ ] ⬜ Per-suite pass / fail progress bars — warn / fail / critical breakdown
- [ ] ⬜ Results filter bar — env, datasource type, suite, date range, status
- [ ] ⬜ Failed check drill-down — sample failing rows from GX result
- [ ] ⬜ Per-check historical trend chart
- [ ] ⬜ Orchestration status panel — pipeline/DAG status, polls every 30s, correlated DQ result
- [ ] ⬜ Datasource type filter — Snowflake / flat file / Unity Catalog toggle
- [ ] ⬜ CSV + PDF export of results
- [ ] ⬜ Severity badge colours — green / amber / red / dark red
- [ ] ⬜ Health score weighting — apply warn/fail/critical penalty weights

### Alerting (6 tasks — 0/6)
- [ ] ⬜ `ResultPublisher` seam — dispatch run outcomes from the post-`execute_run` completion point through a small publisher interface (Teams is the v1 implementation, not a hardcoded call); carry a PII redaction / opt-in policy on `sample_failures` at the seam since it leaves DataQ's trust boundary. Enables post-v1 TestRail / JIRA / Xray publishers as additional subscribers with no re-plumbing _(added per [ADR 0011](adr/0011-extensibility-seams-for-deferred-integrations.md))_
- [ ] ⬜ Notification config UI — Teams webhook per suite, alert on fail / warn / always
- [ ] ⬜ Alert suppression / snooze — silence a specific check for N hours
- [ ] ⬜ Alert dedup — fire on first failure only, not on every subsequent scheduled run
- [ ] ⬜ Teams adaptive card payload — check, datasource, table / file, observed vs expected
- [ ] ⬜ Severity-aware alert routing — warn quiet, fail standard, critical @channel

**Week 6 total: 0 / 16**

---

## Week 7 — Deployment, hardening & docs

**Exit gate:** Production-ready v1 deployed to Azure, CI/CD live, team onboarded.

### DevOps & deployment (5 tasks — 0/5, 1 partial early)
- [ ] 🟡 Containerise FastAPI + React + Celery + Redis — backend `Dockerfile` + `api`/`worker` compose services landed early ([PR 4a](https://github.com/TheurgicDuke771/DataQ/pull/74)); React image + ACR/ACA still pending
- [ ] ⬜ Push images to Azure Container Registry
- [ ] ⬜ Deploy to Azure Container Apps (API + Celery worker) + Azure Static Web App (React UI) — wire CORS middleware for Static-Web-App → Container-Apps cross-origin ([PR #40 nit](https://github.com/TheurgicDuke771/DataQ/pull/40)); override hardcoded `dataq:dataq` Postgres creds + all secrets via Container Apps secret refs ([PR #39 nit](https://github.com/TheurgicDuke771/DataQ/pull/39))
- [ ] ⬜ CI/CD pipeline — lint, test, build, deploy on merge to `main`
- [ ] ⬜ Application Insights integration — traces, errors, slow queries, Celery task metrics _(keep the export behind the structlog handler seam in `core/logging.py`; if a vendor-neutral path is wanted, route via OpenTelemetry/OTLP so the backend is swappable — per [ADR 0010](adr/0010-provider-agnostic-infrastructure-seams.md). App Insights stays the only v1 backend; do not abstract speculatively)_
- [ ] ⬜ Real-vault integration test for `AzureKeyVaultStore` lazy-import branch (currently 0% coverage) ([PR #56 nit](https://github.com/TheurgicDuke771/DataQ/pull/56))

### Azure Monitor webhook setup (post-deployment) (5 tasks — 0/5)
- [ ] ⬜ Action Group (pre-prod) — webhook to pre-prod API URL, shared secret from Key Vault
- [ ] ⬜ Alert Rule (pre-prod) — `example-adf-preprod` factory, Failed pipeline runs signal
- [ ] ⬜ Action Group (prod) — same config pointing to prod API URL
- [ ] ⬜ Alert Rule (prod) — `example-adf-prod` factory, same signal + dimension config
- [ ] ⬜ Smoke test — deliberate DEV pipeline failure → webhook → DB update → UI within 5 min

### FastMCP — MCP server (12 tasks — 0/12)
- [ ] ⬜ Install FastMCP, scaffold `mcp_server.py`, mount at `/mcp`
- [ ] ⬜ Wire Azure AD token validation into FastMCP's auth provider
- [ ] ⬜ Resource: `list_suites`
- [ ] ⬜ Resource: `get_suite_results`
- [ ] ⬜ Resource: `get_health_score`
- [ ] ⬜ Resource: `get_adf_pipeline_status`
- [ ] ⬜ Tool: `trigger_suite_run`
- [ ] ⬜ Tool: `get_run_status`
- [ ] ⬜ Tool: `create_check`
- [ ] ⬜ Tool: `profile_column`
- [ ] ⬜ LLM-optimised docstrings for all 8 tools
- [ ] ⬜ E2E test with Claude Desktop — 4 canonical natural-language queries

### Hardening & docs (5 tasks — 0/5)
- [ ] ⬜ E2E test coverage for critical paths (auth, Snowflake run, flat file run, UC run, results)
- [ ] ⬜ Error handling audit — consistent error shapes across all endpoints
- [ ] ⬜ Ensure all FastAPI endpoints have `summary`, `description`, `tags`, `response_model`
- [ ] ⬜ README + deployment guide + env-var reference
- [ ] ⬜ Team onboarding session + feedback collection
- [ ] ⬜ Document MCP connection config (Claude Desktop / Claude.ai / Copilot / Cursor) in README

**Week 7 total: 0 / 29**

---

## Week 8 — Unit testing

**Exit gate:** ≥80% coverage gate enforced in CI across backend, API, frontend.

### Backend unit tests (pytest) (11 tasks — 0.5/11)
- [ ] ⬜ Auth service — token validation, session expiry, Key Vault credential retrieval
- [ ] 🟡 Connection service — CRUD operations, test endpoint logic per datasource type — Snowflake path covered (16 DB-backed tests, `connection_service.py` 100%) — [PR 5](https://github.com/TheurgicDuke771/DataQ/pull/85); ADF path + `(type, env)` orchestrator-guard covered (3 service tests, `adf.py` 100%) — PR 6; ADLS/S3/UC paths pending their CRUD PRs
- [ ] ⬜ Suite service — CRUD, share assignment, export / import serialisation
- [ ] ⬜ Check service — expectation builder, SQL validator, dry-run logic, threshold tier evaluation
- [ ] ⬜ Column profiler service — null count, distinct count, min/max per datasource type
- [ ] 🟡 Execution service — `run_suite` dispatch + `run_service.execute_run` + GX adapter + NaN sanitizer tested early ([PR 4b.1](https://github.com/TheurgicDuke771/DataQ/pull/77) + [PR 4c-i](https://github.com/TheurgicDuke771/DataQ/pull/78) + [PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)); progress polling / cancel / retention purge pending
- [ ] ⬜ Alerting service — Teams webhook dispatch, dedup logic, snooze / suppression
- [ ] 🟡 ADF service — parse + upsert + `fetch_run_detail` + enrichment + trigger-on-success covered (`AdfProvider` parse/fetch unit tests incl. ARM mapping/http-error; `orchestration_service` DB tests incl. replay idempotency, enrichment fail-soft, trigger gating — modules 100%) — PR 7 + PR 8; `list_recent_runs` polling + gap-recovery dedup pending Week 5
- [ ] ⬜ ADF polling service — succeeded run fetch, skip-if-recently-updated, gap recovery
- [ ] ⬜ Result service — health score calc, historical trend aggregation, export generation
- [ ] ⬜ MCP service — each of 8 tools returns correct shape; auth rejection; `trigger_suite_run` returns valid run_id
- [x] 🟡 **Secret service** — 12 tests, 88% coverage — [PR 3b](https://github.com/TheurgicDuke771/DataQ/pull/56) _(landed early)_

### API layer tests (pytest + httpx) (6 tasks — 0/6, probe endpoint covered early)
- [ ] ⬜ Auth endpoints — login redirect, token refresh, unauthorised → 401
- [x] ✅ **Probe endpoints** (out-of-roadmap) — POST creates+dispatches, idempotent seed, GET results, 404 — against real Postgres — [PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)
- [x] 🟡 Connection endpoints — CRUD happy paths + validation errors — Snowflake covered (13 TestClient tests: CRUD, 422/404/502, secret-never-leaks, auth gate) — [PR 5](https://github.com/TheurgicDuke771/DataQ/pull/85); ADF covered (4 TestClient tests: create, orchestrator 409, second-env 201, type filter) — PR 6; ADLS/S3/UC types follow their CRUD PRs
- [ ] ⬜ Suite & check endpoints — CRUD, share, export / import, dry-run
- [ ] ⬜ Execution endpoints — trigger run, poll progress, cancel, list history
- [ ] ⬜ Results endpoints — dashboard data, drill-down, filters, download
- [x] 🟡 ADF webhook endpoint — valid payload → 200 (recorded/ignored), missing+wrong token → 401, malformed/non-JSON → 422, secret-unconfigured → 503 (9 TestClient tests) — PR 7; duplicate-runId idempotency asserted at the service layer

### Frontend unit tests (Vitest + RTL) (6 tasks — 1/6)
- [x] ✅ **AuthGate** — 4 tests (dev_bypass renders children, unconfigured banner, real+unauth sign-in button, real+auth renders children) — [PR 3c](https://github.com/TheurgicDuke771/DataQ/pull/63)
- [x] 🟡 **API client interceptor** — 3 tests (no-token in dev, Bearer in real-with-account, no-token in real-without-account) — [PR 3c](https://github.com/TheurgicDuke771/DataQ/pull/63)
- [ ] ⬜ Login screen — Azure AD button renders, redirects on click
- [ ] ⬜ Connection manager — card per type, status badge colours, re-auth flow
- [ ] ⬜ Check editor — form fields per check type, profiler loads, dry-run, severity tier toggle
- [ ] ⬜ Suite sharing panel — add/remove user, role assignment
- [ ] ⬜ Execution page — run now, progress bar updates, cancel button
- [ ] ⬜ Results dashboard — stat cards, filters, ADF panel, severity badges

### Test infrastructure (3 tasks — 0.5/3 🟡 early)
- [ ] 🟡 Pytest fixtures — transactional Postgres `db_session` fixture + CI postgres service + fake `CheckRunner`/session landed ([PR 4b.1](https://github.com/TheurgicDuke771/DataQ/pull/77) + [PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)); mock GX context + mock webhooks pending
- [ ] ⬜ CI gate — PRs blocked if coverage drops below 80% _(coverage currently ~91%; `--cov-fail-under` still 0 until W8)_
- [ ] ⬜ Test data fixtures — sample suites, check results, run histories

**Week 8 total: ~4 / 26 (early credit from per-slice tests in W1 — overall coverage ~91%)**

---

## Aggregate

| Week | Done | In progress | Pending | Total |
|---|---|---|---|---|
| Week 1 | 7 | 1 | 2 | 10 |
| Week 2 | 15 | 1 | 3 | 19 |
| Week 3 | 8 | 0 | 10 | 18 |
| Week 4 | 1 | 0 | 21 | 22 |
| Week 5 | 1 | 0 | 14 | 15 |
| Week 6 | 0 | 0 | 16 | 16 |
| Week 7 | 0 | 1 | 28 | 29 |
| Week 8 | 2 | 3 | 21 | 26 |
| **TOTAL** | **34** | **6** | **115** | **155** |

> 155 > 100 because ADR 0004 added Airflow tasks, ADR 0011 added two seam tasks (generic runner dispatch, `ResultPublisher`), ADR 0012 added three Week-3 monitor-kind / metric seam tasks, plus PR-review follow-ups not in the original roadmap. Tracked here for honesty.

---

## Active infrastructure issues

Issues that aren't roadmap tasks but block / risk the work.

| # | Title | Status | Will affect |
|---|---|---|---|
| ~~[#42](https://github.com/TheurgicDuke771/DataQ/issues/42)~~ | ~~Add FK indexes on join columns (backward-compat migration)~~ | **Closed** ([PR #70](https://github.com/TheurgicDuke771/DataQ/pull/70)) | n/a |
| ~~[#43](https://github.com/TheurgicDuke771/DataQ/issues/43)~~ | ~~Silence CodeQL false positives (Alembic + Protocol stubs)~~ | **Closed** ([PR #69](https://github.com/TheurgicDuke771/DataQ/pull/69)) | n/a |
| ~~[#50](https://github.com/TheurgicDuke771/DataQ/issues/50)~~ | ~~Bridge uvicorn access logs through structlog~~ | **Closed** ([PR #71](https://github.com/TheurgicDuke771/DataQ/pull/71)) | n/a |
| ~~[#51](https://github.com/TheurgicDuke771/DataQ/issues/51)~~ | ~~Emit per-request structured log from request_id middleware~~ | **Closed** ([PR #71](https://github.com/TheurgicDuke771/DataQ/pull/71)) | n/a |
| ~~[#54](https://github.com/TheurgicDuke771/DataQ/issues/54)~~ | ~~Consolidate mypy / type-check dep lists (3-file drift)~~ | **Closed** ([PR #68](https://github.com/TheurgicDuke771/DataQ/pull/68)) | n/a |
| ~~[#62](https://github.com/TheurgicDuke771/DataQ/issues/62)~~ | ~~MSAL redirect lifecycle (real-AAD smoke test deferred)~~ | **Closed** (completed 2026-05-28) | n/a |
| ~~[#65](https://github.com/TheurgicDuke771/DataQ/issues/65)~~ | ~~Vite 8 coordinated bump (vite + plugin-react + vitest)~~ | **Closed** ([PR #119](https://github.com/TheurgicDuke771/DataQ/pull/119)) | n/a — superseded Dependabot #111 |
| [#92](https://github.com/TheurgicDuke771/DataQ/issues/92) | Surface the ADF webhook URL instead of hand-assembling a secret-bearing URL | Open | Week 4 connection UI / ADF onboarding |
| ~~[#72](https://github.com/TheurgicDuke771/DataQ/issues/72)~~ | ~~ADR 0004 follow-up: document `trigger_bindings` one-orchestrator-per-(provider, env) assumption~~ | **Closed** ([PR #83](https://github.com/TheurgicDuke771/DataQ/pull/83)) | n/a — guard enforced in PR 6 ADF CRUD |
| ~~[#75](https://github.com/TheurgicDuke771/DataQ/issues/75)~~ | ~~Integration-assert request_id propagates FastAPI→Celery worker logs~~ | **Closed** ([PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)) | n/a |
| ~~[#86](https://github.com/TheurgicDuke771/DataQ/issues/86)~~ | ~~`EnvSecretStore.set` is per-process — Celery worker can't resolve API-written secrets (dev only)~~ | **Closed** ([PR #95](https://github.com/TheurgicDuke771/DataQ/pull/95)) | n/a — Redis-backed dev secret store |
| ~~[#87](https://github.com/TheurgicDuke771/DataQ/issues/87)~~ | ~~Map `SecretWriteError` → 502 in connection create/update (currently 500)~~ | **Closed** ([PR #94](https://github.com/TheurgicDuke771/DataQ/pull/94)) | n/a |

**Deferred polish** (Week-1 governance era; do during slack): #8, #10, #12, #17, #18, #19, #20.

**New follow-up:** real-Snowflake DEV live-run smoke for `SnowflakeCheckRunner.run_checks` (deferred; needs DEV creds — pairs with Week 7 vault provisioning).

---

## Pending design decisions (must land before the week they affect)

| Decision | Affects | Deadline |
|---|---|---|
| ~~Severity tier weights (warn / fail / critical → health score)~~ | Week 3 Day 1 schema migration | ✅ Resolved — [ADR 0005](adr/0005-severity-tier-weights.md) (0.5 / 1.0 / 2.0; SQL-normalised health score) |
| ~~Monitor-kind seam (`check.kind` discriminator + numeric `metric_value` / `duration_ms`)~~ — ADR 0012 | Week 3 schema migration (rides the threshold migration) | ✅ Resolved — [ADR 0012](adr/0012-monitor-kind-seam.md) (`expectation` only in v1; rest reserved) |
| ~~ADF webhook auth (shared secret + rotation)~~ | Week 2 webhook receiver | ✅ Resolved — [ADR 0006](adr/0006-adf-webhook-authentication.md) (secret in URL, hard cutover, no v1 replay check) |
| ~~Airflow callback signing key (HMAC)~~ | Week 2 webhook receiver | ✅ Resolved — [ADR 0007](adr/0007-airflow-callback-model.md) (HMAC-SHA256 header + polling fallback) |
| Azure tenant + app registration values | Week 7 deployment | Before Week 7 |

---

## How to update this file

When merging a PR:

1. Find the task(s) it implements in the relevant week.
2. Flip `⬜` → `✅` (or `⬜` → `🟡` if partial).
3. Append the PR link: `— [PR #N](https://github.com/.../pull/N)`.
4. Update the per-week subtotal at the bottom of the week.
5. Update the **Snapshot** table at the top (task count, open PRs/issues) and the **Aggregate** table.
6. If the PR added an out-of-roadmap task (e.g. ADR-driven scope change), add a row with the note.

PR-template checkbox enforces this. If the change is purely tooling / docs that doesn't map to a roadmap task, tick the "N/A" checkbox.
