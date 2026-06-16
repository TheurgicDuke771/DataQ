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
| **Current week** | Week 4 of 8 (Connection manager UI + check editor UI) — **in progress** (Weeks 1–3 complete) |
| **Roadmap tasks done** | 76 ✅ + 18 🟡 / 177 (~43%) |
| **Out-of-roadmap PRs landed** | governance, tooling lock, Entire CLI, Dependabot triage (10 bumps + pyarrow direct-dep fix #202/#201), PR-3 cleanup + ADRs 0005/0006/0007/0010/0011/0012/0013/0014/0016/**0017** (Python 3.11→3.13 + Snowflake 3→4 CVE refresh, [#129](https://github.com/TheurgicDuke771/DataQ/issues/129)) + the Week-3 testing-discipline upgrade (adversarial harness + mutation spikes, CONTRIBUTING rule 4a) |
| **Week-1 exit gate** | A logged-in user can hit a FastAPI endpoint that triggers GX against Snowflake DEV and persists a result row. — **met** (plumbing complete via PR 4a–4c; live-Snowflake run fails-soft pending DEV creds — deferred smoke) |
| **Next milestone** | Week 4 (frontend) nearly done: connection manager + suites list/detail + catalog check editor + dry-run + profiler + export/import + **sharing panel** + **run-target editor** + **manual Run button** + **Suite Triggers panel** (#216) + **Monaco custom-SQL editor** (ADR 0019; backend #258 + frontend #259) all shipped; **remaining**: admin page (still **backend-blocked** — needs a list-all endpoint) + the check version-history drawer. Week-5 early-credit landed (worker runner-dispatch [#146](https://github.com/TheurgicDuke771/DataQ/issues/146), ADF/Airflow polling [#171](https://github.com/TheurgicDuke771/DataQ/issues/171), `trigger_bindings` CRUD [#172](https://github.com/TheurgicDuke771/DataQ/issues/172)) |
| **Open issues** | **21 open** — roadmap *features* are tracked as ⬜ checkboxes per week (below), not GitHub issues, so these are follow-ups / polish only. **Week-1 carryover (→ W7):** [#168](https://github.com/TheurgicDuke771/DataQ/issues/168) (silent token refresh) / [#169](https://github.com/TheurgicDuke771/DataQ/issues/169) (real Key Vault) / [#170](https://github.com/TheurgicDuke771/DataQ/issues/170) (prod-docs gate). **Backend:** [#147](https://github.com/TheurgicDuke771/DataQ/issues/147) (profiler cleanup) / [#122](https://github.com/TheurgicDuke771/DataQ/issues/122) (skip/error statuses — seam landed) / [#124](https://github.com/TheurgicDuke771/DataQ/issues/124) (DQ dimensions) / [#194](https://github.com/TheurgicDuke771/DataQ/issues/194) + [#195](https://github.com/TheurgicDuke771/DataQ/issues/195) (Snowflake key-pair: encrypted keys / GX deprecated path) / [#92](https://github.com/TheurgicDuke771/DataQ/issues/92) (ADF webhook URL). **Frontend (W4):** [#192](https://github.com/TheurgicDuke771/DataQ/issues/192) (nav prefix-match) / [#197](https://github.com/TheurgicDuke771/DataQ/issues/197) (Select test helper) / [#199](https://github.com/TheurgicDuke771/DataQ/issues/199) (toast helper) / [#204](https://github.com/TheurgicDuke771/DataQ/issues/204) (drawer/delete dedup) / [#205](https://github.com/TheurgicDuke771/DataQ/issues/205) (catalog↔GX contract test) / [#128](https://github.com/TheurgicDuke771/DataQ/issues/128) (full-stack E2E/Playwright). **Governance polish:** #8/#10/#17/#18/#19/#20. **Closed this stretch:** #129, #146, #171, #172, #201 |
| **Open PRs** | none |
| **Design gates** | all Week-3 design gates resolved (ADR 0005 severity weights, 0012 monitor-kind seam, 0016 severity derivation — migration shipped). Pending: two-connection model for `comparison` checks (ADR 0015, post-v1, non-blocking) |

---

## Out-of-roadmap work (foundation that's not in the 100-task list)

These were preconditions for executing the roadmap. Listed for completeness.

| Bundle | PRs | Status |
|---|---|---|
| **PR 0 governance** | #1–#24, #44, #55 — `.gitignore`, CLAUDE.md, CODEOWNERS, ADRs 0001–0004 + 0009, PR / issue templates, architecture diagram, Claude Code agents/skills/hooks/MCP, Entire CLI hooks + allowlist | ✅ |
| **PR 1 tooling lock** | #37 — conda env, Black, Ruff, mypy, pre-commit, CI workflow, Dependabot, `scripts/setup.sh`, frontend tooling | ✅ |
| **PR 2 hygiene follow-ups** | #44 (mermaid + tsconfig + ADR 0009 + CLAUDE.md status), #47 (precommit reorder), #48 (mermaid fix #2), #49 (precommit mypy deps), #52 (markdown linebreak preservation) | ✅ |
| **Architecture Q&A ADRs** | ADR 0010 (provider-agnostic infra seams — cloud portability) + ADR 0011 (extensibility seams — more datasources, `ResultPublisher`, dbt-as-orchestration-provider) + ADR 0013/0014 (marketplace/BYOL anti-lock-in; reconciliation `comparison` kind) + ADR 0016 (severity derivation). Record the now-vs-post-v1 timing per seam | ✅ |

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

### ADF webhook receiver (Azure Monitor → DQ platform) (5 tasks — 4 ✅ / 1 🟡)
- [x] ✅ `POST /api/v1/orchestration/events/adf` — receive Azure Monitor payload, validate shared secret (constant-time, ADR 0006), return 200 — PR 7 _(unified `OrchestrationProvider` seam landed: `orchestration/base.py` Protocol + `RunUpdate` DTO + provider registry; ADF reference impl per ADR 0004 — service code dispatches by provider, never branches on ADF)_
- [x] ✅ Parse webhook payload — `AdfProvider.parse_event` extracts `factoryName`/`pipelineName`/`runId`/`status`/`firedDateTime` → `RunUpdate`, ADF→`PIPELINE_RUN_STATUSES` normalisation — PR 7 _(exact Common-Alert-Schema field mapping validated at Week-7 deploy smoke)_
- [x] ✅ Follow-up ADF REST API call on webhook receipt — fetch run details — PR 8 _(`AdfProvider.fetch_run_detail` GETs the ARM `pipelineruns/{runId}` for authoritative status/timing/message; `orchestration_service.ingest_event` enriches **best-effort** before upsert — any failure (no creds, transport) falls back to the parsed event so a valid webhook is never dropped)_
- [ ] 🟡 Upsert pipeline run status into `pipeline_runs`; correlate with suite run — idempotent upsert (PR 7) + **trigger-on-success skeleton** (PR 8): a `succeeded` run matching enabled `trigger_bindings` creates queued `Run` rows (`triggered_by="<provider>:<pipeline>:<run_id>"`, idempotent on replay); failures never trigger (ADR 0004). **`run_suite` dispatch ungated** in Week 5 ([#215](https://github.com/TheurgicDuke771/DataQ/issues/215), PR-C0a — `Suite.target` + `run_target` resolver; `_trigger_suites` now `run_suite.delay`s each created run); `trigger_bindings` CRUD shipped ([#172](https://github.com/TheurgicDuke771/DataQ/pull/190)) — its UI is Week 5 ([#216](https://github.com/TheurgicDuke771/DataQ/issues/216)). `list_recent_runs` + 10-min polling beat shipped ([#171](https://github.com/TheurgicDuke771/DataQ/pull/189)).
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

### Suite & check backend (4 tasks — 4/4 ✅)
- [x] ✅ API: CRUD for suites and GX expectations (Snowflake path) — **suites** (PR-B1): `suite_service` + `/suites` CRUD (`connection_id` validated then immutable; delete cascades to checks). **checks** (PR-B2): `check_service` + nested `/suites/{id}/checks` CRUD surfacing `kind` + `warn/fail/critical_threshold` + GX `expectation_type`/`config`. v1 monitor-kind guard (only `expectation`; reserved kinds → 422, ADR 0012); checks scoped to their suite (cross-suite access → 404); thresholds are `Decimal` in (exact `Numeric` storage) / `float` out (clean JSON). 24 TestClient tests; all four modules 100%. Share-based access filtering deferred to the suite-sharing task; **DQ-dimension classification** deferred + tracked ([#124](https://github.com/TheurgicDuke771/DataQ/issues/124))
- [x] ✅ API: suite sharing — assign users with owner / editor / viewer roles — **sharing API + authz core** (PR-E1): `suite_authz.require_permission` (404-hides a suite with no access, 403s an insufficient level) + `share_service` + `/suites/{id}/shares` CRUD. Schema vocab `view`/`edit`/`admin` + implicit owner=`created_by`; **admin can delete + manage shares** (per decision); grant-to-owner/unknown → 422; manage needs `admin`, list needs `view`. **Enforcement** (PR-E2): `require_permission` applied across the suite endpoints (GET=view, PATCH=edit, DELETE=admin) + all check endpoints (reads=view, writes=edit); `list_suites` scoped to owned-or-shared. Lands the access control deferred in B1/B2. ~26 TestClient tests across the matrix acting as different users (viewer reads-not-writes, editor writes-not-deletes, admin deletes, outsider→404, list scoping); shares/suites/checks routes + share_service all 100%
- [x] ✅ API: suite export to JSON + import from JSON — `suite_io_service` + `GET /suites/{id}/export` (view) / `POST /suites/import` (any authed user, like create). Document is **connection-agnostic** — omits all DB identity (`id`/`connection_id`/`created_by`/timestamps), so it's a reusable template; import **re-binds** to a freshly chosen `connection_id` and owns the new suite as the importer. Round-trippable: thresholds are `Decimal` in/out (exact). Import is **atomic** — every check kind is validated before any row is written (bad doc → 422, nothing persisted); unknown `version` → 422; missing connection → 422. Checks emitted in stable creation order (diffable). Reuses `check_service.validate_kind` (no dup). 7 TestClient tests (no-identity-leak, view-gated, owned-by-importer, export→import→export round-trip, unknown-connection/version/kind-atomic); `suite_io_service` + `suites.py` 100%
- [x] ✅ API: check dry-run endpoint — validate against live data, return preview result — `POST /suites/{id}/checks/dryrun` (`dryrun_service`): runs **one ad-hoc check** against the suite's connection synchronously and returns a preview (severity `status` + `metric_value` + sanitized `observed/expected`) **without persisting** any Run/Result. Reuses the severity derivation (ADR 0005/0016) + JSON sanitiser. `require_permission` **edit** (authoring); table passed in the body (checks don't carry a target table yet). v1 → 422: non-`expectation` kind, non-Snowflake connection (runner dispatch generalises Week 5); execution failure → 502 (adapter exception never echoed). No `sample_failures` in the preview (PII; follow-up). 7 TestClient tests (mocked runner); `dryrun_service` + `checks.py` 100%

### Severity threshold tiers (warn / fail / critical) (4 tasks — 4/4 ✅)
> **Day 1 design decision: severity weights — ✅ settled in [ADR 0005](adr/0005-severity-tier-weights.md) (warn 0.5 / fail 1.0 / critical 2.0; health = 100×(1−Σpenalty/(N×2.0))).**
- [x] ✅ Add `warn_threshold`, `fail_threshold`, `critical_threshold` fields to check model — nullable `Numeric` columns on `Check` (NULL → plain pass/fail) — migration `9c59b6a44f33`
- [x] ✅ Alembic migration — threshold columns + `status` enum (`pass`, `warn`, `fail`, `critical`) **+ the monitor-kind / metric columns below (one migration)** — the one-shot Week-3 schema seam `9c59b6a44f33` (tested up→down→up; `results.status` retargeted from `passed/failed/skipped` with a data-update-before-CHECK-swap; `run_service` now writes `pass`/`fail` binary-fallback per ADR 0005). `alembic check` clean (no model drift)
- [x] ✅ Post-processing in GX result handler — derive `warn` / `fail` / `critical` from observed value (PR-C) — `services/severity.py` (`extract_metric` + `derive_status`), wired into `run_service._build_result`. Thresholds band the GX **unexpected-%** as `metric_value` (higher=worse, ordered, unset-tier skipped); thresholds-as-policy override GX `success`; binary fallback when no thresholds / no metric. **Settled in [ADR 0016](adr/0016-severity-derivation-semantics.md)** (incl. A→B reversibility: raw `observed_value` retained → switch is additive `direction` column + backfill, never destructive). `duration_ms` stays NULL (per-check timing not separable from GX's suite-level `validate()`). 16 unit + 1 integration test; both modules 100%
- [x] ✅ Update check CRUD + run result response schemas with threshold fields + status values — check-CRUD thresholds (PR-B2) + result response now carries `status` (`pass`/`warn`/`fail`/`critical`) + `metric_value` (probe `CheckResultResponse`, PR-C)

### Monitor abstraction & metric storage — do-now seams (3 tasks — 3/3 ✅)
> **Day 1 design decision: `check.kind` discriminator + numeric metric storage — ✅ settled in [ADR 0012](adr/0012-monitor-kind-seam.md); rides the same migration.** Keeps v1.x auto-monitors (freshness / volume / schema-drift / anomaly — post-v1 Theme A) from forcing a check/result schema rewrite. v1 implements `expectation` only.
- [x] ✅ Add `kind` discriminator to check model (`'expectation'` default; `freshness`/`volume`/`schema_drift`/`anomaly` reserved) — `checks.kind` `NOT NULL DEFAULT 'expectation'` + CHECK over the 6 reserved kinds (incl. `comparison`, ADR 0014) — migration `9c59b6a44f33`
- [x] ✅ Generalise run path to dispatch by `check.kind` (`expectation` → GX `CheckRunner`; others raise `NotImplementedError`) — PR-D: `run_service._specs_for_checks` dispatches by kind; a non-`expectation` check raises `NotImplementedError` → the run goes terminal `failed` **without invoking the adapter** (never silently run as a GX expectation). Composes with the Week-5 `connection.type` runner selection (`kind` picks the monitor, type picks the adapter). `run_service` 100%; test fixtures now set `kind` to mirror DB rows
- [x] ✅ Add `metric_value` (NUMERIC) + `duration_ms` (INT) to results — SQL-aggregatable metric for Week-6 trends + v1.1 anomaly; per-check runtime for cost surface — nullable columns on `Result` — migration `9c59b6a44f33`

### Column profiler (3 tasks — 3/3 ✅)
- [x] ✅ Column profiler endpoint (Snowflake) — nulls, distinct count, min / max, top values — `profile_service` + `POST /suites/{id}/profile` (require_permission **edit**, suite-scoped so the connection is access-gated). Reads-only, persists nothing: one aggregate query (row count + null/distinct/min/max per column) + one top-N-values query per column, then `assemble_profile`. **SQL-injection-safe** — queries are built with the **SQLAlchemy Core expression language** (`select`/`table`/`column`, dialect-quoted) so there's no raw-string SQL sink (no S608/B608/CodeQL `py/sql-injection`); identifiers are additionally allowlist-validated (`validate_identifier`, strict `^[A-Za-z_][A-Za-z0-9_$]*$`) as defence-in-depth + a clean early 422, and `top_n` is `int()`-coerced. v1 → 422: non-Snowflake type (dispatch generalises Week 5), bad identifier, no schema; execution failure → 502 (adapter exception never echoed). min/max/top-values NaN-sanitised. 28 tests (22 pure: identifier allowlist incl. injection strings, compiled-SQL builders, assembly, div-by-zero, NaN; 6 endpoint via fake conn: stats, injection-422, unsupported-type-422, 502, edit-gated, no-schema-422); `suites.py` 100%, `profile_service` 90% (live `_open_connection` is the deferred warehouse seam)
- [x] ✅ Column profiler endpoint (ADLS / S3) — same stats via Pandas on sampled file — extended `profile_service` into a type dispatcher (`profile_connection`): SQL types → in-warehouse aggregation (unchanged), flat-file types (`adls_gen2`/`s3`) → download a **sample** of the file into Pandas and compute the same stats (`profile_dataframe`). Same `POST /suites/{id}/profile` endpoint, now polymorphic: SQL targets pass `table`/`schema`, flat-file targets pass `path` (+ optional `file_format`, else inferred from extension); response carries whichever identity applies. CSV + Parquet. **pandas "load less data" levers** (per the user's scale.html prompt): column projection (CSV `usecols`, Parquet `columns=` via pyarrow schema) so profiling 3 of N columns doesn't read all N, + row sampling (`_SAMPLE_ROWS`), + **zero-copy Arrow-backed dtypes** on the Parquet read (`dtype_backend="pyarrow"` — parquet is already Arrow, kept zero-copy; the stat helpers + `_to_native` are Arrow-scalar/`pd.NA`-safe). Stats are vectorised pandas (no Python row loops — enhancingperf N/A; CoW is a no-op for a read-only profiler + a process-global flag, so not toggled; pyarrow CSV engine rejected — it's incompatible with `nrows`/callable-`usecols`). NaN/Timestamp→JSON-safe. v1 → 422: unsupported type (UC still pending), missing target, unknown format, missing column; read failure → 502 (exception never echoed). Reuses `ColumnProfile`/`ProfileResult`. 24 new tests (pure `profile_dataframe`/`infer_file_format`/`_to_native`/projection-parse + endpoint via fake `_read_dataframe`); `suites.py` 100%, `profile_service` 85% (only the live `_open_connection`/`_download_bytes` network seams uncovered — deferred smoke). _Deferred: streaming/range reads (whole object still downloaded before sampling) + out-of-core (Dask) — future work if profiling cost bites._
- [x] ✅ Column profiler endpoint (Unity Catalog) — via Databricks SQL Warehouse — added `unity_catalog` to the profiler's **SQL** branch (reuses `profile_table` + the SQLAlchemy Core query builders + `assemble_profile` — no duplicate aggregation logic). `_open_connection` now dispatches the engine by type (`_engine_args`): Snowflake URL or a `databricks://token:…@host?http_path=…` URL via the installed `databricks` SQLAlchemy dialect. UC's **3-level namespace** (`catalog.schema.table`) is handled by building the table with a `quoted_name(f"{catalog}.{schema}", quote=False)` so the dialect emits three dotted parts rather than quoting the dotted string as one — verified by compiling the real builders against the Databricks dialect. Injection-safe: every part (catalog/schema/table/column) is allowlist-validated (no raw-string SQL, no CodeQL py/sql-injection surface). Endpoint gained `catalog` (request + response); the dispatcher requires it for UC (`422 profile_target_invalid` if absent). 12 new tests (catalog-aware builders compiled to SQL, `_engine_args` URL dispatch, UC endpoint stats via the same fake conn, missing-catalog 422, bad-catalog-identifier 422); `suites.py` 100%, `profile_service` 86% (only the live `_open_connection`/`_download_bytes` network seams uncovered — deferred smoke). **Completes the column profiler across all 4 datasources.**

### Flat file check specifics (2 tasks — 2/2 ✅)
- [x] ✅ Check types for flat files: schema validation, row count, null checks, ~~freshness by filename date~~ — `FlatFileCheckRunner` (`datasources/flatfile.py`): downloads the file (S3/ADLS) into a pandas DataFrame and runs the suite's GX expectations against GX's **pandas DataFrame asset**, so all GX expectation-kind checks (schema/row-count/null/value checks) run against flat files. Extracted the shared GX machinery — snake_case→GX-class translation, GX-result→`SuiteOutcome` mapping, and the suite/validation-definition `run_expectations` flow — into `datasources/gx_runner.py` (reused by the Snowflake runner and the future UC runner; no duplication). Flat-file IO (`download_bytes` + full-file `read_dataframe`) moved to `flatfile.py` behind primitives (raw config + resolved secret, not the ORM — matches `build_snowflake_runner`), and the column profiler now shares it. Because GX runs **in-process on the DataFrame**, the run path is fully unit-tested with a canned frame (real GX execution, not mocked) — only the network download is the deferred-smoke seam. 16 new tests; `gx_runner` 100%, `flatfile` 76% (only `download_bytes` uncovered). _Connection-type → runner dispatch (Snowflake vs flat-file) is the Week-5 `run_suite` wiring; **freshness-by-filename** is the reserved `freshness` monitor kind (deferred, ADR 0012), not a GX expectation._
- [x] ✅ Batch resolution — resolve batching regex to matched files, pick latest or specific batch — `resolve_batch` (`datasources/flatfile.py`): a batch `pattern` is a regex whose **first capture group is the batch key**; `latest` selects the greatest key (lexicographic — ISO dates sort chronologically), falling back to the storage modified-time when the pattern has no group; `specific` selects the file whose key equals a given `batch`. `BatchNotFoundError` on no match / unknown batch. The filter+select logic is pure and fully unit-tested (8 tests); object listing (`list_files` — S3 `list_objects_v2` paginated / ADLS `list_blobs`) is the live seam, and `resolve_batch_file` composes list+resolve. _Wiring a check's target to a resolved batch path is the Week-5 `run_suite` dispatch._

### Unity Catalog check specifics (2 tasks — 2/2 ✅)
- [x] ✅ UC table check path — UC table → GX DataFrame datasource → run suite — `UnityCatalogCheckRunner` (`datasources/unity_catalog.py`): reads the target table from the Databricks SQL Warehouse into a pandas DataFrame (`pd.read_sql_table` via the databricks SQLAlchemy dialect — reflection quotes identifiers, so no hand-built SQL) and runs the suite's GX expectations against GX's **pandas DataFrame asset** — the "GX DataFrame datasource" shape (§5), the same shape Databricks Labs DQX consumes, so v1.1 swaps GX→DQX behind this one interface. Reuses the shared `gx_runner` (`run_expectations` + result mapping) exactly like the flat-file runner; the `catalog` is pinned in the connection URL (`build_databricks_url`, now shared with the column profiler — no duplication) so the 2-level `schema.table` resolves to `catalog.schema.table`. Because GX runs **in-process on the DataFrame**, the run path is unit-tested with a canned frame (real GX execution); only the reflect+read (`_read_table`) is the deferred-smoke seam. `build_unity_catalog_runner` mirrors `build_snowflake_runner` (raw config + resolved PAT, not the ORM). 14 tests; `unity_catalog` 91% (only `_read_table` uncovered). _Connection-type → runner dispatch is the Week-5 `run_suite` wiring._
- [x] ✅ Integration tests across all three datasource types — `backend/tests/integration/test_datasource_runs.py`: end-to-end suite runs through the **real** `run_service.execute_run` + **real Postgres** persistence, with **real GX execution** for the flat-file (`FlatFileCheckRunner`) and Unity Catalog (`UnityCatalogCheckRunner`) paths on an in-memory DataFrame (only the file download / table read is stubbed) — exercising the whole chain: `check.kind` dispatch → runner → GX validation → severity derivation → `Result` rows. Snowflake runs through a canned outcome (live warehouse is the deferred-smoke seam; its GX mapping is covered in `datasources/test_snowflake.py`). Asserts run `succeeded`, both Results persisted, severity derived (null-check → `fail`, row-count → `pass` with observed value). Plus a parametrized **adversarial-frame** run (numpy + pyarrow hostile columns via the shared battery) proving a column-agnostic monitor still runs + persists over a file whose data column is hostile. 7 tests.

**Week 3 total: 18 / 18 ✅ — COMPLETE**

---

## Week 4 — Connection manager UI + check editor UI (frontend)

**Exit gate:** Users can configure any connection type and author checks end-to-end in the UI.

### Frontend tooling coordinated bumps (added — not in original roadmap) (1 task — 1/1 ✅)
- [x] ✅ Vite 8 coordinated bump — `vite` ^6→^8.0.16 + `@vitejs/plugin-react` ^5→^6.0.2 + `vitest` ^3→^4.1.8 + `@vitest/coverage-v8` ^4.1.8 in lockstep — [PR #119](https://github.com/TheurgicDuke771/DataQ/pull/119) (Fixes [#65](https://github.com/TheurgicDuke771/DataQ/issues/65); supersedes Dependabot #111 + closed [#57](https://github.com/TheurgicDuke771/DataQ/pull/57)). plugin-react v6 peers `vite ^8`, vitest v4 peers `vite ^6||^7||^8` — done early to drain the frontend dep backlog; format/lint/typecheck/test/build all green

### Frontend polish from PR-3c review (added — not in original roadmap) (3 tasks — 3/3 ✅)
- [x] ✅ Wrap `MsalProvider` subtree in an antd `AntApp` / React error boundary so render-time failures don't fall back to plain text — `AntApp` already wrapped the tree; added a class `ErrorBoundary` (antd `Result` fallback + Reload) inside it covering everything after first paint — [PR #210](https://github.com/TheurgicDuke771/DataQ/pull/210)
- [x] ✅ Bundle code-splitting — `React.lazy` per route + Suspense + `manualChunks` vendor split (react / antd / msal / vendor) — initial `index` chunk ~1.1 MB → **6.7 kB**; pages load on navigation — [PR #210](https://github.com/TheurgicDuke771/DataQ/pull/210) _(the residual >500 KB chunk is antd itself, now isolated as one long-cache vendor)_
- [x] ✅ Tighten `Settings.model_config` `extra="ignore"` → `"forbid"` (the [PR #39 nit](https://github.com/TheurgicDuke771/DataQ/pull/39)) — unblocked by splitting the env files ([#209](https://github.com/TheurgicDuke771/DataQ/issues/209)): root `.env` now carries infra/compose-only keys (`POSTGRES_*`, `VITE_*`), app config moved to a new `.env.app` (the only file Settings reads + the api/worker/migrate `env_file`), `.env.app.example` template + `setup.sh`/`docker-compose.yml` repointed. `forbid` now catches typo'd/stale keys without the infra keys crashing startup

### Connection manager UI (6 tasks — 6/6 ✅)
- [x] ✅ Connection cards — Snowflake (3 envs), ADF, ADLS/S3, Databricks sections with status badges — grouped-by-type cards with env tag + credential badge + Test action — [PR #191](https://github.com/TheurgicDuke771/DataQ/pull/191)
- [x] ✅ Add connection drawer — type-specific form fields per connection type — spec-driven `ConnectionTypeFields` from `CONNECTION_FORM_SPECS` (all six types; Snowflake password/key-pair auth toggle) — [PR #196](https://github.com/TheurgicDuke771/DataQ/pull/196) (+ Snowflake key-pair backend [PR #193](https://github.com/TheurgicDuke771/DataQ/pull/193))
- [x] ✅ Connection health page — bulk test, live status, re-auth surface — "Test all" header action tests every connection concurrently; each card shows a live health badge (testing / healthy / unreachable) and surfaces an inline **Re-authenticate** link on failure — [PR #208](https://github.com/TheurgicDuke771/DataQ/pull/208) _(folded into the connections list rather than a duplicate route)_
- [x] ✅ Connection re-auth UI — surface expired tokens, inline refresh action — `ReauthModal` (single-secret rotate+verify) + edit drawer + delete via the card actions menu — [PR #198](https://github.com/TheurgicDuke771/DataQ/pull/198)
- [x] ✅ ADLS/S3 connection form — account URL, SAS toggle — covered by the spec-driven drawer ([PR #196](https://github.com/TheurgicDuke771/DataQ/pull/196)) _(container browser + managed-identity/IAM-role modes deferred with the backend, ADR 0010/0011)_
- [x] ✅ Databricks connection form — workspace URL, PAT, warehouse id — covered by the spec-driven drawer ([PR #196](https://github.com/TheurgicDuke771/DataQ/pull/196)) _(live SQL-Warehouse picker deferred; warehouse id is a text field)_

### Check editor UI (9 tasks — 9/9 ✅)
- [x] ✅ Suite list + detail two-panel layout, environment badge on each suite — selectable suites list ←→ detail (connection chip + env tag + checks) — [PR #200](https://github.com/TheurgicDuke771/DataQ/pull/200)
- [x] ✅ Form-based check editor — catalog-driven expectation picker + dynamic typed config fields + create/edit/delete — `expectationCatalog.ts` + `CheckDrawer` ([PR #203](https://github.com/TheurgicDuke771/DataQ/pull/203)). GX expectations are datasource-agnostic in v1, so one catalog serves all four types
- [x] ✅ Flat file check editor — file path + format selector — the datasource-shaped **run-target editor** on the suite drawer ([PR #246](https://github.com/TheurgicDuke771/DataQ/pull/246)) authors a flat-file target (`path` + optional CSV/Parquet `file_format`), so ADLS/S3 suites become runnable; the generic catalog editor ([PR #203](https://github.com/TheurgicDuke771/DataQ/pull/203)) already covers flat-file expectations. _(Container/regex **batch-resolution** inputs remain a post-v1 polish — needs the flat-file listing surface.)_
- [x] ✅ Unity Catalog check editor — catalog / schema / table inputs — the run-target editor ([PR #246](https://github.com/TheurgicDuke771/DataQ/pull/246)) authors a UC target (`catalog` + `table` + optional `schema`), making UC suites runnable; expectations covered by the generic catalog editor. _(A **cascading enumerated** 3-level picker is optional polish — blocked on a Databricks catalog-listing API.)_
- [x] ✅ **Monaco SQL editor for custom check** — a "Custom SQL" category authoring a GX `UnexpectedRowsExpectation` (ADR 0019): a lazy-loaded, locally-bundled Monaco editor (`SqlEditorField`, its own `monaco` Vite chunk so the eager bundle is untouched) + a client-side mirror of the backend read-only/single-statement guardrail (`customSql.ts`) for inline feedback. Gated to SQL datasources (Snowflake / Unity Catalog) in both the create page and the edit drawer via `expectationsByCategoryFor(connectionType)`. _(backend unblocked by ADR 0019 / [PR #258](https://github.com/TheurgicDuke771/DataQ/pull/258); Snowflake keyword autocomplete deferred — Monaco ships SQL syntax highlighting only)_
- [x] ✅ Column profiler panel — inline in check editor, loads on table / file selection — `ColumnProfilePanel` (shared by the create page `CheckNew` + the edit drawer `CheckDrawer`): a collapsed-by-default panel that profiles one column of the suite's run target (#215) via `POST /suites/{id}/profile` (no persistence) and renders null count/fraction, distinct count, min/max, and top values — so the author can ground a range / allowed-set / null threshold in real data before saving. The profiled column **pre-fills from the check's `column` config field** (render-phase prop-sync, like `DryRunPreview`) but stays editable to explore neighbouring columns; `table`/`schema`/`catalog` (SQL) or `path`/`file_format` (flat file) are pulled from the suite target. Disabled with a reason until the suite has a table/file target + a column is entered; backend limits (no credential / unreachable / unsupported type) surface as a clean error alert. 4 vitest (disabled-states / pre-filled-profile with request-shape assertion / error). _(PR-D2; `ColumnProfilePanel.tsx` + `api/suites.ts` `profileColumns`)_
- [x] ✅ **Check dry-run button** — preview pass/fail inline before saving — `DryRunPreview` component (shared by the create page `CheckNew` + the edit drawer `CheckDrawer`): runs the in-progress check against the suite's live target via `POST /suites/{id}/checks/dryrun` and renders the severity outcome (status tag + `metric_value` + observed/expected), **no persistence**. Reuses `buildCheckPayload` (so the preview runs exactly the saved check's config/thresholds) + the severity colour map; `table`/`schema` come from the suite's run target (#215, fetched via new `getSuite`). Disabled with a reason until an expectation is picked + the suite has a table target; backend limits (Snowflake-only, no-credential/unreachable) surface as a clean error alert (dev fail-softs to a 502 with fake creds). 4 vitest (disabled-states / success-outcome with request-shape assertion / error) + an e2e button-enabled assertion. _(PR-D1; `DryRunPreview.tsx` + `api/suites.ts` `getSuite`/`dryRunCheck`)_
- [x] ✅ **Check version history drawer — see previous config before overwriting** — a `check_versions` table snapshots a check's editable state (name/expectation/config/thresholds + author) on create, on every *real* update (no-op PATCHes skipped via `session.is_modified`), and per imported check; `GET /suites/{id}/checks/{cid}/versions` (view-gated, newest-first, author resolved server-side). The **History** button on `CheckDrawer` opens `CheckHistoryDrawer` — newest-first list, latest tagged **Current**, each row showing expectation label + pretty-printed config + `Warn ≥/Fail ≥/Critical ≥` thresholds + author/timestamp. View-only; **restore deferred to [#283](https://github.com/TheurgicDuke771/DataQ/issues/283)**. Backend migration verified up/down; 7 backend + 6 frontend tests. _(#280; backend [PR #281](https://github.com/TheurgicDuke771/DataQ/pull/281) + frontend [PR #282](https://github.com/TheurgicDuke771/DataQ/pull/282); ADR 0019 custom-SQL configs stay readable via pretty-print)_
- [x] ✅ Severity tier toggle in check editor — three-threshold UI — optional warn/fail/critical inputs banding the unexpected-% (ADR 0016), blank = binary — [PR #203](https://github.com/TheurgicDuke771/DataQ/pull/203)

### Access & admin UI (3 tasks — 2/3)
- [x] ✅ Suite sharing panel — add / remove users, assign roles inline — **Share** button on the suite detail opens `SharePanel` (drawer): lists collaborators by name (from the enriched `ShareRead`) and, for owner/admin, adds/changes/removes them. Add picker is a debounced `GET /users/search` type-ahead filtering out the owner + already-shared users; permission is the view/edit/admin ladder. Gated on **`SuiteRead.my_permission`** — a new field stamping the caller's effective level (owner/admin/edit/view), populated on create/get/update + batched across the list via `suite_authz.effective_permissions` (one shares `IN`-query, no N+1) — so a non-owner admin gets the manage controls and a viewer a read-only list. Backend enablers landed first: **PR-E2** (`GET /users/search` behind a FastAPI-free `user_service` — case-insensitive email/display_name substring, 2-char min, limit≤50, LIKE-escaped — + `ShareRead` enriched with `email`/`display_name` via a joined read-only `Share.user`). 6 component tests (list-by-name, empty, search→grant with owner/existing filtered, change, revoke, read-only non-admin) + backend `my_permission` + `/users/search` tests. _(PR-E2 + PR-E3)_
- [ ] ⬜ Admin page — list all suites, all users, access overview
- [x] ✅ Suite export / import UI (download JSON, upload JSON) — **Export** button on the suite detail header downloads `<suite>.json` from `GET /suites/{id}/export` via a transient-anchor blob (`utils/download.ts` — `downloadJson` + `toFilenameStem`); **Import** button on the suites header opens `ImportSuiteDrawer` — drag/drop a `.json`, client-side parse + shape-check (fail-fast on non-suite/malformed JSON), pick a target connection, `POST /suites/import`. The parsed document is handed back **untouched** so thresholds round-trip exactly (string-or-number Decimal encoding never coerced). 11 tests (download lib: stem slug + blob/anchor/revoke; drawer: valid→import-with-faithful-document, non-suite/malformed-JSON rejection, connection-gated Import). _(PR-E1; backend ready from Week 3)_

### GX-Cloud-style UI redesign (added — not in original roadmap) (4 tasks — 4/4 ✅)
> Model the UI on [GX Cloud](https://greatexpectations.io/gx-cloud/): dedicated **full-page create flows** that **classify** what you're creating, keeping the lighter drawers for *edit*. Plan: `~/.claude/plans/fancy-moseying-raccoon.md`. Decided: in-app results page (Grafana deferred to an optional post-v1 ops add-on, never the per-suite authz path — ADR 0018 pending); full-page create + drawer edit; connections/checks redesign now (backends ready), results after the Week-5 run-enablement backend.
- [x] ✅ **Connections — dedicated, classified add page** — `/connections/new` splits the type picker into **Data sources** vs **Orchestration** sections (CLAUDE.md §4), then the spec-driven form; the list page is grouped the same way. `CONNECTION_KIND` is the single source for the split. Edit/re-auth/delete stay on the drawer. _(PR-A1; `ConnectionNew.tsx` + sectioned `Connections.tsx`)_
- [x] ✅ **Connections — `ConnectionDrawer` trimmed to edit-only** — create now lives on the page; the drawer drops the type/env Selects + secret + create branch, shows type/env read-only, and the list owns a simpler `editing: Connection | null` state. _(PR-A2)_
- [x] ✅ **Checks — suite selection as a route + categorized expectation picker** — `/suites/:suiteId` makes the selected suite deep-linkable and survives the check-editor round-trip; the catalog gains a `category` (Column values / Table shape) and the picker groups by it (antd optgroups). Reserved Freshness/Volume/Schema-drift categories (ADR 0012) land with the dedicated page. _(PR-B1)_
- [x] ✅ **Checks — dedicated `/suites/:suiteId/checks/new` page** — category → expectation → config/thresholds, with reserved monitor-kind categories (Freshness/Volume/Schema-drift, ADR 0012) shown disabled. Shared form logic extracted to `checkForm.ts` (conversions) + `checkFormFields.tsx` (field components), reused by the page and the now edit-only `CheckDrawer`. The natural home for the [#215](https://github.com/TheurgicDuke771/DataQ/issues/215) target table/path field. _(PR-B2; `CheckNew.tsx`)_

**Week 4 total: 25 / 26 ✅** _(connection manager UI complete (6/6); check editor core shipped + **dry-run preview** (PR-D1) + **column profiler panel** (PR-D2) + the datasource-shaped **run-target editor** (PR #246 — closes the flat-file + Unity Catalog target inputs, making suites runnable); **check version-history drawer** (#280; backend #281 + frontend #282 — view-only, restore deferred to #283); **suite export/import UI** (PR-E1); **suite sharing panel** (PR-E2 backend + PR-E3 UI, gated on `SuiteRead.my_permission`); **GX-Cloud redesign complete (4/4)** — classified connection + check create pages, edit-only drawers, suite-route/categorized picker (PR-A1/A2/B1/B2); PR-3c polish 3/3 (Settings `extra="forbid"` shipped via the `.env`/`.env.app` split, #209); **Monaco custom-SQL editor** (ADR 0019 — custom-SQL as a GX `UnexpectedRowsExpectation`; backend #258 + frontend #259) — only the **admin page** (backend-blocked on a list-all endpoint) remains)_

---

## Week 5 — Execution engine + scheduling

**Exit gate:** Async runs with live progress across all datasource types; scheduling operational.

### Async execution backend (9 tasks — 4/9 ✅, early)
- [x] ✅ Celery + Redis background task runner for GX scan execution — `run_suite` task + `run_service` — landed early via [PR 4a](https://github.com/TheurgicDuke771/DataQ/pull/74) + [PR 4c-i](https://github.com/TheurgicDuke771/DataQ/pull/78) + [PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)
- [x] ✅ Generalise `run_suite` worker dispatch — select the `CheckRunner` by `connection.type` via the runner registry (`build_check_runner`), replacing the Snowflake-hardcoded wiring in `worker/tasks.py`; the seam that makes the flat-file / UC run paths route correctly and post-v1 RDBMS adapters a drop-in — [PR #146-fix](https://github.com/TheurgicDuke771/DataQ/issues/146) _(added per [ADR 0011](adr/0011-extensibility-seams-for-deferred-integrations.md); profiler dispatch collapsed onto a parallel registry in the same effort, [#147](https://github.com/TheurgicDuke771/DataQ/issues/147))_
- [x] ✅ **Check target table/path** — modeled as a **per-suite** datasource-shaped `Suite.target` (JSONB, shaped like the column-profiler request: `table`/`schema`/`catalog`/`path`/`file_format`), since `execute_run` runs all of a suite's checks against one batch (the GX data-asset-per-suite shape). `services/run_target.resolve_target` resolves it per connection type to the runner's `(table, schema, catalog)` triple (flat-file path rides the table-shaped `CheckRunner`'s `table` slot); `validate_target` is the write-time 422 guard on suite create/update. Migration `b1f2c3d4e5a6` (additive nullable column, tested up/down on Postgres). **Dispatch ungated**: `_trigger_suites` now hands each created run to `run_suite.delay` (lazy import — worker↔service cycle), broker-fail marks the run `failed`; `run_suite` resolves the target from the suite (targetless → clean `failed`), so the worker no longer takes a `table` arg; stale `until_week3` wording removed. 13 resolver units + worker/orchestration/suite-API target tests. _(Re-tracked from Week 3; [#215](https://github.com/TheurgicDuke771/DataQ/issues/215); PR-C0a)_
- [x] ✅ **Manual run trigger + run/result read API** — `POST /suites/{id}/run` queues a `Run` and dispatches it via the shared `run_dispatch.dispatch_run` helper (**edit**-gated — the capability ladder grants "trigger runs" at edit); the suite target (#215) is resolved **up front** so a targetless/misconfigured suite fails fast with 422 instead of a queued→failed run, and a broker outage marks the run `failed` + returns 503 (same contract as the probe). New `api/v1/runs.py` read surface for the results page: `GET /runs?suite_id=&status=&limit=` and `GET /runs/{id}` (run + joined `Result`s) are **suite-scoped** — the list filters to accessible suites and the detail gates on `require_permission(view)`, so per-suite sharing + existence-hiding hold (exactly why direct-Postgres/Grafana is rejected as the primary surface); `GET /pipeline_runs?provider=&status=` is the orchestration monitoring feed (auth-only, not suite-scoped). Read helpers live in `run_service.list_runs/get_run/list_results` + `orchestration_service.list_pipeline_runs`. 14 DB-backed API tests (trigger happy/targetless-422/edit-403/no-access-404/broker-503; list scoping+filters+limit; detail+results / 404s; pipeline filters + auth). _(Run-enablement gap; PR-C0b; gates the Week-6 Results page PR-C1)_
- [ ] ⬜ Run progress API — poll endpoint returning per-check live status
- [ ] ⬜ Cancel run endpoint — gracefully terminate in-progress Celery task
- [ ] ⬜ Run history retention policy — configurable purge of results older than N days
- [ ] ⬜ Flat file run path — resolve batch, load via Pandas, execute GX suite
- [ ] ⬜ UC run path — submit job to Databricks SQL Warehouse, execute GX suite

### ADF + Airflow hybrid monitoring (webhook + polling fallback) (4 tasks — 2/4 ✅)
- [x] ✅ Celery beat task — poll ADF REST API every 10 min for **succeeded** runs (skip pipelines updated by webhook recently) — provider-agnostic `poll_orchestration_runs` beat task through the `OrchestrationProvider` seam — [PR #171](https://github.com/TheurgicDuke771/DataQ/pull/189)
- [x] ✅ Celery beat task — poll Airflow REST API `dagRuns` every 10 min — same provider-agnostic beat task (ADF + Airflow share it) — [PR #171](https://github.com/TheurgicDuke771/DataQ/pull/189) _(added per ADR 0004; not in roadmap)_
- [ ] ⬜ Gap recovery logic — on startup + every 30 min, fetch last hour of run statuses
- [ ] ⬜ `GET /api/v1/orchestration/pipelines` — latest status per pipeline/DAG, provider-agnostic

> **`trigger_bindings` CRUD** (provider-agnostic suite-run triggers — `provider`/`pipeline_or_dag_id`/`env` → `suite_id`) landed early via [PR #172](https://github.com/TheurgicDuke771/DataQ/pull/190), unblocking the trigger-on-success path skeletoned in Week 2 PR 8.

### Execution UI (5 tasks — 1/5 ✅)
- [ ] ⬜ Run now panel — suite picker, env / datasource, notification target
- [ ] ⬜ Live run progress UI — check-by-check status with spinner + cancel button
- [ ] ⬜ Scheduled runs table — create, pause, delete cron schedules
- [ ] ⬜ Recent runs audit table with drill-down link to results
- [x] ✅ **Suite Triggers panel** — UI over the `trigger_bindings` CRUD (#172): bind/unbind a pipeline/DAG (`provider` + `pipeline_or_dag_id` + `env`) to a suite so it runs on the pipeline's success. **Suite-level**, not a check field (orchestration providers are never a datasource/check attribute, CLAUDE.md §4); inline `TriggersPanel` card on the suite detail — list + enable/disable toggle + remove + add form (ADF/Airflow · pipeline-or-DAG id · env), edit-gated (read-only for viewers). `api/triggerBindings.ts` + 6 component tests. _(PR #247; closes [#216](https://github.com/TheurgicDuke771/DataQ/issues/216))_

**Week 5 total: 7 / 18** _(early credit: Celery task runner in PR 4; worker runner-dispatch #146; ADF + Airflow 10-min polling #171; plus `trigger_bindings` CRUD #172 out-of-band; check target-table + dispatch ungate #215 (PR-C0a); manual run trigger + run/result read API (PR-C0b); **Suite Triggers UI #216 (PR #247)**. +3 tasks re-tracked here: check target-table #215 (slipped from W3), Suite Triggers UI #216, and the run-enablement read API (PR-C0b). The **manual Run button** on the suite detail (PR #245, the UI for the PR-C0b trigger endpoint) + the datasource-shaped **run-target editor** on the suite drawer (PR #246, makes UI-created suites runnable) also shipped this stretch)_

---

## Week 6 — Results dashboard + alerting

**Exit gate:** Full results dashboard live across all source types; alerts firing with suppression.

### Results dashboard (10 tasks — 2/10 ✅)
> **Build as an in-app React page** (the GX-Cloud-style redesign Phase C), **not Grafana** — reading Postgres directly would bypass DataQ's per-suite sharing + PII redaction; Grafana is deferred to an optional post-v1 ops add-on ([ADR 0018](adr/0018-results-surface-and-grafana-deferral.md), accepted). The Week-5 run-enablement backend it gates on is **done**: `runs.py` read endpoints (`GET /runs`, `GET /runs/{id}`, `GET /pipeline_runs`) + `POST /suites/{id}/run` (PR-C0b); [#215](https://github.com/TheurgicDuke771/DataQ/issues/215) target (PR-C0a). The `results.metric_value`/`duration_ms` columns were seam-built for these trend charts (ADR 0012).
- [x] ✅ **Results page scaffold** (PR-C1) — the in-app `/results` page + sidebar nav (Connections · Suites · **Results** · Profile). **Runs tab**: a runs table (suite name, status, triggered-by, started, duration) with a status filter, each row opening a **run-detail drawer** that drills into the per-check results (check name + expectation, severity tag, `metric_value`, observed value). **Pipeline runs tab**: the orchestration monitoring feed (`pipeline_runs`) with a provider filter. Pure presentation helpers (`resultsFormat.ts` — duration/timestamp formatters, status→colour maps) are unit-tested; `api/runs.ts` mirrors the C0b schemas. Demo seed (`demo_data.py`) now lands runs + results (pass/pass/warn/fail spread) + two pipeline-runs + suite targets so the page has real content. 10 vitest + 2 Playwright (`results.spec.ts`). _The rich widgets below build on this scaffold._
- [ ] ⬜ Health score stat cards + 7-day trend chart
- [ ] ⬜ Per-suite pass / fail progress bars — warn / fail / critical breakdown
- [ ] 🟡 Results filter bar — env, datasource type, suite, date range, status — _run **status** filter + pipeline **provider** filter shipped (PR-C1); env/datasource/suite/date-range pending_
- [ ] 🟡 Failed check drill-down — sample failing rows from GX result — _per-check drill-down shipped (PR-C1); **sample rows withheld** pending row-level PII redaction ([#226](https://github.com/TheurgicDuke771/DataQ/issues/226), ADR 0018)_
- [ ] ⬜ Per-check historical trend chart
- [ ] 🟡 Orchestration status panel — pipeline/DAG status, polls every 30s, correlated DQ result — _pipeline-runs feed + status shipped (PR-C1, `/pipeline_runs` tab); 30s auto-poll + DQ-run correlation pending_
- [ ] ⬜ Datasource type filter — Snowflake / flat file / Unity Catalog toggle
- [ ] ⬜ CSV + PDF export of results
- [x] ✅ Severity badge colours — green / amber / red / dark red — severity-tier (pass/warn/fail/critical) + run-status tag colour maps in `resultsFormat.ts`, rendered as antd Tags on the runs table + run detail (PR-C1)
- [ ] ⬜ Health score weighting — apply warn/fail/critical penalty weights

### Alerting (6 tasks — 0/6)
- [ ] ⬜ `ResultPublisher` seam — dispatch run outcomes from the post-`execute_run` completion point through a small publisher interface (Teams is the v1 implementation, not a hardcoded call); carry a PII redaction / opt-in policy on `sample_failures` at the seam since it leaves DataQ's trust boundary. Enables post-v1 TestRail / JIRA / Xray publishers as additional subscribers with no re-plumbing _(added per [ADR 0011](adr/0011-extensibility-seams-for-deferred-integrations.md))_
- [ ] ⬜ Notification config UI — Teams webhook per suite, alert on fail / warn / always
- [ ] ⬜ Alert suppression / snooze — silence a specific check for N hours
- [ ] ⬜ Alert dedup — fire on first failure only, not on every subsequent scheduled run
- [ ] ⬜ Teams adaptive card payload — check, datasource, table / file, observed vs expected
- [ ] ⬜ Severity-aware alert routing — warn quiet, fail standard, critical @channel

### Prototype adoption — dedicated-page flows + Dashboard landing (added — not in original roadmap) (6 tasks — 0/6) — tracked in epic [#175](https://github.com/TheurgicDuke771/DataQ/issues/175)
> Adopt the **DataQ Design System** prototype (claude.ai/design project *"DataQ Design System"*, reverse-engineered from the shipped indigo/antd app — its `readme.md` is the spec). The **foundation already matches** the product (`theme.ts` palette, `resultsFormat.ts` status model, the 56px-header/220px-sider shell), so this is **not a re-skin** — it adopts the prototype's **structure**: every create/edit flow becomes a dedicated, deep-linkable **page** (the **Share** drawer stays the *only* drawer), login lands on an **Enhanced Monitoring Dashboard**, and standard **error pages** ship. Components are reproduced with **real antd** — the prototype's framework-free recreations + `_ds_bundle.js` are reference only (per the design README). Backend-gated screens (Admin, Settings) + post-v1 items (marketing, dark mode) are tracked in Week 7 + Post-v1 below. The intentionally-dropped prototype bits (the **"View as" role switch**, the **component bundle**, the **teal palette**) are recorded in Post-v1 as *not adopted*.
>
> **Design source — how to open the prototype (read before picking up any task in this track):** the claude.ai/design project **"DataQ Design System"** — `projectId` **`317fec67-2b8a-498c-8f6b-523750916a8d`** (owner Arijit). Read it with the **`/design-sync`** skill / **`DesignSync`** tool over the claude.ai login: `DesignSync method=list_files projectId=…`, then `method=get_file path=<…>`. Key paths: **`readme.md`** = the canonical spec (voice · color · type · spacing · status model · component/template manifest); **`guidelines/*.card.html`** = foundation specimen cards; **`templates/app/*.jsx`** = framework-free recreations of each screen; **`components/<group>/*.jsx`** = the component recreations; **`tokens/*.css`** = raw token values; **`uploads/DESIGN.md`** = the *superseded teal* brief (ignore for palette). ⚠️ The hosted share link `https://api.anthropic.com/v1/design/h/SVkQJqee7xsc2FNWVOsnIQ` returns **404** — reach the prototype via DesignSync, not the link. The `.jsx` / `_ds_bundle.js` are **reference only** — build in real antd reading `theme.ts`.
- [ ] ⬜ **Nav / shell restructure** — add **Dashboard** as the landing entry; reorder the sider to **Dashboard · Connections · Suites · Results · Profile**; add the footer group **Settings · Admin · Documentation** (Admin/Settings land in Week 7). Change the login redirect `/` → `/dashboard` (was `/connections`). Touches `App.tsx` `NAV_ITEMS` + `<Routes>` only — the nav already prefix-matches ([#192](https://github.com/TheurgicDuke771/DataQ/issues/192)). _Design ref: `templates/app/AppShell.jsx` (white header + light sider + footer nav) + readme §"What's in here → Templates → `templates/app`" (the sidebar + footer item lists)._
- [ ] ⬜ **Enhanced Monitoring Dashboard page** (`/dashboard`, `DashboardScreen`) — the new landing screen that **hosts the existing Week-6 dashboard widgets** (health-score stat cards, 7-day quality-trend chart, per-suite performance bars, recent-runs table). This is the **page home** for the "Health score stat cards", "Per-suite pass/fail bars" and "Per-check historical trend chart" tasks above — build them here rather than on `/results`. **Prereq:** a chart-library decision (antd ships no charts → `@ant-design/charts` or `recharts`; runs through `pnpm audit` + bundle-size gate → ADR-light note). Render only backend-backed KPIs (see the W7 *KPI honesty pass*). _Design ref: `templates/app/DashboardScreen.jsx`; components `data/MetricCard.jsx`, `data/Card.jsx`, `feedback/ProgressBar.jsx`, `data/DataTable.jsx`; readme §"Templates" (Enhanced Monitoring — KPIs · Quality Trends · Suite Performance · Recent Runs)._
- [ ] ⬜ **New Suite → dedicated page** (`/suites/new`, `SuiteFlow`) — extract the `SuiteDrawer` **create** path into a routed *details / source* page reusing `suiteTarget.ts` + the run-target editor (PR #246). Removes the drawer's create branch; `Suites.tsx` swaps `setDrawer({open:true})` for `navigate('/suites/new')`. _Design ref: `templates/app/SuiteFlow.jsx` (New Suite → Details/Source) → `CheckFlow.jsx` (Define Rules); readme §"Templates" full-page-flows note._
- [ ] ⬜ **Edit flows → dedicated pages** — unify edit onto the existing create pages via an `:id` route + prefill, reusing the already-extracted form specs (`connectionFormSpec.ts` / `checkForm.ts` / `suiteTarget.ts`): connection edit `/connections/:id/edit` → `ConnectionNew`; suite edit `/suites/:id/edit` → `SuiteFlow`; check edit `/suites/:id/checks/:checkId/edit` → `CheckNew`. **Removes** the `ConnectionDrawer` / `CheckDrawer` / `SuiteDrawer` **edit** drawers; the parent pages drop their `editing` / `drawer` state for `navigate()`. (Dedups with [#204](https://github.com/TheurgicDuke771/DataQ/issues/204).) _Design ref: `templates/app/ConnectionFlow.jsx` (Configuration), `SuiteFlow.jsx`, `CheckFlow.jsx` — the create flows; edit reuses the same page shape, prefilled._
- [ ] ⬜ **Results drill-down → dedicated route** (`/results/:runId`, `ResultsScreen` detail) — promote the in-page `openRun` `useState` ([Results.tsx:54](../frontend/src/pages/Results.tsx#L54)) to a routed run-detail page with back-nav, per-check results (severity tags + `metric_value` + observed), and CSV/JSON download. Deep-linkable + refreshable (the prototype's `ResultsScreen` flow). _Design ref: `templates/app/ResultsScreen.jsx`; readme §"Templates" (Results → click a run → Run detail with per-check results + CSV/JSON download)._
- [ ] ⬜ **Standard error pages (4xx / 5xx)** — an `ErrorState`-based page in the app shell switchable across 400 / 401 / 403 / 404 / 429 / 500 / 502 / 503 / 504; wire it into the router catch-all + the `ErrorBoundary` ([PR #210](https://github.com/TheurgicDuke771/DataQ/pull/210)) fallback so render-time + routing failures render in-brand. _Design ref: `templates/error/index.html` + `components/feedback/ErrorState.jsx` (+ `components/feedback/errorstate.card.html`); readme §"Templates → `templates/error/`"._

**Week 6 total: 2 / 22** _(early credit: Results page scaffold + severity badge colours (PR-C1, the GX-Cloud redesign Phase C); 3 dashboard tasks partially landed — status/provider filters, per-check drill-down, pipeline-runs feed. +6 prototype-adoption tasks added: nav/shell, Dashboard landing page, suite-create page, edit-flows→pages, results drill-down route, error pages.)_

---

## Week 7 — Deployment, hardening & docs

**Exit gate:** Production-ready v1 deployed to Azure, CI/CD live, team onboarded.

### DevOps & deployment (6 tasks — 0/6, 1 partial early)
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

### Hardening & docs (6 tasks — 0/6 + 1 🟡)
- [ ] 🟡 E2E test coverage for critical paths — full-stack E2E landed early ([#128](https://github.com/TheurgicDuke771/DataQ/issues/128)): API smoke (`backend/scripts/e2e_smoke.py`, 12/12) + browser smoke (`frontend/e2e/`, Playwright, 6/6, CI `frontend-e2e` job) cover auth/dev-bypass + the read + authoring paths. **Live run paths (Snowflake/flat-file/UC) still pending the W5 execution path + #215.**
- [ ] ⬜ Error handling audit — consistent error shapes across all endpoints
- [ ] ⬜ Ensure all FastAPI endpoints have `summary`, `description`, `tags`, `response_model`
- [ ] ⬜ README + deployment guide + env-var reference
- [ ] ⬜ Team onboarding session + feedback collection
- [ ] ⬜ Document MCP connection config (Claude Desktop / Claude.ai / Copilot / Cursor) in README

### Prototype adoption — backend-gated screens + final polish (added — not in original roadmap) (5 tasks — 0/5) — tracked in epic [#176](https://github.com/TheurgicDuke771/DataQ/issues/176)
> Closes out the prototype after the Week-6 structural pass: the two screens that need backend (**Admin**, **Settings**) plus the final visual-fidelity pass to the design-system guidelines. Admin needs a real **workspace-admin** authz concept (today `admin` is per-suite only) — the prototype's client-side "View as" switch is **not** used (real authz is server-side, CLAUDE.md §11). **To open the prototype, see the *Design source* note in the Week-6 prototype-adoption group** (project `317fec67-2b8a-498c-8f6b-523750916a8d` via the `DesignSync` tool).
- [ ] ⬜ **Backend: workspace-admin list-all endpoint** — `GET /admin/suites` · `/admin/users` · `/admin/access` (all-suites / all-users / access overview) behind a new **workspace-admin** authz check (distinct from the per-suite `view < edit < admin < owner` ladder in `suite_authz`). Backward-compatible additive surface; the long-standing **backend block** for the Admin page. _Unblocks the Week-4 ⬜ "Admin page" task._ _Design ref: `templates/app/AdminScreen.jsx` (the columns / overview data the page consumes — defines the endpoint's response shape)._
- [ ] ⬜ **Admin control centre page** (`/admin`, `AdminScreen`) — all suites / users / access overview on the new endpoint, with a **real 403** for non-admins (server-driven, via the workspace-admin check — *not* a client "View as" toggle). _Closes the Week-4 "Admin page — list all suites, all users, access overview" task._ _Design ref: `templates/app/AdminScreen.jsx`; readme §"Templates" ("a demo 'View as' role switch … gates Admin behind a 403 for non-admins" — adopt the 403 page, drop the demo switch)._
- [ ] ⬜ **Workspace Settings page** (`/settings`, `SettingsScreen`) — the app-shell settings screen. **No settings/preferences backend exists today** (only the `v1/` routers), so v1 ships the page shell + only controls that map to a real backend (e.g. Teams alert webhook config once the Week-6 `ResultPublisher` notification-config UI lands); everything else renders as a clearly-labelled placeholder until a settings API exists. Backend-gated. _Design ref: `templates/app/SettingsScreen.jsx`._
- [ ] ⬜ **Dashboard KPI honesty pass** — the Dashboard renders only metrics with a real data model: pass-rate, run counts, and `metric_value`-backed trends (ADR 0012 seam). **Omit "anomalies detected"** (the `anomaly` monitor-kind is reserved, not implemented in v1) and **"avg. time to resolution"** (there is no incident/resolution model). Re-introduce when the monitor kinds / model exist (post-v1 Theme A). _Design ref: `templates/app/DashboardScreen.jsx` (the KPI card row — keep pass-rate / runs / trends, drop the two unbacked KPIs)._
- [ ] ⬜ **Visual-fidelity pass to the design spec** — self-hosted **Inter** + **JetBrains Mono** `@font-face` (if not already wired), motion tokens (120–180ms ease on bg/border/shadow, 0.5px press nudge), the 2px indigo focus ring, hover elevation on interactive cards, and empty/loading states — matched to the prototype's `guidelines/*` foundation cards. Keep using **semantic tokens** so post-v1 dark mode inherits for free. _Design ref: `guidelines/{type-body,type-mono,color-indigo,color-neutrals,color-status,spacing-scale,elevation,radius}.card.html` + `tokens/{fonts,typography,elevation,spacing,colors}.css` + `assets/fonts/`; readme §"Visual foundations" / "Motion" / "Focus" / "Iconography"._

**Week 7 total: 0 / 34** _(+5 prototype-adoption tasks: workspace-admin endpoint, Admin page, Settings page, Dashboard KPI honesty pass, visual-fidelity pass)_

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

### Frontend unit tests (Vitest + RTL) (8 tasks — 1/8 ✅)
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
- [ ] 🟡 Test data fixtures — sample suites, check results, run histories — `backend/scripts/demo_data.py` seeds a representative dataset (all six connection types + 3 suites with varied checks + a cross-user share) through the real service layer, wired into `seed_dev`; **run histories still pending** (need the W5 execution path). Paired with `backend/scripts/e2e_smoke.py` (full-stack API E2E — see below)

**Week 8 total: ~4 / 26 (early credit from per-slice tests in W1 — overall coverage ~91% backend; frontend ~80% lines)**

> **Full-stack E2E ([#128](https://github.com/TheurgicDuke771/DataQ/issues/128) — both halves landed):**
> - **API half** — `backend/scripts/e2e_smoke.py` drives the **real** running stack (docker compose + dev-bypass + the demo seed) with no mocks: asserts the six connection types list (secrets never returned), suites + checks read back, severity thresholds round-trip, the create→add-check→dry-run→delete authoring loop, and dry-run failing soft (502/422, not a crash). **12/12 pass.**
> - **Browser half** — `frontend/e2e/` (Playwright) drives the **React UI** the same way a user does (`browser → Vite proxy → api → Postgres`): app-shell loads under dev-bypass, sider nav, seeded connections grouped by type + "Test all" health path, seeded suite → its checks, and a full create-suite → add → delete authoring round-trip. **6/6 pass.** Wired into CI as the `frontend-e2e` job (Postgres + Redis services, migrate + seed, uvicorn on :8000, Playwright starts its own Vite server). See [frontend/e2e/README.md](../frontend/e2e/README.md).
>
> Live datasource `test()`/runs remain the deferred smoke (no real creds — connectivity fails-soft).

---

## Aggregate

| Week | Done | In progress | Pending | Total |
|---|---|---|---|---|
| Week 1 | 7 | 3 | 0 | 10 |
| Week 2 | 15 | 1 | 3 | 19 |
| Week 3 | 18 | 0 | 0 | 18 |
| Week 4 | 25 | 0 | 1 | 26 |
| Week 5 | 7 | 0 | 11 | 18 |
| Week 6 | 2 | 3 | 17 | 22 |
| Week 7 | 0 | 2 | 32 | 34 |
| Week 8 | 2 | 9 | 19 | 30 |
| **TOTAL** | **76** | **18** | **83** | **177** |

> Post-v1 items (marketing landing page, dark mode) are tracked in their own section below and are **excluded** from this weekly aggregate.

> 161 > 100 because ADR 0004 added Airflow tasks, ADR 0011 added two seam tasks (generic runner dispatch, `ResultPublisher`), ADR 0012 added three Week-3 monitor-kind / metric seam tasks, the W5 run-enablement gaps surfaced in review (check target-table #215, Suite Triggers UI #216), the GX-Cloud-style UI redesign added four UI-shape tasks (dedicated/classified connection + check pages), the **DataQ Design System prototype adoption** added eleven tasks (six Week-6 dedicated-page-flow + Dashboard-landing tasks, five Week-7 backend-gated screens + polish), plus PR-review follow-ups not in the original roadmap. Tracked here for honesty.

---

## Post-v1 — deferred prototype items

From the **DataQ Design System** prototype, but out of v1 scope. Not counted in the weekly aggregate. **To open the prototype, see the *Design source* note in the Week-6 prototype-adoption group** (project `317fec67-2b8a-498c-8f6b-523750916a8d` via the `DesignSync` tool).

- [ ] ⬜ **Marketing landing page** (`templates/marketing/`) — sticky nav, hero (live product preview), features bento, social-proof strip, dark CTA closer, footer. **Build when DataQ goes multi-tenant** — single-tenant v1 is an Azure-AD-gated console with no public/unauthenticated surface, so a marketing site has no home yet. Reproduce with real antd (or a static marketing stack) at that point. _Design ref: `templates/marketing/{MarketingNav,Hero,Features,MarketingClose}.jsx` + `templates/marketing/index.html`; readme §"Templates → `templates/marketing/`"._
- [ ] ⬜ **Dark mode** — ship the `[data-theme="dark"]` semantic remap (deep-slate surfaces, lifted status hues for AA contrast) + an opt-in toggle (optional `prefers-color-scheme` auto-switch). **Architecturally ready** — v1 components already reference semantic tokens, so the flip is "zero component changes" *iff* the Week-7 visual-fidelity pass keeps that discipline. v1 ships light-only by decision. _Design ref: `tokens/dark.css` (the `[data-theme="dark"]` scope) + `guidelines/dark-theme.card.html`; readme §"Dark mode (planned post-v1)"._

**Not adopted (intentional, per design review):** the prototype's **"View as" role switch** (real authz is server-side — Azure AD + per-suite sharing; CLAUDE.md §11), its **framework-free component code + `_ds_bundle.js` / `window.*` globals** (production uses real antd per the design README), and the **teal "Enterprise Monitor" palette** (the shipped indigo theme stands; the design system itself already superseded the teal `uploads/DESIGN.md` direction).

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
| ~~[#129](https://github.com/TheurgicDuke771/DataQ/issues/129)~~ | ~~Snowflake connector 3→4 + Python 3.11→3.13 CVE refresh~~ | **Closed** ([#178](https://github.com/TheurgicDuke771/DataQ/pull/178), ADR 0017) | n/a — cleared 5 CVEs |
| ~~[#146](https://github.com/TheurgicDuke771/DataQ/issues/146)~~ | ~~Worker hardcodes `build_snowflake_runner` — dispatch `CheckRunner` by `connection.type`~~ | **Closed** (#165 + profiler registry) | n/a — runner + profiler registries |
| ~~[#201](https://github.com/TheurgicDuke771/DataQ/issues/201)~~ | ~~Undeclared direct dependency: pyarrow (Parquet flat-file relied on a transitive)~~ | **Closed** ([PR #202](https://github.com/TheurgicDuke771/DataQ/pull/202)) | n/a — unblocked databricks 4.x bump |
| ~~[#213](https://github.com/TheurgicDuke771/DataQ/issues/213)~~ | ~~compose frontend service never receives `VITE_*` vars → dev-bypass unreachable in the browser~~ | **Closed** (this PR) | n/a — `docker compose up` now reaches the UI under dev-bypass |
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
