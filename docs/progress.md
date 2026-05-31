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
| **Today** | 2026-05-30 |
| **Calendar burn** | day 7 of 56 (~13%) |
| **Roadmap tasks done** | 10 ✅ + 2 🟡 / 152 (7%) |
| **Out-of-roadmap PRs landed** | 5 bundles (governance, tooling lock, Entire CLI, Dependabot triage round 1, PR-3 cleanup) + ADRs 0006/0007 (orchestration auth) |
| **Current week** | Week 2 — Connection manager (backend) |
| **Week-1 exit gate** | A logged-in user can hit a FastAPI endpoint that triggers GX against Snowflake DEV and persists a result row. — **met** (plumbing complete via PR 4a–4c; live-Snowflake run fails-soft pending DEV creds — deferred smoke) |
| **Next milestone** | PR 6 — ADF connection CRUD + `(type, env)` uniqueness guard (#72) (Week 2) |
| **Open issues** | 12 (#72 closed by ADR-0004 doc PR #83) |
| **Open PRs** | PR 5 (#85 — Snowflake connection CRUD) |

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

### Snowflake & ADF (3 tasks — 2/3)
- [x] ✅ API: CRUD for Snowflake connections (DEV / QA / UAT), connection test endpoint — [PR 5](https://github.com/TheurgicDuke771/DataQ/pull/85) _(also introduced the `ConnectionAdapter` seam + registry per [ADR 0011](adr/0011-extensibility-seams-for-deferred-integrations.md), and `SecretStore.set` write-through — so PRs 6-8 are pure adapter additions)_
- [x] ✅ API: CRUD for ADF connections (subscription ID + service principal) — PR 6 _(`ADFConnectionAdapter` in the new `orchestration/` package — NOT `datasources/`, per CLAUDE.md §4; `test()` does SP token + factory GET via httpx. Enforces `(type, env)` uniqueness for orchestrator rows via a **partial unique index** `WHERE type IN ('adf','airflow')` per [#72](https://github.com/TheurgicDuke771/DataQ/issues/72) / ADR 0004 — datasources excluded, so Snowflake stays many-per-env. CRUD/API reused unchanged: pure adapter + registry + migration addition.)_
- [ ] ⬜ Connection re-auth endpoint — refresh expired Key Vault token
- [ ] ⬜ Review `connections.secret_ref` nullability — decide based on Airflow basic-poll / unauthenticated S3 cases ([PR #41 nit](https://github.com/TheurgicDuke771/DataQ/pull/41))

### ADF webhook receiver (Azure Monitor → DQ platform) (5 tasks — 0/5)
- [ ] ⬜ `POST /api/v1/orchestration/events/adf` — receive Azure Monitor payload, validate shared secret, return 200 _(path differs from roadmap per ADR 0004 — uses unified `OrchestrationProvider` endpoint)_
- [ ] ⬜ Parse webhook payload — extract `pipelineName`, `factoryName`, `runId`, `firedDateTime`
- [ ] ⬜ Follow-up ADF REST API call on webhook receipt — fetch run details
- [ ] ⬜ Upsert pipeline run status into `pipeline_runs`; correlate with suite run
- [ ] ⬜ Shared secret config in Key Vault → `ADF_WEBHOOK_SECRET` env var

### Airflow orchestration (added per ADR 0004; not in original roadmap) (3 tasks — 0/3)
- [ ] ⬜ `POST /api/v1/orchestration/events/airflow` — receive HMAC-signed callback payload
- [ ] ⬜ Airflow `on_success_callback` / `on_failure_callback` helper snippet for users' DAGs
- [ ] ⬜ Airflow connection type — webserver URL + auth (token-based, v1 default)

### Flat file — ADLS Gen2 & S3 (4 tasks — 0/4)
- [ ] ⬜ API: CRUD for ADLS Gen2 connections (account URL + managed identity / SAS)
- [ ] ⬜ API: CRUD for S3 connections (bucket + IAM role / access key)
- [ ] ⬜ GX `pandas_abs` / `pandas_s3` datasource wiring — connect, list containers, list files
- [ ] ⬜ File asset config model: container, batching regex, file format (CSV / Parquet / JSON)

### Unity Catalog / Databricks (3 tasks — 0/3)
- [ ] ⬜ API: CRUD for Databricks connection (workspace URL + PAT token + SQL Warehouse ID)
- [ ] ⬜ GX Spark / JDBC datasource wiring for Unity Catalog — connect, list catalogs / schemas / tables
- [ ] ⬜ UC auth test endpoint — validate PAT + SQL Warehouse reachability

**Week 2 total: 2 / 19**

---

## Week 3 — Suite & check API — all datasource types (backend)

**Exit gate:** Full check CRUD API across Snowflake, flat files and Unity Catalog; column profiler live.

### Suite & check backend (4 tasks — 0/4)
- [ ] ⬜ API: CRUD for suites and GX expectations (Snowflake path)
- [ ] ⬜ API: suite sharing — assign users with owner / editor / viewer roles
- [ ] ⬜ API: suite export to JSON + import from JSON
- [ ] ⬜ API: check dry-run endpoint — validate against live data, return preview result

### Severity threshold tiers (warn / fail / critical) (4 tasks — 0/4)
> **Day 1 design decision: severity weights — agree before schema migration. ADR 0005 pending.**
- [ ] ⬜ Add `warn_threshold`, `fail_threshold`, `critical_threshold` fields to check model
- [ ] ⬜ Alembic migration — threshold columns + `status` enum (`pass`, `warn`, `fail`, `critical`) **+ the monitor-kind / metric columns below (one migration)**
- [ ] ⬜ Post-processing in GX result handler — derive `warn` / `fail` / `critical` from observed value
- [ ] ⬜ Update check CRUD + run result response schemas with threshold fields + status values

### Monitor abstraction & metric storage — do-now seams (3 tasks — 0/3)
> **Day 1 design decision: `check.kind` discriminator + numeric metric storage — decide before the threshold migration; rides the same migration. ADR 0012 pending.** Keeps v1.x auto-monitors (freshness / volume / schema-drift / anomaly — post-v1 Theme A) from forcing a check/result schema rewrite. v1 implements `expectation` only.
- [ ] ⬜ Add `kind` discriminator to check model (`'expectation'` default; `freshness`/`volume`/`schema_drift`/`anomaly` reserved)
- [ ] ⬜ Generalise run path to dispatch by `check.kind` (`expectation` → GX `CheckRunner`; others raise `NotImplementedError`)
- [ ] ⬜ Add `metric_value` (NUMERIC) + `duration_ms` (INT) to results — SQL-aggregatable metric for Week-6 trends + v1.1 anomaly; per-check runtime for cost surface

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

**Week 3 total: 0 / 18**

---

## Week 4 — Connection manager UI + check editor UI (frontend)

**Exit gate:** Users can configure any connection type and author checks end-to-end in the UI.

### Frontend tooling coordinated bumps (added — not in original roadmap) (1 task — 0/1)
- [ ] ⬜ Vite 8 coordinated bump — `vite` + `@vitejs/plugin-react` + `vitest` in lockstep ([#65](https://github.com/TheurgicDuke771/DataQ/issues/65); supersedes closed [#57](https://github.com/TheurgicDuke771/DataQ/pull/57))

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

**Week 4 total: 0 / 22**

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
- [ ] ⬜ Action Group (nonprod) — webhook to nonprod API URL, shared secret from Key Vault
- [ ] ⬜ Alert Rule (nonprod) — `lll-adf-nonprod` factory, Failed pipeline runs signal
- [ ] ⬜ Action Group (prod) — same config pointing to prod API URL
- [ ] ⬜ Alert Rule (prod) — `lll-adf-prod` factory, same signal + dimension config
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
- [ ] ⬜ ADF service — webhook payload parsing, follow-up REST call, upsert, gap recovery dedup
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
- [ ] ⬜ ADF webhook endpoint — valid payload → 200, invalid secret → 401, malformed → 422, duplicate runId idempotent

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
| Week 2 | 1 | 0 | 18 | 19 |
| Week 3 | 0 | 0 | 18 | 18 |
| Week 4 | 0 | 0 | 22 | 22 |
| Week 5 | 1 | 0 | 14 | 15 |
| Week 6 | 0 | 0 | 16 | 16 |
| Week 7 | 0 | 1 | 28 | 29 |
| Week 8 | 2 | 3 | 21 | 26 |
| **TOTAL** | **11** | **5** | **139** | **155** |

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
| [#62](https://github.com/TheurgicDuke771/DataQ/issues/62) | MSAL redirect lifecycle (real-AAD smoke test deferred) | Open | Week 7 deployment |
| [#65](https://github.com/TheurgicDuke771/DataQ/issues/65) | Vite 8 coordinated bump (vite + plugin-react + vitest) | Open | Week 4 (also tracked as a roadmap task above) |
| ~~[#72](https://github.com/TheurgicDuke771/DataQ/issues/72)~~ | ~~ADR 0004 follow-up: document `trigger_bindings` one-orchestrator-per-(provider, env) assumption~~ | **Closed** ([PR #83](https://github.com/TheurgicDuke771/DataQ/pull/83)) | n/a — guard enforced in PR 6 ADF CRUD |
| ~~[#75](https://github.com/TheurgicDuke771/DataQ/issues/75)~~ | ~~Integration-assert request_id propagates FastAPI→Celery worker logs~~ | **Closed** ([PR 4c-ii](https://github.com/TheurgicDuke771/DataQ/pull/79)) | n/a |
| [#86](https://github.com/TheurgicDuke771/DataQ/issues/86) | `EnvSecretStore.set` is per-process — Celery worker can't resolve API-written secrets (dev only) | Open | Week 5 connection-driven runs (PR 5 follow-on) |
| [#87](https://github.com/TheurgicDuke771/DataQ/issues/87) | Map `SecretWriteError` → 502 in connection create/update (currently 500) | Open | hardening (low priority) |

**Deferred polish** (Week-1 governance era; do during slack): #8, #10, #12, #17, #18, #19, #20.

**New follow-up:** real-Snowflake DEV live-run smoke for `SnowflakeCheckRunner.run_checks` (deferred; needs DEV creds — pairs with Week 7 vault provisioning).

---

## Pending design decisions (must land before the week they affect)

| Decision | Affects | Deadline |
|---|---|---|
| Severity tier weights (warn / fail / critical → health score) | Week 3 Day 1 schema migration | Before Week 3 starts |
| Monitor-kind seam (`check.kind` discriminator + numeric `metric_value` / `duration_ms`) — ADR 0012 | Week 3 schema migration (rides the threshold migration) | Before Week 3 starts |
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
5. Update the **Snapshot** table at the top (calendar burn, task count).
6. If the PR added an out-of-roadmap task (e.g. ADR-driven scope change), add a row with the note.

PR-template checkbox enforces this. If the change is purely tooling / docs that doesn't map to a roadmap task, tick the "N/A" checkbox.
