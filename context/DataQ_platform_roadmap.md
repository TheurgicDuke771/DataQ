# DataQ — v1 Development Roadmap

> **DataQ** — Data quality monitoring platform. The evolution of SnowQ, extended to Snowflake · ADLS Gen2 · S3 · Unity Catalog · ADF.

**Stack:** GX Core · FastAPI · React + Vite + Ant Design · Celery + Redis · PostgreSQL · Azure Container Apps  
**Timeline:** 8 weeks  
**Scope:** Single tenant · DEV / QA / UAT Snowflake environments · Suite-level access sharing

---

## Summary

| | |
|---|---|
| **Total weeks** | 8 |
| **Total tasks** | 100 |
| **Datasource types** | Snowflake · Azure Data Lake (ADLS Gen2) · S3 · Unity Catalog (Databricks) · ADF |
| **Auth** | Azure AD SSO (MSAL) · Suite-level owner / editor / viewer |
| **ADF monitoring** | Hybrid — Azure Monitor webhook (near real-time failure alerts) + Celery polling fallback (succeeded runs + gap recovery) |
| **MCP** | FastMCP — curated MCP server mounted into FastAPI; 8 hand-crafted tools; accessible from Claude Desktop, Claude.ai, GitHub Copilot, Cursor |
| **Deferred to v1.1** | Check templates, clone suite across envs, trigger via REST API, run on data sample, email digest, threshold alerting, shareable report link, Power BI / Teams card, audit log |

---

## Week 1 — Foundation, auth & project scaffold

**Milestone:** Azure AD auth working, GX + FastAPI connected to Snowflake DEV, Swagger live

### Setup
- [ ] Monorepo scaffold — FastAPI backend + React/Vite frontend + Celery + Redis
- [ ] Docker Compose dev environment (all services wired up)
- [ ] PostgreSQL DB + Alembic migrations baseline
- [ ] Core data model: connections, suites, checks, runs, results, shares

### Auth
- [ ] Azure AD SSO — MSAL token validation in FastAPI + login flow in React
- [ ] Session timeout + silent token refresh handling
- [ ] Key Vault integration for all credential storage

### GX baseline & API conventions
- [ ] GX Core wired to Snowflake datasource (DEV env) — run a basic suite
- [ ] GX result serialisation to DB (suites, checks, run results)
- [ ] Configure FastAPI with Pydantic models, route tags, and Swagger (`/docs`) + ReDoc (`/redoc`) enabled for dev/staging; disabled in production

---

## Week 2 — Connection manager — all datasource types (backend)

**Milestone:** All connection types configurable and testable via API; credentials stored in Key Vault

### Snowflake & ADF
- [ ] API: CRUD for Snowflake connections (DEV / QA / UAT), connection test endpoint
- [ ] API: CRUD for ADF connections (subscription ID + service principal)
- [ ] Connection re-auth endpoint — refresh expired Key Vault token

### ADF webhook receiver (Azure Monitor → DQ platform)
- [ ] `POST /api/v1/adf/events` — receive Azure Monitor common alert schema payload, validate shared secret in `Authorization` header, return `200 OK` immediately
- [ ] Parse webhook payload — extract `pipelineName`, `factoryName`, `runId`, `firedDateTime` from Azure Monitor alert context
- [ ] Follow-up ADF REST API call on webhook receipt — fetch full run details (duration, error message, trigger info) using `runId`
- [ ] Upsert pipeline run status into `pipeline_runs` table; trigger DQ result correlation if a matching suite run exists
- [ ] Shared secret config — store webhook secret in Key Vault, expose as `ADF_WEBHOOK_SECRET` env var; document that the same secret must be set in the Azure Monitor Action Group

> **Security note:** The `/api/v1/adf/events` endpoint must be publicly reachable by Azure Monitor. It is secured via shared secret header validation only — no Azure AD token is issued by Azure Monitor for webhook calls. Do not disable secret validation in any environment.

### Flat file — ADLS Gen2 & S3
- [ ] API: CRUD for ADLS Gen2 connections (account URL + managed identity / SAS)
- [ ] API: CRUD for S3 connections (bucket + IAM role / access key)
- [ ] GX `pandas_abs` / `pandas_s3` datasource wiring — connect, list containers, list files
- [ ] File asset config model: container, batching regex, file format (CSV / Parquet / JSON)

### Unity Catalog / Databricks
- [ ] API: CRUD for Databricks connection (workspace URL + PAT token + SQL Warehouse ID)
- [ ] GX Spark / JDBC datasource wiring for Unity Catalog — connect, list catalogs / schemas / tables
- [ ] UC auth test endpoint — validate PAT + SQL Warehouse reachability

---

## Week 3 — Suite & check API — all datasource types (backend)

**Milestone:** Full check CRUD API working across Snowflake, flat files and Unity Catalog; column profiler live

### Suite & check backend
- [ ] API: CRUD for suites and GX expectations (Snowflake path)
- [ ] API: suite sharing — assign users with owner / editor / viewer roles
- [ ] API: suite export to JSON + import from JSON
- [ ] API: check dry-run endpoint — validate against live data, return preview result

### Severity threshold tiers (warn / fail / critical)
> **Design decision required upfront:** agree health score weighting before building — e.g. warn = 0.5 penalty, fail = 1.0, critical = 2.0. This affects the DB schema for run results and cannot be changed cheaply after data is written.
- [ ] Add optional `warn_threshold`, `fail_threshold`, `critical_threshold` fields to check model — all nullable; if only one threshold set, check behaves as standard pass/fail
- [ ] Alembic migration — add threshold columns to `checks` table and `status` enum (`pass`, `warn`, `fail`, `critical`) to `check_results` table
- [ ] Post-processing logic in GX result handler — after GX returns binary result, evaluate observed value against thresholds to derive `warn` / `fail` / `critical` status
- [ ] Update check CRUD API to accept and return threshold fields; update run result response schema to include new status values and health score weighting config

### Column profiler
- [ ] Column profiler endpoint (Snowflake) — nulls, distinct count, min / max, top values
- [ ] Column profiler endpoint (ADLS / S3) — same stats via Pandas on sampled file
- [ ] Column profiler endpoint (Unity Catalog) — via Databricks SQL Warehouse

### Flat file check specifics
- [ ] Check types for flat files: schema validation, row count, null checks, freshness by filename date
- [ ] Batch resolution — resolve batching regex to matched files, pick latest or specific batch

### Unity Catalog check specifics
- [ ] UC table check path — `spark.read.table()` → GX DataFrame datasource → run suite
- [ ] Integration tests across all three datasource types

---

## Week 4 — Connection manager UI + check editor UI (frontend)

**Milestone:** Users can configure any connection type and author checks end-to-end in the UI

### Connection manager UI
- [ ] Connection cards — Snowflake (3 envs), ADF, ADLS/S3, Databricks sections with status badges
- [ ] Add connection drawer — type-specific form fields per connection type
- [ ] Connection health page — bulk test all configured connections, show live status badges, surface auth failures with re-auth action link
- [ ] Connection re-auth UI — surface expired tokens, inline refresh action
- [ ] ADLS/S3 connection form — account URL, container browser, managed identity / SAS toggle
- [ ] Databricks connection form — workspace URL, PAT, SQL Warehouse picker

### Check editor UI
- [ ] Suite list + detail two-panel layout, environment badge on each suite
- [ ] Form-based check editor (Snowflake) — database / schema / table picker, check type dropdown, threshold
- [ ] Flat file check editor — container picker, batching regex input, file format selector, check type
- [ ] Unity Catalog check editor — catalog / schema / table three-level picker
- [ ] Monaco SQL editor for custom check (all datasource types, Snowflake keyword autocomplete)
- [ ] Column profiler panel — inline in check editor, loads on table / file selection
- [ ] Check dry-run button — show preview pass / fail inline before saving
- [ ] Check version history drawer — see previous config before overwriting
- [ ] Severity tier toggle in check editor — optional "enable warn / fail / critical tiers" switch; when on, renders three threshold inputs instead of one; label each clearly with expected behaviour (warn = flag only, fail = alert, critical = alert with urgency)

### Access & admin UI
- [ ] Suite sharing panel — add / remove users, assign roles inline
- [ ] Admin page — list all suites, all users, access overview
- [ ] Suite export / import UI (download JSON, upload JSON)

---

## Week 5 — Execution engine + scheduling

**Milestone:** Async runs with live progress across all datasource types; scheduling operational

### Async execution backend
- [ ] Celery + Redis background task runner for GX scan execution
- [ ] Run progress API — poll endpoint returning per-check live status
- [ ] Cancel run endpoint — gracefully terminate in-progress Celery task
- [ ] Run history retention policy — configurable purge of results older than N days
- [ ] Flat file run path — resolve batch, load via Pandas, execute GX suite
- [ ] UC run path — submit job to Databricks SQL Warehouse, execute GX suite

### ADF hybrid monitoring (webhook + polling fallback)
- [ ] Celery beat task — poll ADF REST API every 10 min for **succeeded** pipeline runs and full run metadata; skip pipelines already updated by webhook within the last 10 min to avoid redundant calls
- [ ] Gap recovery logic — on startup and on a 30-min schedule, fetch all ADF run statuses for the past hour and upsert any missed events (catches webhook failures during API restarts or deploys)
- [ ] `GET /api/v1/adf/pipelines` — return latest status per pipeline (source: DB, updated by both webhook and polling); used by the UI for display

> **Design note:** Webhook handles failure events near-instantly (~1–5 min). Polling handles succeeded runs, run detail enrichment, and recovery from missed webhook calls. The UI always reads from the DB — it never calls ADF directly.

### Execution UI
- [ ] Run now panel — suite picker, env / datasource, notification target
- [ ] Live run progress UI — check-by-check status with spinner + cancel button
- [ ] Scheduled runs table — create, pause, delete cron schedules
- [ ] Recent runs audit table with drill-down link to results

---

## Week 6 — Results dashboard + alerting

**Milestone:** Full results dashboard live across all source types; alerts firing with suppression

### Results dashboard
- [ ] Health score stat cards + 7-day trend chart
- [ ] Per-suite pass / fail progress bars — updated to show warn / fail / critical breakdown
- [ ] Results filter bar — by env, datasource type, suite, date range, status (including warn / fail / critical)
- [ ] Failed check drill-down — sample failing rows from GX result
- [ ] Per-check historical trend chart (not just overall health score)
- [ ] ADF pipeline status panel — polls `GET /api/v1/adf/pipelines` every 30s; status reflects webhook-pushed failures (near real-time) and polling-filled succeeded runs; shows pipeline name, last run time, duration, status badge, and correlated DQ result
- [ ] Datasource type filter — toggle Snowflake / flat file / Unity Catalog on results view
- [ ] CSV + PDF export of results
- [ ] Severity badge colours — green (pass), amber (warn), red (fail), dark red (critical); applied consistently across all result tables, drill-downs, and suite cards
- [ ] Health score weighting — apply agreed penalty weights (warn / fail / critical) in health score calculation; show severity breakdown in the 7-day trend tooltip

### Alerting
- [ ] Notification config UI — Teams webhook per suite, alert on fail / warn / always
- [ ] Alert suppression / snooze — silence a specific check for N hours
- [ ] Alert dedup — fire on first failure only, not on every subsequent scheduled run
- [ ] Teams adaptive card payload — check name, datasource, table / file, observed vs expected
- [ ] Severity-aware alert routing — `warn` fires a quieter notification (different card colour, no @mention); `fail` fires standard alert; `critical` fires with @channel mention and distinct card styling

---

## Week 7 — Deployment, hardening & docs

**Milestone:** Production-ready v1 deployed to Azure, CI/CD live, team onboarded

### DevOps & deployment
- [ ] Containerise FastAPI + React + Celery + Redis
- [ ] Push images to Azure Container Registry
- [ ] Deploy to Azure Container Apps (API + Celery worker) + Azure Static Web App (React UI)
- [ ] CI/CD pipeline — lint, test, build, deploy on merge to `main`
- [ ] Application Insights integration — traces, errors, slow queries, Celery task metrics

### Azure Monitor webhook setup (one-time infra, post-deployment)
> Requires Azure Container Apps deployment to be live first so the public FastAPI URL is known. Requires `Microsoft.Insights/actionGroups/write` + `Microsoft.Insights/metricAlerts/write` permissions on the subscription.
- [ ] Create Action Group (nonprod) — type: Webhook, URL: `https://<nonprod-api>.azurecontainerapps.io/api/v1/adf/events`, shared secret: value from Key Vault
- [ ] Create Alert Rule (nonprod) — scope: `lll-adf-nonprod` factory, signal: `Failed pipeline runs`, dimension: all pipelines (tick "include all future values"), action: Action Group above
- [ ] Create Action Group (prod) — same config pointing to prod API URL
- [ ] Create Alert Rule (prod) — scope: `lll-adf-prod` factory, same signal + dimension config
- [ ] Smoke test — trigger a deliberate pipeline failure in DEV, confirm webhook fires, DB updates, and ADF panel reflects failure within 5 min

### FastMCP — MCP server
> **Library:** `fastmcp` (PrefectHQ). Curated hand-crafted tools mounted into the existing FastAPI app. Each tool calls the internal service layer directly — no logic duplication, but descriptions are written specifically for LLM consumption, not REST API consumers.

- [ ] Install FastMCP, scaffold `mcp_server.py`, mount MCP app into FastAPI at `/mcp` — `app.mount("/mcp", mcp.get_asgi_app())`
- [ ] Wire Azure AD token validation into FastMCP's auth provider — validate Bearer token on every MCP tool call using the same `verify_azure_ad_token` logic as the REST API
- [ ] Implement `list_suites` resource — all suites accessible to current user, name / datasource / env / last run status / check count
- [ ] Implement `get_suite_results` resource — latest DQ run results for a suite; pass/fail per check, observed vs expected values, sample failing rows
- [ ] Implement `get_health_score` resource — overall health score and 7-day trend, filterable by env and datasource type
- [ ] Implement `get_adf_pipeline_status` resource — latest ADF pipeline run status with correlated DQ result per pipeline
- [ ] Implement `trigger_suite_run` tool — async GX suite execution against a given datasource + env; returns `run_id` for polling
- [ ] Implement `get_run_status` tool — poll live check-by-check progress for a running execution using `run_id`
- [ ] Implement `create_check` tool — add a new GX expectation to a suite (form path: check type + column + threshold; or raw SQL)
- [ ] Implement `profile_column` tool — run column profiler on a table or file; returns nulls, distinct count, min/max, top values
- [ ] Write LLM-optimised docstrings for all 8 tools — describe what the tool does, when to use it, what parameters mean, and what the response contains; tested against realistic natural language queries
- [ ] Test end-to-end with Claude Desktop — verify tool selection is correct for: "what failed today?", "run the orders suite on DEV", "why did the customer pipeline fail?", "add a null check on email"
- [ ] Document MCP connection config for Claude Desktop, Claude.ai, GitHub Copilot (`mcp.json`), and Cursor (`~/.cursor/mcp.json`) in README

> **Security note:** The `/mcp` endpoint must be protected with Azure AD token validation. Do not expose it without auth — it provides full read/write access to suites, checks, and execution.

### Hardening & docs
- [ ] E2E test coverage for critical paths (auth, Snowflake run, flat file run, UC run, results)
- [ ] Error handling audit — all API endpoints return consistent error shapes
- [ ] Ensure all FastAPI endpoints have `summary`, `description`, `tags`, and `response_model` for clean Swagger output
- [ ] README + deployment guide + environment variable reference
- [ ] Team onboarding session + feedback collection

---

## Week 8 — Unit testing

**Milestone:** Comprehensive unit test suite covering backend services, API layer, and frontend components

### Backend unit tests (pytest)
- [ ] Auth service — token validation, session expiry, Key Vault credential retrieval
- [ ] Connection service — CRUD operations, test endpoint logic per datasource type
- [ ] Suite service — CRUD, share assignment, export / import serialisation
- [ ] Check service — expectation builder (form path), SQL check validator, dry-run logic, threshold tier evaluation (warn/fail/critical status derivation from observed value + three thresholds, binary fallback when tiers not set)
- [ ] Column profiler service — null count, distinct count, min/max per datasource type
- [ ] Execution service — Celery task dispatch, progress polling, cancel logic, retention purge
- [ ] Alerting service — Teams webhook dispatch, dedup logic, snooze / suppression
- [ ] ADF service — webhook payload parsing (valid schema, missing fields, wrong secret → 401), follow-up REST API call for run details, upsert logic, gap recovery dedup
- [ ] ADF polling service — succeeded run fetch, skip-if-recently-updated logic, startup gap recovery
- [ ] Result service — health score calculation, historical trend aggregation, export generation
- [ ] MCP service — each of the 8 tools returns correct response shape; auth rejection on missing/invalid token; `trigger_suite_run` returns valid `run_id`; `create_check` persists to DB correctly

### API layer tests (pytest + httpx)
- [ ] Auth endpoints — login redirect, token refresh, unauthorised access returns 401
- [ ] Connection endpoints — CRUD happy paths + validation errors
- [ ] Suite & check endpoints — CRUD, share, export / import, dry-run
- [ ] Execution endpoints — trigger run, poll progress, cancel, list history
- [ ] Results endpoints — dashboard data, drill-down, filters, download
- [ ] ADF webhook endpoint — valid payload returns 200, invalid secret returns 401, malformed payload returns 422, duplicate runId is idempotent

### Frontend unit tests (Vitest + React Testing Library)
- [ ] Login screen — Azure AD button renders, redirects on click
- [ ] Connection manager — card renders per type, status badge colours, re-auth flow
- [ ] Check editor — form fields render per check type, column profiler loads, dry-run shows result, severity tier toggle shows / hides three threshold inputs correctly
- [ ] Suite sharing panel — add/remove user, role assignment
- [ ] Execution page — run now triggers correctly, progress bar updates, cancel button
- [ ] Results dashboard — stat cards render correct values, filters update table, ADF panel shows status, severity badges render correct colour for pass / warn / fail / critical

### Test infrastructure
- [ ] Pytest fixtures — mock GX context, mock Snowflake / ADLS / UC connections, mock Celery, mock Azure Monitor webhook payload, mock ADF REST API run detail response
- [ ] CI gate — PRs blocked if unit test coverage drops below 80%
- [ ] Test data fixtures — sample suites, check results, run histories for consistent assertions

---

## Tech stack summary

| Layer | Technology |
|---|---|
| Backend framework | FastAPI (Python) |
| DQ engine | Great Expectations (GX Core) v1 |
| Task queue | Celery + Redis |
| Database | PostgreSQL + Alembic |
| Frontend | React + Vite + Ant Design |
| SQL editor | Monaco Editor |
| Auth | Azure AD (MSAL) |
| Secrets | Azure Key Vault |
| Hosting | Azure Container Apps (API + worker) · Azure Static Web App (UI) |
| Observability | Azure Application Insights |
| CI/CD | GitHub Actions / Azure DevOps |
| API docs | FastAPI built-in Swagger UI + ReDoc |
| MCP server | FastMCP (PrefectHQ) — 8 curated tools mounted at `/mcp`; compatible with Claude Desktop, Claude.ai, GitHub Copilot, Cursor |
| ADF monitoring | Azure Monitor Alert Rule → Action Group → webhook (`POST /api/v1/adf/events`) for near real-time failures · Celery beat polling (10 min) for succeeded runs + gap recovery |

---

## Datasource support matrix

| Datasource | Connection | Column profiler | Check editor | Execution path |
|---|---|---|---|---|
| Snowflake (DEV/QA/UAT) | ✅ Week 2 | ✅ Week 3 | ✅ Week 4 | ✅ Week 5 |
| ADLS Gen2 (flat files) | ✅ Week 2 | ✅ Week 3 | ✅ Week 4 | ✅ Week 5 |
| AWS S3 (flat files) | ✅ Week 2 | ✅ Week 3 | ✅ Week 4 | ✅ Week 5 |
| Unity Catalog (Databricks) | ✅ Week 2 | ✅ Week 3 | ✅ Week 4 | ✅ Week 5 |
| ADF (pipeline status) | ✅ Week 2 | n/a | n/a | ✅ Week 2 (webhook receiver) · Week 5 (hybrid polling) · Week 7 (Azure Monitor setup) |

---

## MCP tools reference

| Tool / Resource | Type | Description | Example natural language query |
|---|---|---|---|
| `list_suites` | resource | All suites accessible to current user — name, datasource, env, last run status, check count | "What suites do I have in QA?" |
| `get_suite_results` | resource | Latest DQ run results for a suite — pass/fail per check, observed vs expected, failing row samples | "What failed in the orders suite today?" |
| `get_health_score` | resource | Overall health score and 7-day trend, filterable by env and datasource | "What's the data health score for QA this week?" |
| `get_adf_pipeline_status` | resource | Latest ADF pipeline run status with correlated DQ result per pipeline | "Did any ADF pipelines fail overnight?" |
| `trigger_suite_run` | tool | Trigger async GX suite execution — returns `run_id` to poll for status | "Run the orders completeness suite on DEV" |
| `get_run_status` | tool | Poll live check-by-check progress for a running execution using `run_id` | "Is the orders run finished yet?" |
| `create_check` | tool | Add a new GX expectation to a suite — form path (check type + column + threshold) or raw SQL | "Add a null check on email to the customer suite" |
| `profile_column` | tool | Run column profiler on a table or file — nulls, distinct count, min/max, top values | "Profile the revenue column in FACT_ORDERS" |

> **Auth:** All MCP tools require a valid Azure AD Bearer token. The same token used for the DataQ web UI works for MCP clients. Configure once in your AI tool of choice — Claude Desktop, Copilot, or Cursor — and all 8 tools are available.

---

## Deferred to v1.1

- Check templates / reusable library
- Clone suite across environments
- Trigger run via REST API (for ADF / CI pipeline integration)
- Run on data sample (for large tables)
- Email digest — daily summary of all suite results
- Threshold-based alerting — alert only when failure % exceeds N
- Shareable report link (read-only, for stakeholders)
- Power BI / Teams adaptive card integration
- Audit log — who changed what, when
- Dark mode
- Per-check owner tagging — assign a team or individual as accountable owner for a specific check; surface in results and alerts
- Volume anomaly detection — flag row count change beyond % drift vs rolling N-day average; requires dynamic baseline calculation rather than static threshold
- SLA definition per table / pipeline — formal `sla_deadline` field on suites (e.g. "table must be refreshed by 07:00"); dedicated SLA breach counter KPI on results dashboard
- Databricks Live Tables (DLT) expectations — integrate with Delta Live Tables Expectations API for streaming pipeline DQ checks; separate execution model from the current GX + Spark path
- Table / asset lineage — visual dependency graph mapping ADF pipelines → Snowflake tables / ADLS files → DataQ suites → checks; built with React Flow; uses data already held in connections and suite config; ~1 week of work
