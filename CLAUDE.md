# CLAUDE.md — DataQ project guide for AI assistants

> Single source of truth for any Claude / AI assistant working in this repo. Read this end-to-end before touching code.

---

## 1. Project summary

**DataQ** is a single-tenant data quality monitoring platform built around Great Expectations (GX Core). It runs DQ checks across **4 datasources** and integrates with **2 orchestration providers**.

| Layer | Components |
|---|---|
| **Datasources (you can write checks against)** | Snowflake (DEV/QA/UAT), ADLS Gen2, AWS S3, Unity Catalog (Databricks) |
| **Orchestration providers (monitor + trigger only — NOT datasources)** | Azure Data Factory (ADF), Apache Airflow |
| **Backend** | FastAPI + Celery + Redis + PostgreSQL + Alembic |
| **Frontend** | React + Vite + Ant Design + Monaco editor |
| **Auth / secrets** | Azure AD (MSAL) + Azure Key Vault |
| **Deploy** | Azure Container Apps + Azure Static Web App |
| **Observability** | Azure Application Insights + structlog |
| **AI integration** | FastMCP (8 curated tools mounted at `/mcp`) — Claude Desktop / Claude.ai / Copilot / Cursor |

Timeline: **8 weeks** to v1. Scope: single tenant, suite-level access sharing, Azure-hosted.

---

## 2. Architecture at a glance

See [docs/architecture.md](docs/architecture.md) for the full diagram (Mermaid — renders on GitHub).

```
Browser ──HTTPS──► React (Static Web App) ──► FastAPI (Container Apps) ──► PostgreSQL
                                                    │  │
AI clients ──MCP/HTTP──► FastAPI /mcp endpoint      │  └──► Celery worker ──► GX execution ──► Snowflake / ADLS / S3 / UC
                                                    │
                                                    ├──► Redis (task queue)
                                                    ├──► Key Vault (secrets)
                                                    └──► App Insights (observability)

ADF ──► Azure Monitor alert rule ──► webhook ──► POST /api/v1/orchestration/events/adf
Airflow ──► on_success/on_failure_callback ──► POST /api/v1/orchestration/events/airflow
FastAPI ──► MS Teams webhook (alerts)
```

---

## 3. Repo layout

Flat monorepo (decided in Week 1):

```
DataQ/
├── backend/                     # FastAPI + Celery + GX (Python, conda)
│   ├── app/
│   │   ├── core/                # logging, errors, config (locked in PR 2)
│   │   ├── db/                  # SQLAlchemy models, session
│   │   ├── api/                 # FastAPI routers (versioned: /api/v1/...)
│   │   ├── services/            # business logic per domain
│   │   ├── orchestration/       # OrchestrationProvider abstraction (ADF, Airflow)
│   │   ├── datasources/         # ConnectionAdapter + CheckRunner per type; gx_runner.py (shared GX translation), flatfile.py (flat-file IO + runner + batch resolution)
│   │   └── mcp/                 # FastMCP tools (Week 7)
│   ├── alembic/
│   └── tests/                   # + tests/support/ (adversarial harness), tests/integration/ (end-to-end datasource runs)
├── frontend/                    # React + Vite + Ant Design (Node, pnpm)
│   ├── src/
│   └── tests/
├── docs/
│   ├── architecture.md          # Mermaid architecture diagram
│   └── adr/                     # Architecture Decision Records
├── integrations/                # user-deployed snippets (NOT app code; e.g. Airflow DAG callback)
│   └── airflow/                 # dataq_airflow_callback.py + setup README
├── scripts/
│   └── setup.sh                 # one-command dev env bootstrap
├── context/                     # original product/roadmap context (read-only reference)
│   └── DataQ_platform_roadmap.md
├── .github/
│   ├── workflows/
│   ├── pull_request_template.md
│   ├── CODEOWNERS
│   └── ISSUE_TEMPLATE/
├── docker-compose.yml
├── environment.yml              # conda env — pip section points at backend/requirements-dev.txt
├── pyproject.toml               # Black + Ruff + mypy config
├── CONTRIBUTING.md
├── CLAUDE.md                    # this file
└── README.md
```

**Promotion to `apps/` + `packages/`:** only if a real shared package emerges (e.g., auto-generated OpenAPI client in Week 4–5). Default flat.

---

## 4. Datasources vs orchestration — critical distinction

**Datasources** are stores you write DQ checks against:
- Snowflake (DEV/QA/UAT)
- ADLS Gen2 (flat files)
- AWS S3 (flat files)
- Unity Catalog / Databricks

**Orchestration providers** are NOT datasources. They are workflow engines whose pipelines/DAGs we observe and react to. Their *only* three responsibilities in DataQ:

1. **Monitor** pipeline/DAG runs → stored in `pipeline_runs` table (separate from `runs` / `results`).
2. **Detect failure** in near-real-time via provider-specific event channels (webhook for both).
3. **Trigger suite execution on successful completion** via `trigger_bindings` (`provider`, `pipeline_or_dag_id`, `suite_id`, `env`). Failure events alert the user but do NOT trigger suite runs.

Both providers implement a single `OrchestrationProvider` interface — ADF is the reference implementation, Airflow is the second. **Never hardcode ADF-only logic; always go through the abstraction.**

| Provider | Event channel | Auth | Polling fallback |
|---|---|---|---|
| ADF | Azure Monitor alert → webhook | Shared secret header (Azure Monitor's only mode) | ADF REST API, 10 min |
| Airflow | DAG `on_*_callback` → webhook | HMAC-signed payload (signing key in Key Vault) | Airflow REST API `dagRuns`, 10 min |

Airflow callbacks require the user to add a snippet to their DAGs (we can't mutate them). Polling is the documented fallback.

**Anti-pattern (do not do this):** treating ADF/Airflow as a 5th/6th datasource in the connection editor, check editor, or suite model.

---

## 5. Framework choice — GX-only for v1

- **v1:** Great Expectations (GX Core) is the sole DQ framework across all 4 datasources. Unifies result schema, suite/check model, MCP tools, and the check editor. Every v1 check is a GX **expectation** (`check.kind = 'expectation'`).
- **v1.1:** Databricks Labs **DQX** will be added for DLT / streaming use cases (GX is batch-only and runs poorly on streaming). DQX will implement the same `UnityCatalogCheckRunner` interface introduced in Week 3 — UI exposes `engine: gx | dqx` toggle on UC suites.
- **Monitor-kind seam (do-now, Week 3):** not every monitor is a GX expectation. A `check.kind` discriminator (`expectation` in v1; `freshness | volume | schema_drift | anomaly | comparison` reserved) + numeric `metric_value` on results let v1.x auto-monitors slot in without a check/result schema rewrite. This seam is **orthogonal to the datasource seams** (`CheckRunner`, `ConnectionAdapter`): it varies by *monitor kind*, not datasource. See ADR `0012` (and `0014` for the reserved `comparison` / cross-dataset reconciliation kind) and post-v1 roadmap Theme A. Most real incidents are freshness/volume, not value-level — this is the leap from "GX runner" to DQ platform.
- **Week-3 outcome (done):** the UC run path is thin behind `UnityCatalogCheckRunner` (reads the table into a GX DataFrame asset — the DQX swap-in shape), and `check.kind` + `metric_value`/`duration_ms` shipped in the one threshold migration, so the monitor-kind impls won't ripple into the suite/check/result layer later.

---

## 6. Working agreements (rules above feature work)

Full list (37 rules across 8 categories) lives in [CONTRIBUTING.md](CONTRIBUTING.md). Highlights:

### Commit & change discipline
- **One functionality per commit** (where possible).
- **Manually test each committed change before starting the next functionality** (required until unit tests land in Week 8).
- **Defects → GitHub issue, never silent fixes.** Use `gh issue create`. The fixing PR must include `Fixes #N`.
- **From Week 8 onward, every new functionality ships with tests.**

### Git workflow
- **Trunk-based** with short-lived feature branches off `main`. No long-lived `develop`.
- Branch names: `feature/<desc>`, `fix/issue-<N>-<desc>`, `chore/<desc>`, `docs/<desc>`.
- `main` is protected: PR + passing CI + no force-push. (≥1 approving review is disabled during solo-dev phase; re-enable before onboarding a second contributor.)
- **Squash-merge only into `main`.**
- **Conventional commits** (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).

### CI/CD quality gates (block merge)
- Ruff (lint), Black `--check` (format), mypy (types), pytest (from W8), frontend lint/format/test.
- `betterleaks` secret scanning (pre-commit + CI).
- Bandit (Python SAST) + CodeQL.
- **Dependency CVE audit (CI): `pip-audit -r backend/requirements-dev.txt` (full backend runtime + test surface) + `pnpm audit --audit-level=high` (frontend).** Synchronous merge gate; complements the async Dependabot layer below.
- **Python deps have one source of truth: `backend/requirements.txt`** (runtime hub) → `requirements-dev.txt` (`-r` it + test toolchain) → `environment.yml` + CI all install from it. The re-listed subsets `requirements-dev.txt` pulls are `requirements-typecheck.txt` (the typed deps mypy needs) and `requirements-tooling.txt` (Black/Ruff/mypy/Bandit/pre-commit); the `typecheck-deps-sync` check (pre-commit **and** CI `backend-lint`) keeps the mypy hook aligned. `requirements-mutation.txt` (mutmut) is **standalone — not `-r`'d by anything**, so it stays off CI's install + `pip-audit` surface (manual tool, CONTRIBUTING rule 4a). Bump a Python version in `requirements.txt` only.
- Dependabot for npm + pip + github-actions — **version updates + security alerts/updates both enabled** (alerts scan the full pip+npm dependency graph).

### Tooling (locked in Week 1, do not drift)
- **Python:** conda env (`conda create -n dataq python=3.13`) — *not* venv, *not* poetry. (3.13 since ADR 0017; was 3.11.)
- **Black** formatter (CI-enforced).
- **Ruff** lint, **mypy** types, **structlog** logging, **Pydantic Settings** config (12-factor).
- **Frontend:** Prettier + ESLint.

### Observability
- **Structured logging from Week 1.** structlog, JSON, `request_id` correlation propagated FastAPI → Celery → GX.
- **PII redaction at logger level** (failed-check sample rows may contain sensitive data).
- **App Insights exception tracking wired Week 1**, not Week 7.

### Database
- **Backward-compatible migrations only.** No `DROP COLUMN` + code change in same PR. Two-step deploys from W5 onward.
- Migration PR checklist: rollback plan + "tested up + down locally."

### Documentation
- **ADRs in `docs/adr/`** — one short markdown per significant decision.
- `scripts/setup.sh` — one command from clone to working dev env.

### Security cadence
- End-of-week quick scan from Week 2: vuln alerts (Dependabot alerts + CI `pip-audit`/`pnpm audit`), secret scan, OWASP spot check, Key Vault audit.
- Hard security review gate before Week 7 deploy.

---

## 7. Required reading before coding

1. [CONTRIBUTING.md](CONTRIBUTING.md) — full 37-rule working agreements + DoD + commit/branch conventions
2. [docs/adr/](docs/adr/) — all ADRs (architecture decisions with rationale)
3. [context/DataQ_platform_roadmap.md](context/DataQ_platform_roadmap.md) — the 8-week, 100-task product roadmap
4. The current week's milestone target (see §13 below)

---

## 8. Local dev quickstart

> **Note:** These commands assume Week 1 scaffolding (PR 1) is in place. They will not work on a fresh clone until `scripts/setup.sh`, `environment.yml`, and `docker-compose.yml` are committed.

```bash
git clone <repo>
cd DataQ
./scripts/setup.sh           # creates conda env, installs pre-commit, pulls images, runs migrations, seeds dev data
conda activate dataq
docker-compose up            # Postgres + Redis + FastAPI + React + Celery worker
# Smoke test:
curl -X POST http://localhost:8000/api/v1/_probe/snowflake-suite
# Browse Swagger: http://localhost:8000/docs
```

---

## 9. Key design decisions (with ADR links)

| Decision | ADR | Status |
|---|---|---|
| Trunk-based branching, squash-merge into `main` | [0001](docs/adr/0001-trunk-based-branching.md) | Locked W1 |
| Conventional commits | [0002](docs/adr/0002-conventional-commits.md) | Locked W1 |
| GX-only for v1; DQX deferred to v1.1 | [0003](docs/adr/0003-gx-only-for-v1.md) | Locked W1 |
| Orchestration abstraction (ADF + Airflow share `OrchestrationProvider`) | [0004](docs/adr/0004-orchestration-abstraction.md) | Locked W1 |
| Severity tier weights (warn/fail/critical → health score) | [0005](docs/adr/0005-severity-tier-weights.md) | Accepted W2 (weights 0.5/1.0/2.0; SQL-normalised health score) |
| ADF webhook auth (shared secret in URL, hard-cutover rotation, no v1 replay check) | [0006](docs/adr/0006-adf-webhook-authentication.md) | Accepted W2 |
| Airflow callback model (HMAC-signed header + polling fallback) | [0007](docs/adr/0007-airflow-callback-model.md) | Accepted W2 |
| MCP mounted at `/mcp` with Azure AD auth | `0008` (TBD W7) | Pending W7 |
| Repo layout: flat monorepo (`backend/` + `frontend/`) | [0009](docs/adr/0009-flat-monorepo-layout.md) | Locked W1 |
| Provider-agnostic infra seams (Azure = default impl, not architecture; auth boundary now, observability via OTel deferred) | [0010](docs/adr/0010-provider-agnostic-infrastructure-seams.md) | Accepted W2 |
| Extensibility seams (generic runner dispatch, `ResultPublisher`, dbt-as-`OrchestrationProvider`; second impls deferred post-v1) | [0011](docs/adr/0011-extensibility-seams-for-deferred-integrations.md) | Accepted W2 |
| Monitor-kind seam (`check.kind` discriminator + numeric `metric_value`/`duration_ms`; v1 = `expectation` only, auto-monitors deferred to v1.x) | [0012](docs/adr/0012-monitor-kind-seam.md) | Accepted W2 (rides the W3 threshold migration) |
| Marketplace distribution = customer-deployed **BYOL** (not multi-tenant hosted SaaS); post-v1; standing anti-lock-in guardrails keep Azure as one impl behind each seam | [0013](docs/adr/0013-marketplace-distribution-and-anti-lock-in.md) | Accepted (2026-06-01) |
| Cross-dataset reconciliation as a reserved `comparison` check kind (reuse FastAPI_DataComparison engine; build post-v1; two-connection model → ADR 0015 pending) | [0014](docs/adr/0014-reconciliation-comparison-check-kind.md) | Accepted (2026-06-01) |
| Severity derivation (thresholds band the GX unexpected-% as `metric_value`, higher=worse; thresholds-as-policy override GX `success`; binary fallback; A→B reversible since raw `observed_value` retained) | [0016](docs/adr/0016-severity-derivation-semantics.md) | Accepted (2026-06-04) |
| Python runtime 3.11 → 3.13 (3.14 deferred — GX 1.17 caps at 3.13; supersedes the W1 Python-3.11 lock); bundled with the Snowflake 3→4 + cryptography/pyOpenSSL CVE refresh (#129) | [0017](docs/adr/0017-python-313-runtime-upgrade.md) | Accepted (2026-06-08) |
| Results surface is an in-app React page (suite-scoped authz + PII redaction the API owns); Grafana deferred to an optional post-v1 read-only **ops** add-on, never the per-user product surface | [0018](docs/adr/0018-results-surface-and-grafana-deferral.md) | Accepted (2026-06-11) |
| Custom-SQL checks ride `kind='expectation'` via GX `UnexpectedRowsExpectation` (no new kind / migration / runner change); guardrails = read-only single-statement validation + SQL-datasource-only gating + least-privilege role; binary pass/fail in v1 | [0019](docs/adr/0019-custom-sql-check-kind.md) | Accepted (2026-06-14) |
| History/audit strategy: per-entity Type-4 snapshot tables (`check_versions`, `connection_versions`) where config history is needed; **no SCD-2** (breaks the FK model + maintenance tax); credentials never snapshotted; cascade-delete accepted (history not retained past delete); soft-delete + cross-entity audit log deferred | [0020](docs/adr/0020-history-and-audit-strategy.md) | Accepted (2026-06-20) |
| Live test/demo-data environment (retail model + 3 reference flows A/B/C) lives **outside** the repo — Terraform/mock-data/Databricks notebook **not git-tracked**; only the ADR + `progress.md` pointer are committed; v1-supporting, discharges the deferred live-warehouse/file smoke | [0021](docs/adr/0021-demo-test-data-environment-strategy.md) | Accepted (2026-06-21) |
| Week-6 prototype adoption — build the **full 13-screen set** as dedicated, deep-linkable pages (**no create/edit drawer survives**; Share + version-history + run-progress + import-suite remain as non-edit drawers; **prototype wins** on any drawer-vs-page / layout conflict; Profile content + **Settings** + Admin-reconcile pulled into W6); source picker is **datasources-first, Orchestration last**; chart library = **recharts** (lazy-loaded, clears `pnpm audit`/bundle gate); "View as" switch + marketing + dark mode **not** adopted | [0022](docs/adr/0022-week6-prototype-adoption-and-chart-library.md) | Accepted (2026-06-21) |

---

## 10. Critical pointers (easy to get wrong)

- **`pipeline_runs` ≠ `runs`.** Orchestrator pipeline executions live in `pipeline_runs`; DQ suite executions live in `runs`. They link via `triggered_by: '<provider>:<pipeline_or_dag_id>:<provider_run_id>'`.
- **`trigger_bindings` is provider-agnostic.** Composite key (`provider`, `pipeline_or_dag_id`, `env`) → `suite_id`. Don't add an ADF-specific bindings table.
- **PII redaction at the logger level**, not at every call site. The redactor sits in `backend/app/core/logging.py`.
- **Backward-compatible migrations only.** Code that depends on a new column ships in a separate PR *after* the migration is deployed.
- **The Week-3 threshold migration already added the schema seams (done).** It landed `check.kind` (default `'expectation'`), `results.metric_value` (NUMERIC) + `duration_ms` (INT), and the severity thresholds — see ADR `0012`. `metric_value` is the SQL-aggregatable scalar a monitor measured; **don't store metrics only in JSONB `observed_value`** (you can't `AVG()`/`STDDEV()` it for trends or anomaly baselines), and **don't add a second migration re-introducing these columns**.
- **Secret scanning in pre-commit AND CI.** Don't rely on one alone.
- **Azure Monitor alert setup (Week 7) needs the deployed public API URL.** Deployment must come first; coordinate Container Apps ingress with infra/security before Week 7 to avoid a deployment-day surprise.
- **MCP tool descriptions are LLM-facing, not REST-API-facing.** Write them for natural-language selection; test against the 4 canonical NL queries in the roadmap.

---

## 11. What NOT to do

- ❌ Don't add ADF or Airflow as a queryable datasource in the connection editor / check editor / suite model.
- ❌ Don't bypass the `OrchestrationProvider` abstraction with provider-specific branching in service code.
- ❌ Don't deepen Azure lock-in: no reading MSAL/Entra claims in route/service code (depend on the generic `get_current_user`), no hardcoded Azure resource names/endpoints in business logic, no Azure-only assumptions baked into container images. Azure is one impl behind each seam — see ADR [0010](docs/adr/0010-provider-agnostic-infrastructure-seams.md) / [0013](docs/adr/0013-marketplace-distribution-and-anti-lock-in.md).
- ❌ Don't `git commit --no-verify` past hooks. If a hook fails, fix the underlying issue.
- ❌ Don't commit `.env` files. Use `.env.example` / `.env.app.example` as the templates.
- ❌ Don't put a credential — **even a local/mock one** — in any git-tracked file (templates, `scripts/`, CI, compose). Env templates ship the secret keys **blank** with the shape in a comment; `scripts/setup.sh` generates the local-dev password into the gitignored `.env`/`.env.app` on first run. Non-secret config defaults and non-secret identifiers (db/user name) may stay populated.
- ❌ Don't drop columns in the same PR as the code change that stops using them. Two-step it.
- ❌ Don't fix bugs silently. Raise a GitHub issue, then PR with `Fixes #N`.
- ❌ Don't batch unrelated changes into one commit. One functionality per commit.
- ❌ Don't track GX Core at "latest." Pin the version in `environment.yml` — GX v1 API has drifted across point releases.
- ❌ Don't use venv or poetry for backend dev. Conda only.
- ❌ Don't write the MCP layer before Week 7. The service layer must stabilise first.

---

## 12. Where things live

| Artifact | Location |
|---|---|
| Product roadmap (100 tasks, 8 weeks) | [context/DataQ_platform_roadmap.md](context/DataQ_platform_roadmap.md) |
| System architecture diagram | [docs/architecture.md](docs/architecture.md) |
| Architecture Decision Records | [docs/adr/](docs/adr/) |
| Working agreements (full 37-rule list) | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Live task tracker (per-PR roadmap status) | [docs/progress.md](docs/progress.md) |
| Memory (cross-session AI context) | `~/.claude/projects/-Users-arijit-Coding-Python-DataQ/memory/` |

---

## 13. Status & current milestone

> **Detailed task-level status** lives in [docs/progress.md](docs/progress.md) — mirrors the 100-task roadmap, updated per PR. This section carries only the headline.

**Current week:** **Week 6 of 8 — Results dashboard + alerting — COMPLETE (27/27); entering Week 7** (Weeks 1–5 complete, all exit gates met). Week-6 close: the **alerting backend track** — `ResultPublisher` seam (#366), Teams adaptive card + publisher (#367), severity-aware routing (#368), dedup (#369), suppression/snooze (#370), per-suite notification config (#373) — plus the **prototype Phase 5–6 screens**: Profile content (#374), Workspace Settings (#375), Admin layout-reconcile (#376), standard 4xx/5xx error pages (#377), and the per-suite **notification config UI** (#378). Earlier Week-6: Results scaffold (PR-C1), Enhanced Monitoring Dashboard + run-detail route (#333), results filter bar + orchestration poll/correlation (#347), drawer→page restructures (#350), layout/prototype-fidelity polish (#353), and redacted sample failing rows (#365, closed #226). **Week-7 early-credit:** Azure deploy scaffolding (#379 — frontend Dockerfile + ACA/SWA manifests + parameterized deploy workflow + CORS middleware + prod env reference; manual-trigger only, the actual apply stays blocked on Azure RP registration per ADR 0021). _All Week-6 feature work landed as a stacked PR chain #366→#379 (open, each green + reviewed)._
**Week-6 exit gate:** Full results dashboard live across all source types; alerts firing with suppression. — **met** (Enhanced Monitoring Dashboard + Results page + run-detail across Snowflake/flat-file/UC; the `ResultPublisher`→Teams alerting path with severity-aware routing, dedup, per-check snooze suppression, and per-suite config + UI). Live warehouse/file run remains the deferred Week-1 smoke.
**Week-5 exit gate:** Async runs with live progress across all datasource types; scheduling operational. — **met** (run paths Snowflake + flat-file batch + UC; `GET /runs/{id}/progress` + the live-progress drawer; cancel; cron `schedules` + 60s dispatcher + scheduled-runs UI; ADF/Airflow polling + gap recovery + `/orchestration/pipelines`; PII-minimisation retention sweep). Live warehouse/file run remains the deferred Week-1 smoke.
**Week-4 exit gate:** Users can configure any connection type and author checks end-to-end in the UI. — **met** (connection manager UI for all six types + edit/re-auth/delete; suites list/detail; catalog-driven check editor + Monaco custom-SQL; dry-run preview; column profiler panel; run-target editor; export/import + sharing panels; check version-history drawer; **admin control centre** — #289). Live warehouse/file runs remain the deferred smoke.
**Week-1 exit gate:** GX against Snowflake DEV persists a result row. — **met** via `POST /api/v1/_probe/snowflake-suite` → Celery `run_suite` → `run_service` → `results` (live Snowflake run fails-soft pending creds — deferred smoke).
**Week-2 exit gate:** All six connection types configurable + testable via API, credentials in the SecretStore. — **met** (Snowflake / ADF / Airflow / ADLS Gen2 / S3 / Unity Catalog behind the `ConnectionAdapter` seam + registry; real Key Vault provisioning deferred to W7).
**Week-3 exit gate:** Full check CRUD across Snowflake / flat files / Unity Catalog + column profiler live. — **met** (suite & check CRUD + sharing + export/import + dry-run; severity tiers + monitor-kind seam; column profiler on all 4 datasources; the three GX `CheckRunner`s — Snowflake / flat-file / UC — behind the shared `gx_runner`; flat-file batch resolution; end-to-end datasource-run integration tests). Live warehouse/file runs remain the deferred smoke.
**Completed since project start (2026-05-24):** see [docs/progress.md](docs/progress.md) for the per-PR ledger. Headlines:
- **Week 1** — governance + tooling lock (#1–#37), structlog/error-envelope/FastAPI skeleton + SQLAlchemy/Alembic baseline (PR 2), Azure AD SSO end-to-end (PR 3), async backbone + Snowflake GX adapter + run/result persistence (PR 4).
- **Week 2** — connection manager for all six types (Snowflake/ADF/Airflow/ADLS/S3/UC) behind the `ConnectionAdapter` seam; ADF + Airflow orchestration event receivers (secret-in-URL / HMAC) + connection adapters; re-auth endpoint; ADRs 0005/0006/0007/0010/0011/0012.
- **Week 3** — suite & check CRUD + sharing + export/import + dry-run; severity tiers (ADR 0005/0016) + monitor-kind seam (ADR 0012); column profiler (all 4 datasources); GX `CheckRunner`s (Snowflake/flat-file/UC) on the shared `gx_runner`; batch resolution; integration tests. Plus the testing-discipline upgrade (adversarial harness + mutation spikes, CONTRIBUTING rule 4a).
- **Week 4 (complete, 26/26)** — frontend: app-router shell + connections list (#191), spec-driven add-connection drawer (#196) + Snowflake key-pair (#193), connection edit/re-auth/delete (#198), suites list/detail two-panel (#200), catalog-driven check editor (#203). Plus Week-5 early-credit (worker runner-dispatch #146, ADF/Airflow 10-min polling #171, `trigger_bindings` CRUD #172, per-suite run target + dispatch ungate #215 — `Suite.target` + `run_target` resolver, `_trigger_suites` now dispatches `run_suite`; **manual run trigger + run/result read API** PR-C0b — `POST /suites/{id}/run` (edit-gated, resolves the target up front) + `api/v1/runs.py` `GET /runs`·`/runs/{id}`·`/pipeline_runs`, suite-authz-scoped, the read surface the Results page consumes) and the Python 3.13 + Snowflake 4 CVE refresh (ADR 0017, #129) + Dependabot batch (#202 pyarrow direct dep + 10 bumps). Plus the **custom-SQL check editor** (ADR 0019; backend #258 + Monaco frontend #259), the **`.env`/`.env.app` split** unblocking `Settings` `extra="forbid"` (#209), and the testing/CI hardening (frontend Stryker mutation harness #255; **CI is now an enforced merge gate** — 12 required checks on the `main` ruleset). Plus the **check version-history drawer** (#280) and the **admin control centre** (#289 — see below) closing out the week.
- **Week 5 (complete, 18/18)** — execution engine + scheduling. Async run paths across all datasource types (Snowflake + flat-file batch #298 + UC #299); `error`/`skip` operational statuses (#297/#298, closes #122); run progress API #301 + **live run-progress drawer (A3)**; cancel run #302 (folds #227); **scheduling backend (A7)** — `schedules` table + `dispatch_due_schedules` 60s beat (DST-aware, no-backfill, `FOR UPDATE SKIP LOCKED`) + CRUD — and the **scheduled-runs UI (A6)**; **run-now panel**; ADF/Airflow 10-min polling #171 + **gap recovery #307** + provider-agnostic `GET /orchestration/pipelines` #305; **run-history retention sweep (A8)** (PII-minimisation, not a row delete — keeps `metric_value` trends). +re-tracked: check target-table #215, Suite Triggers UI #216, run-enablement read API PR-C0b. Closeout: #147/#317 merged, #327 filed. Recent-runs audit table moved to Week 6.
**Results surface (Phase C, done through PR-C1):** the in-app **`/results` page** shipped — runs table + run-detail drill-down (per-check results, severity tags) + orchestration pipeline-runs tab + sidebar nav (Connections · Suites · Results · Profile), on the C0b read API; ADR 0018 (in-app page over Grafana) accepted; demo seed lands runs/results/pipeline-runs. Rich dashboard widgets (health cards, trends, per-suite bars, sample-row drill-down [#226](https://github.com/TheurgicDuke771/DataQ/issues/226), export) remain Week 6.
**Admin control centre (#289, closes Week 4):** workspace-admin authz (config `WORKSPACE_ADMIN_EMAILS` allowlist — generic identity axis, no Azure/Entra claim read, no migration) + `admin_service` + `GET /admin/{suites,users,access}` (unscoped — bypasses owned-or-shared) behind `require_workspace_admin` (403); `/me` exposes `is_workspace_admin`; frontend `/admin` page (Suites · Users · Access) + `Forbidden` 403 + admin-only nav via a shared `MeProvider`/`useMe`. Pulled the Week-7 prototype-adoption admin tasks forward.
**Next milestone:** **Week 7 — Deployment, hardening & docs.** Exit gate: production-ready v1 deployed to Azure, CI/CD live, team onboarded. Deploy **scaffolding** is in (#379 — apply-ready); the actual cloud deploy + Azure Monitor webhook setup + the FastMCP server (8 tools) + hardening/docs remain. The harness `terraform apply` stays **blocked on Azure RP registration (Owner)** per ADR 0021 — coordinate before the deploy-day tasks.
**Active blockers:** none in-repo. The cloud deploy/apply is gated on Azure RP registration (external, needs an Owner). Open follow-ups: profiler N+1 batching [#327](https://github.com/TheurgicDuke771/DataQ/issues/327); validation-handler `ctx` serialization [#371](https://github.com/TheurgicDuke771/DataQ/issues/371) + `SecretStore.delete` secret cleanup [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) (both filed in W6); Week-6 follow-ups [#348](https://github.com/TheurgicDuke771/DataQ/issues/348)/[#349](https://github.com/TheurgicDuke771/DataQ/issues/349)/[#351](https://github.com/TheurgicDuke771/DataQ/issues/351)/[#352](https://github.com/TheurgicDuke771/DataQ/issues/352); Week-4 nits [#197](https://github.com/TheurgicDuke771/DataQ/issues/197)/[#199](https://github.com/TheurgicDuke771/DataQ/issues/199)/[#204](https://github.com/TheurgicDuke771/DataQ/issues/204)/[#205](https://github.com/TheurgicDuke771/DataQ/issues/205); backend [#194](https://github.com/TheurgicDuke771/DataQ/issues/194)/[#195](https://github.com/TheurgicDuke771/DataQ/issues/195). See [docs/progress.md](docs/progress.md).

Update this section at the end of each week with: current week, the week's exit gate, and any open blocker issues by number. Per-PR task ticks go in `docs/progress.md` (PR-template checkbox).

---

## Appendix — Tech stack quick reference

| Layer | Tech |
|---|---|
| Backend framework | FastAPI (Python 3.13) |
| DQ engine | Great Expectations (GX Core) v1 — **pinned version** |
| Task queue | Celery + Redis |
| Database | PostgreSQL + Alembic |
| Frontend | React + Vite + Ant Design |
| SQL editor | Monaco |
| Auth | Azure AD (MSAL) |
| Secrets | Azure Key Vault |
| Hosting | Azure Container Apps (API + worker) · Azure Static Web App (UI) |
| Observability | Azure Application Insights + structlog |
| CI/CD | GitHub Actions |
| API docs | FastAPI Swagger + ReDoc |
| MCP | FastMCP (PrefectHQ) — 8 curated tools at `/mcp` |
| Python tooling | conda + Black + Ruff + mypy + pytest + Bandit |
| Frontend tooling | Prettier + ESLint + Vitest + React Testing Library |
| Secret scanning | betterleaks (pre-commit + CI) |
| SAST | Bandit + CodeQL |
