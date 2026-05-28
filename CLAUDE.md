# CLAUDE.md — DataQ project guide for AI assistants

> Single source of truth for any Claude / AI assistant working in this repo. Read this end-to-end before touching code.

---

## 1. Project summary

**DataQ** is the v1 evolution of SnowQ — a single-tenant data quality monitoring platform built around Great Expectations (GX Core). It runs DQ checks across **4 datasources** and integrates with **2 orchestration providers**.

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
│   │   ├── datasources/         # GX adapter per datasource type
│   │   └── mcp/                 # FastMCP tools (Week 7)
│   ├── alembic/
│   └── tests/
├── frontend/                    # React + Vite + Ant Design (Node, pnpm)
│   ├── src/
│   └── tests/
├── docs/
│   ├── architecture.md          # Mermaid architecture diagram
│   └── adr/                     # Architecture Decision Records
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
├── environment.yml              # conda env definition
├── conda-lock.yml
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

- **v1:** Great Expectations (GX Core) is the sole DQ framework across all 4 datasources. Unifies result schema, suite/check model, MCP tools, and the check editor.
- **v1.1:** Databricks Labs **DQX** will be added for DLT / streaming use cases (GX is batch-only and runs poorly on streaming). DQX will implement the same `UnityCatalogCheckRunner` interface introduced in Week 3 — UI exposes `engine: gx | dqx` toggle on UC suites.
- **Implication for Week 3:** keep the UC adapter thin behind `UnityCatalogCheckRunner` so v1.1 DQX swap-in doesn't ripple into the suite/check/result layer.

---

## 6. Working agreements (rules above feature work)

Full list (30 rules across 8 categories) lives in [CONTRIBUTING.md](CONTRIBUTING.md). Highlights:

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
- `gitleaks` secret scanning (pre-commit + CI).
- Bandit (Python SAST) + CodeQL.
- Dependabot for npm + pip + github-actions.

### Tooling (locked in Week 1, do not drift)
- **Python:** conda env (`conda create -n dataq python=3.11`) — *not* venv, *not* poetry.
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
- End-of-week quick scan from Week 2: vuln alerts, secret scan, OWASP spot check, Key Vault audit.
- Hard security review gate before Week 7 deploy.

---

## 7. Required reading before coding

1. [CONTRIBUTING.md](CONTRIBUTING.md) — full 30-rule working agreements + DoD + commit/branch conventions
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
| Severity tier weights (warn/fail/critical → health score) | `0005` (TBD W3) | Pending stakeholder agreement before W3 starts |
| ADF webhook auth (shared secret) + Key Vault rotation | `0006` (TBD W2) | Pending W2 |
| Airflow callback model (HMAC-signed) + polling fallback | `0007` (TBD W2) | Pending W2 |
| MCP mounted at `/mcp` with Azure AD auth | `0008` (TBD W7) | Pending W7 |
| Repo layout: flat monorepo (`backend/` + `frontend/`) | [0009](docs/adr/0009-flat-monorepo-layout.md) | Locked W1 |

---

## 10. Critical pointers (easy to get wrong)

- **`pipeline_runs` ≠ `runs`.** Orchestrator pipeline executions live in `pipeline_runs`; DQ suite executions live in `runs`. They link via `triggered_by: '<provider>:<pipeline_or_dag_id>:<provider_run_id>'`.
- **`trigger_bindings` is provider-agnostic.** Composite key (`provider`, `pipeline_or_dag_id`, `env`) → `suite_id`. Don't add an ADF-specific bindings table.
- **PII redaction at the logger level**, not at every call site. The redactor sits in `backend/app/core/logging.py`.
- **Backward-compatible migrations only.** Code that depends on a new column ships in a separate PR *after* the migration is deployed.
- **Secret scanning in pre-commit AND CI.** Don't rely on one alone.
- **Azure Monitor alert setup (Week 7) needs the deployed public API URL.** Deployment must come first; coordinate Container Apps ingress with infra/security before Week 7 to avoid a deployment-day surprise.
- **MCP tool descriptions are LLM-facing, not REST-API-facing.** Write them for natural-language selection; test against the 4 canonical NL queries in the roadmap.

---

## 11. What NOT to do

- ❌ Don't add ADF or Airflow as a queryable datasource in the connection editor / check editor / suite model.
- ❌ Don't bypass the `OrchestrationProvider` abstraction with provider-specific branching in service code.
- ❌ Don't `git commit --no-verify` past hooks. If a hook fails, fix the underlying issue.
- ❌ Don't commit `.env` files. Use `.env.example` as the template.
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
| Working agreements (full 30-rule list) | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Execution plan (sequencing, gates, risks) | Reference plan: `~/.claude/plans/hey-create-the-project-inherited-flute.md` (will be committed to `docs/execution-plan.md` in a follow-up PR) |
| Memory (cross-session AI context) | `~/.claude/projects/-Users-arijit-Coding-Python-DataQ/memory/` |

---

## 13. Status & current milestone

> **Detailed task-level status** lives in [docs/progress.md](docs/progress.md) — mirrors the 100-task roadmap, updated per PR. This section carries only the headline.

**Current week:** Week 1 — Foundation (~80% — PR 4 remaining)
**Week-1 exit gate:** A logged-in user can hit a FastAPI endpoint that triggers GX against Snowflake DEV and persists a result row.
**Completed since project start (2026-05-24):**
- PR 0 governance bundle (#1–#24, #44, #55) — onboarding docs, ADRs, CODEOWNERS, templates, Entire CLI hooks
- PR 1 (#37) — coding structure & tooling lock
- PR 2 a/b/c (#39, #40, #41) — Docker Compose, structlog + error envelope + FastAPI skeleton, SQLAlchemy models + Alembic baseline
- PR 3 a/b/c (#53, #56, #63) — Azure AD SSO end-to-end (backend MSAL + SecretStore abstraction + frontend MSAL + `/me`)
**Next milestone:** PR 4 — GX + Snowflake probe endpoint (closes Week 1 exit gate)
**Active blockers:** none. See [docs/progress.md](docs/progress.md) for the active-issues list.

Update this section at the end of each week with: current week, the week's exit gate, and any open blocker issues by number. Per-PR task ticks go in `docs/progress.md` (PR-template checkbox).

---

## Appendix — Tech stack quick reference

| Layer | Tech |
|---|---|
| Backend framework | FastAPI (Python 3.11) |
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
| Secret scanning | gitleaks (pre-commit + CI) |
| SAST | Bandit + CodeQL |
