# CLAUDE.md — DataQ project guide for AI assistants

> Single source of truth for any Claude / AI assistant working in this repo. Read this end-to-end before touching code.

---

## 1. Project summary

**DataQ** is a single-tenant data quality monitoring platform built around Great Expectations (GX Core). It runs DQ checks across **5 datasources** and integrates with **3 orchestration providers**.

| Layer | Components |
|---|---|
| **Datasources (you can write checks against)** | Snowflake (DEV/QA/UAT), ADLS Gen2, AWS S3, Unity Catalog (Databricks), Apache Iceberg (native `pyiceberg` read — ADR 0030) |
| **Orchestration providers (monitor + trigger only — NOT datasources)** | Azure Data Factory (ADF), Apache Airflow, dbt (ADR 0029) |
| **Backend** | FastAPI + Celery + Redis + PostgreSQL + Alembic |
| **Frontend** | React + Vite + Ant Design + Monaco editor (generic OIDC — `oidc-client-ts`) |
| **Auth / secrets** | OIDC (Azure AD validated; provider-neutral `AUTH_*` contract) + Azure Key Vault |
| **Deploy** | Azure Container Apps (API + worker + frontend; frontend is the sole public surface, api internal — ADR 0028 §5) |
| **Observability** | Azure Application Insights + structlog |
| **AI integration** | FastMCP (8 curated tools mounted at `/mcp`) — Claude Desktop / Claude.ai / Copilot / Cursor |

Timeline: **8 weeks** to v1. Scope: single tenant, suite-level access sharing, Azure-hosted.

---

## 2. Architecture at a glance

See [docs/architecture.md](docs/architecture.md) for the full diagram (Mermaid — renders on GitHub).

```
Browser ──HTTPS──► Frontend Container App (nginx SPA, sole public ingress)
AI clients ──MCP/HTTP──► │  proxies /api + /mcp + /healthz same-origin
                         ▼
                    FastAPI (Container Apps, INTERNAL ingress) ──► PostgreSQL
                         │  │
                         │  └──► Celery worker ──► GX execution ──► Snowflake / ADLS / S3 / UC
                         ├──► Redis (task queue)
                         ├──► Key Vault (secrets)
                         └──► App Insights (observability)

ADF ──► Azure Monitor alert rule ──► webhook ──► POST <frontend>/api/v1/orchestration/events/adf ──► (proxied) api
Airflow ──► on_success/on_failure_callback ──► POST <frontend>/api/v1/orchestration/events/airflow ──► (proxied) api
FastAPI ──► MS Teams / Slack / email (alerts, ResultPublisher seam)
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
- Apache Iceberg (native `pyiceberg` read — ADR 0030; engine-registered Iceberg tables also work zero-code under the `snowflake`/`unity_catalog` connections)

**Orchestration providers** are NOT datasources. They are workflow engines whose pipelines/DAGs we observe and react to. Their *only* three responsibilities in DataQ:

1. **Monitor** pipeline/DAG runs → stored in `pipeline_runs` table (separate from `runs` / `results`).
2. **Detect failure** in near-real-time via provider-specific event channels (webhook for both).
3. **Trigger suite execution on successful completion** via `trigger_bindings` (`provider`, `pipeline_or_dag_id`, `suite_id`, `env`). Failure events alert the user but do NOT trigger suite runs.

All three providers implement a single `OrchestrationProvider` interface — ADF is the reference implementation, Airflow is the second, dbt (ADR 0029) is the third (artifact-poll + HMAC callback, no host REST API). **Never hardcode ADF-only logic; always go through the abstraction.**

| Provider | Event channel | Auth | Polling fallback |
|---|---|---|---|
| ADF | Azure Monitor alert → webhook | Shared secret header (Azure Monitor's only mode) | ADF REST API, 10 min |
| Airflow | DAG `on_*_callback` → webhook | HMAC-signed payload (signing key in Key Vault) | Airflow REST API `dagRuns`, 10 min |
| dbt | post-build callback → webhook | HMAC-signed payload (app-level signing key) | poll `run_results.json` artifact (adls/s3/file), 10 min |

Airflow callbacks require the user to add a snippet to their DAGs (we can't mutate them). Polling is the documented fallback.

**Anti-pattern (do not do this):** treating ADF/Airflow as a 5th/6th datasource in the connection editor, check editor, or suite model.

---

## 5. Framework choice — GX-only for v1

- **v1:** Great Expectations (GX Core) is the sole DQ framework across all datasources. Unifies result schema, suite/check model, MCP tools, and the check editor. Every v1 check is a GX **expectation** (`check.kind = 'expectation'`).
- **v1.1:** Databricks Labs **DQX** will be added for DLT / streaming use cases (GX is batch-only and runs poorly on streaming). DQX will implement the same `UnityCatalogCheckRunner` interface introduced in Week 3 — UI exposes `engine: gx | dqx` toggle on UC suites.
- **Monitor-kind seam (do-now, Week 3):** not every monitor is a GX expectation. A `check.kind` discriminator (`expectation` in v1; `freshness | volume | schema_drift | anomaly | comparison` reserved — freshness/volume shipped post-v1 (#426/#437) and **`comparison` shipped v1.1 W3** (ADR 0015, #791–#795); `schema_drift`/`anomaly` remain reserved) + numeric `metric_value` on results let v1.x auto-monitors slot in without a check/result schema rewrite. This seam is **orthogonal to the datasource seams** (`CheckRunner`, `ConnectionAdapter`): it varies by *monitor kind*, not datasource. See ADR `0012` (and `0014` for the reserved `comparison` / cross-dataset reconciliation kind) and post-v1 roadmap Theme A. Most real incidents are freshness/volume, not value-level — this is the leap from "GX runner" to DQ platform.
- **Week-3 outcome (done):** the UC run path is thin behind `UnityCatalogCheckRunner` (reads the table into a GX DataFrame asset — the DQX swap-in shape), and `check.kind` + `metric_value`/`duration_ms` shipped in the one threshold migration, so the monitor-kind impls won't ripple into the suite/check/result layer later.

---

## 6. Working agreements (rules above feature work)

Full list (40 rules across 8 categories) lives in [CONTRIBUTING.md](CONTRIBUTING.md). Highlights:

### Commit & change discipline
Per-functionality workflow, in order:
1. **One functionality per commit** (where possible).
2. **Test coverage for the functionality** (unit/integration as applicable — the ≥80% CI gate, live since Week 8, covers this).
3. **Docs updated if required** (CLAUDE.md / ADR / CONTRIBUTING / user docs — whichever the change touches).
4. **Agentic code-review on the PR** — spawn `/code-review` (never an inline self-review only) and post findings to the PR as inline comments (`/code-review --comment`).
5. **Fix issues found in the same PR** where feasible.
6. **File a GitHub issue for anything deferred** — never drop a finding silently. Use `gh issue create`; the fixing PR must include `Fixes #N`.
7. **Full CI gate must pass** (lint/format/types/tests/security — see below).
8. **Squash-merge to `main`.**

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

1. [CONTRIBUTING.md](CONTRIBUTING.md) — full 40-rule working agreements + DoD + commit/branch conventions
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

## 9. Key design decisions (ADR index)

The full decision index — one line per ADR with status, 0001–0029 to date — lives at **[docs/adr/README.md](docs/adr/README.md)** and is the **single source of truth** (this section used to duplicate it as a table and the two drifted; it no longer does). Read the index before coding and open the individual ADR whenever a decision bears on your change. The day-to-day operating rules those decisions distill into are already captured in §4–§6, §10 and §11 of this file.

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
- ❌ Don't deepen Azure lock-in: no reading Entra/OIDC provider claims in route/service code (depend on the generic `get_current_user`), no hardcoded Azure resource names/endpoints in business logic, no Azure-only assumptions baked into container images. Azure is one impl behind each seam — see ADR [0010](docs/adr/0010-provider-agnostic-infrastructure-seams.md) / [0013](docs/adr/0013-marketplace-distribution-and-anti-lock-in.md).
- ❌ Don't `git commit --no-verify` past hooks. If a hook fails, fix the underlying issue.
- ❌ Don't commit `.env` files. Use `.env.example` / `.env.app.example` as the templates.
- ❌ Don't put a credential — **even a local/mock one** — in any git-tracked file (templates, `scripts/`, CI, compose). Env templates ship the secret keys **blank** with the shape in a comment; `scripts/setup.sh` generates the local-dev password into the gitignored `.env`/`.env.app` on first run. Non-secret config defaults and non-secret identifiers (db/user name) may stay populated.
- ❌ Don't drop columns in the same PR as the code change that stops using them. Two-step it.
- ❌ Don't fix bugs silently. Raise a GitHub issue, then PR with `Fixes #N`.
- ❌ Don't batch unrelated changes into one commit. One functionality per commit.
- ❌ Don't track GX Core at "latest." Pin the version in `environment.yml` — GX v1 API has drifted across point releases.
- ❌ Don't add a dependency under a strong-copyleft or source-available license (GPL, AGPL, SSPL, BUSL/Elastic, Commons-Clause) — DataQ ships MIT (ADR [0031](docs/adr/0031-oss-byol-distribution-licensing.md), CONTRIBUTING rule 40); weak copyleft (LGPL/MPL) is OK with notices. Exceptions need an ADR.
- ❌ Don't use venv or poetry for backend dev. Conda only.
- ❌ Don't write the MCP layer before Week 7. The service layer must stabilise first.

---

## 12. Where things live

| Artifact | Location |
|---|---|
| Product roadmap (100 tasks, 8 weeks) | [context/DataQ_platform_roadmap.md](context/DataQ_platform_roadmap.md) |
| System architecture diagram | [docs/architecture.md](docs/architecture.md) |
| Architecture Decision Records | [docs/adr/](docs/adr/) |
| Working agreements (full 40-rule list) | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Live task tracker (post-v1, per-PR status) | [docs/progress.md](docs/progress.md) — the completed v1 ledger is archived at [docs/progress-v1.md](docs/progress-v1.md) |
| **Deploy runbook + pre-/post-deploy checklists** | [deploy/README.md](deploy/README.md) — provisioning, the `workflow_dispatch` Deploy flow, and the **pre-deploy** (CI green, docs current, migration-safe) + **post-deploy smoke** (login, UI renders, every high-level flow works, infra rolled) checklists. **Run both around every deploy.** |
| Memory (cross-session AI context) | `~/.claude/projects/-Users-arijit-Coding-Python-DataQ/memory/` |
| **Harness ad-hoc test window script** | `~/Coding/Python/DataQ-harness/scripts/harness_window.sh` (harness-side, **not git-tracked** — ADR 0021). The harness compute is **stopped by default** since 2026-07-04 (Azure cost wind-down, #590 — ~CAD 17/day awake vs ~0 stopped); this script opens a test window: `window [--adf] [--dags] [--dbt]` = wake (redis→Airflow→workers→trigger + ADF triggers) → run the flows (mockdata jobs as manual executions; `--dags` REST-triggers the cron DAGs; `--adf` create-runs the Flow-A ADF pipelines; `--dbt` resume+starts the `dbt-lineage` ACA job (#609 — dbt Core builds the `ANALYTICS_STG` views + `ANALYTICS` mart dynamic tables, artifacts → ADLS `raw/dbt/latest` for the #611 poller) and re-suspends it — the `--adf` pipelines, the `flow_a_snowflake_load` DAG **and `--dbt`** need live Snowflake, the UC/medallion DAGs don't) → sleep again, verified. `status`/`start`/`run`/`stop` also run standalone; `status` and `stop` cover the `dbt-lineage` job (its nightly `0 2 * * *` cron is disarmed by `stop` like the mockdata crons). Full-cycle validated 2026-07-04 (11.5 min, all flows green; `--dbt` leg added 2026-07-05, not yet window-validated). |

---

## 13. Status & current milestone

> **Detailed task-level status** lives in [docs/progress.md](docs/progress.md) — the live post-v1 tracker, updated per PR (the completed v1 ledger, which mirrored the 100-task roadmap, is archived frozen at [docs/progress-v1.md](docs/progress-v1.md)). This section carries only the headline.

**Current week:** **v1 DONE — `v1.0.0` tagged 2026-07-04 (Week 8 closed 29/29 + exit gate MET; epic #177 + the W8 milestone closed; retro at [docs/retro-v1.md](docs/retro-v1.md); **v1.1 cycle planned 2026-07-04** — see [docs/progress.md](docs/progress.md) §Cycle plan).** The gates are live in CI: backend `--cov-fail-under=80` in `pyproject.toml` (98.3% / 1273 tests on main, after closing the four sub-80% modules — #557) + frontend `lines: 80` over ALL of `src/` via `pnpm test:coverage` (87.8% / 334 tests — #558). The W8 batch #556–#560 also closed #385 (CORS activation tests, #556), #205 (catalog↔GX contract test, #559), #352 (dashboard Avg-Duration + real deltas, #560), and #128 (full-stack E2E — the gates were its last half). **Go-live close (2026-07-03/04, all cleared):** pre-tag QA — qa-verifier workout NO-GO on the NUL-byte 500 (#567) → fixed #570 (also closed #371) → re-run GO (21 injection points 422, zero 500s); prod redeployed `2fa05333` + re-probed live; live prod workout as non-admin Olivia 15/15 + webhook-auth hostility 7/7 401s (#569 closed, both halves); **ops/renewals timers consciously SKIPPED** (demo-scoped credentials; expiry self-signals via #419 alerting; recovery = re-mint + KV update; G-i teardown covers the end state). Checklist progress 2026-07-03: #553 closed (#562, bare pip-audit green) · mutation spike done (mutmut `dashboard_service` 436/436 killed; Stryker 82.35%; survivors → #563, config retarget #564) · prod deploy + smoke re-green done (`8dee4f4a` images; Flows A/B/C `succeeded` as dataq-admin — flat-file suite recreated post-#540; Azure CLI pre-authorized on the API scope for non-interactive bearers, #565 + TF import) · decisions recorded: **ADR 0026 deferred post-v1** (PATs-first shape confirmed; Basic auth rejected — see the ADR's decision record) + **Databricks Free-Edition** (demo/eval OK, paid workspace before commercial use — gap G-h) + **pre-marketplace harness teardown** (gap G-i: strip Flows A/B/C + harness connections + demo users before any marketplace/customer-facing artifact; also deploy/README.md). **Week 7 — Deployment, hardening & docs — COMPLETE (41/41, closed 2026-07-03; milestone + epic #176 closed); DataQ v1 is DEPLOYED TO AZURE and reachable** (Weeks 1–7 complete, all exit gates met). **Cloud deploy (2026-06-28):** the in-repo Terraform (`deploy/terraform/azure/`, ADR 0024) stood up the app stack into `dataq-rg` — `dataq-app-{api,worker}` + `dataq-app-migrate` job (GHCR slim image, ADR 0025) on the **shared `dataq-cae`** Container Apps env, `dataq-app-redis` (password-auth), Key Vault (UAMI) + App Insights + Log Analytics, and **`dataq-app-web`** Static Web App with the api **linked as same-origin `/api` backend**. The app's DB is a distinct **`dataq`** database + least-priv **`dataq_app`** role on the **shared `dataq-pg-wus3-*`** server (1-of-each free/trial cap → env + Postgres shared with the harness, neutral-named `purpose=dataq-shared`; harness Postgres backed-up→recreated→restored). Azure AD **SSO app registrations** (API + SPA) created in TF + wired; migrate job ran `alembic upgrade head`; API healthy (401 = auth-enforced), SPA + deep-links 200, GitHub OIDC secrets/vars + `production` env set. Fixed **#393** (opencensus AzureLogHandler `lock=None` on Py3.13) en route. **GHCR package→repo connect done** (Actions-access grant → CI's `GITHUB_TOKEN` can push) and the **Deploy workflow validated end-to-end** (#403 fixed the migrate-command + frontend-pnpm bugs #401/#402; build→push→`alembic upgrade head`→ACA roll + SWA deploy all green on `v6`). **Post-deploy hardening (2026-06-28):** two production bugs surfaced and fixed — **#405** (Celery beat crashed on startup: the embedded `worker -B` beat re-nulled `self.lock` inside the opencensus `AzureLogHandler.createLock` fork on Python 3.13, silently killing ALL periodic tasks — orchestration polling, scheduled-suite dispatch, gap recovery, and sample-failure purge; fixed by making `createLock` idempotent in `backend/app/core/logging.py` + a network-free regression test; **#407** merged) and **#406** (deployed app couldn't read Key Vault: `AzureKeyVaultStore` called `DefaultAzureCredential()` with no args but the api+worker container runs a USER-assigned managed identity and `AZURE_CLIENT_ID` was unset — blocked connection tests, suite runs, AND orchestration polling; fixed by adding `AZURE_CLIENT_ID = azurerm_user_assigned_identity.app.client_id` to `local.app_env` in `deploy/terraform/azure/containerapps.tf`; **#408** merged). Backend image `:v7` built+pushed from main (with #405+#406); api+worker rolled to v7; App Insights re-enabled on the worker (the #405 mitigation of temporarily dropping `APPLICATIONINSIGHTS_CONNECTION_STRING` on the worker is reverted) — landed as **#409** (`image_tag` default v4→v7). **Orchestration polling is now live end-to-end** — beat starts clean (zero NoneType crashes) with App Insights on, api healthy (401, auth-enforced), ADF+Airflow connections polling via the 10-min beat fallback, Key Vault secrets read successfully. **Post-deploy feature batch (2026-06-29, all merged; prod image `:v7`→`:v10`):** Slack + email alert publishers behind the `ResultPublisher` seam (#413, `:v8`), column-aware failing-sample redaction (#417) + the #383/#384/#395/#423 hardening batch (`:v9`, bump #414), URL-encode DB password (#421/#395), always-alert operationally-failed runs (#419), alerting upsert race fix (#420/#384), per-run check outcome in the runs table (#425/#423), mypy gate over `backend/tests` (#418), and — **pulling ADR 0012 post-v1 Theme A forward — freshness & volume monitor-kinds end-to-end** (run engine #426 + authoring path & check-editor UI #437, `:v10`, bump #438). Three post-v1 design docs landed (#422/#430/#436) and are consolidated in **[context/post-v1-roadmap.md](context/post-v1-roadmap.md)** (the single post-v1 home + week-wise-task-generator input). **W7 in-repo work now DONE:** the **FastMCP 8-tool server** at `/mcp` (Azure-AD `JWTVerifier`-validated, fail-closed; ADR 0008, #460); the **hardening/docs pass** — prod-docs gate (#464), Swagger completeness + error-shape audit (#465), deployment guide + complete env-var reference (#468); **consistency hardening** — trigger-dedup index (#456, closes #308) + stuck-run reaper (#458, closes #309); the **visual-fidelity pass** (#459); and the W1–6 deferred + not-started triages (#463/#467, closing #169/#170). **W7 close-out batch (2026-07-01/02, all merged):** OTel **request/task spans** to App Insights (#525 — vendor-neutral core `backend/app/core/tracing.py` + Azure exporter-only, module-scope FastAPI + producer/consumer Celery instrumentation, secret/PII-safe span attributes, `dataq.request_id` span↔log join; opencensus→OTel log migration → #524) · **vault lazy-import test coverage** (#523, `secrets.py` 100%) · **#17 MCP polish** (#522 — this file's `.mcp.json` appendix + CONTRIBUTING rule 39 (numbered 38 pre-#547)) · **Playwright E2E expansion** (#526 schedules/triggers/notifications panels + #527 run-detail sample/dashboard/check-editor variants/admin — 25 specs green in CI) · **opt-in live-smoke lane** (#531 — `frontend/e2e-live/` gated on `E2E_LIVE_BASE_URL` with captured-OIDC session + `e2e_smoke.py` `DATAQ_BEARER` mode + runbook checklist; never in CI) · **user-docs enrichment** (#528 — notifications/scheduling/best-practices/feature-matrix pages; #532 filed) · **MCP tool-expansion candidates** (#530 — post-v1 Theme 13 + issue #529). **LIVE SMOKE RUN + #492 DONE (2026-07-02, via the #531 lane):** browser lane 3/3 · `e2e_smoke.py` bearer-mode vs prod · **Flows A/B/C verified green** (Snowflake ×3 / UC / flat-file) · **#525 spans confirmed in App Insights** · **MCP 4-query protocol smoke passed** vs live `/mcp` · **#492 closed** — Action Group + `PipelineFailedRuns` metric alert on the harness factory; a deliberate `pl_dataq_smoke_fail` failure was visible in DataQ **4m14s** after fire (Common-Alert-Schema → `AlertPing` → immediate-poll ingest, #534). Smoke fallout fixed same-day and deployed: UC dialect regression (#535→#537), traceback-locals credential leak (#536→#538 — **Databricks PAT rotation required**), suite-delete FK cascade (#540→#542); deploy-workflow frontend flake filed (#539). **W7 externals closed (2026-07-02):** team onboarding discharged (six demo Entra users cross-shared at every ADR-0027 tier on the deployed app — no separate session, solo-dev) and KV purge-protection **decided: left off** (demo-scoped vault, destroy/re-apply flexibility; recorded in `deploy/README.md`) — and the last row closed 2026-07-03: **MCP client-config E2E passed** (#550 — a real VS Code `.vscode/mcp.json` against live `/mcp/` exercised all 8 tools end-to-end; client setup guide moved to `docs/mcp-setup.md`, README keeps a lean pointer + the trailing-slash `/mcp/` guidance) — **Week 7 is COMPLETE (41/41)**. Week-6 close: the **alerting backend track** — `ResultPublisher` seam (#366), Teams adaptive card + publisher (#367), severity-aware routing (#368), dedup (#369), suppression/snooze (#370), per-suite notification config (#373) — plus the **prototype Phase 5–6 screens**: Profile content (#374), Workspace Settings (#375), Admin layout-reconcile (#376), standard 4xx/5xx error pages (#377), and the per-suite **notification config UI** (#378). Earlier Week-6: Results scaffold (PR-C1), Enhanced Monitoring Dashboard + run-detail route (#333), results filter bar + orchestration poll/correlation (#347), drawer→page restructures (#350), layout/prototype-fidelity polish (#353), and redacted sample failing rows (#365, closed #226). **Week-7 early-credit:** Azure deploy scaffolding (#379 — frontend Dockerfile + ACA/SWA manifests + parameterized deploy workflow + CORS middleware + prod env reference; manual-trigger only, the actual apply stays blocked on Azure RP registration per ADR 0021). _All Week-6 feature work **merged to `main`** as the stacked PR chain #366→#379; follow-ups #380 (W6 close-out docs), #381 (deploy migration-gate + doc reconcile — a Week-7 CI/CD task landed early), and #390 (live run-progress per-status histogram fix, closes #316) merged after._ **Cloud-neutral cutover (2026-07-01, ADR 0028 §5 — DONE):** the deployed frontend moved **Static Web App → a Container App** (`dataq-app-frontend`), now the **sole public surface**, running the one generic nginx image with runtime `DATAQ_AUTH_*` OIDC config (**MSAL retired** for a generic oidc-client-ts; validated live as Olivia → dashboard). The **api moved to internal ingress** (reached only via the frontend nginx proxy `/api` + `/mcp` + `/healthz`); the SWA (`dataq-app-web`) is **destroyed**. Landed as **#509** (cutover) → **#510** (lifecycle guards — `ignore_changes` on container images so applies never roll prod back to `var.image_tag`, + on the api `identifier_uris` so applies never strip the token audience) → **#511** (three ACA gotchas: nginx must proxy **HTTP/1.1** or ACA ingress 426s; api ingress **HTTP + `allow_insecure_connections`**; **orphaned SWA-EasyAuth** on the api disabled via `az containerapp auth update --enabled false` — it 401'd every request post-SWA-destroy; DataQ does its own `fastapi-azure-auth` validation). Prod frontend image `:v2`; URL `https://dataq-app-frontend.purplefield-f7322a1b.westus2.azurecontainerapps.io`. Follow-up **#512** (multi-arch frontend QEMU build was impractically slow/flaky — 45-min stall → cancel) **fixed in #518**: pin the frontend Dockerfile `build` stage to `--platform=$BUILDPLATFORM` so the arch-neutral Vite build runs natively once and only the per-arch nginx `serve` stage differs — frontend publish dropped from a 45-min stall to **~1min**, and a native-arm64 `:latest` multi-arch is published reliably again. AWS/GCP deploy IaC stays post-v1 (#505).
**Week-8 exit gate:** ≥80% coverage gate enforced in CI across backend, API, frontend. — **met** (2026-07-03: backend/API gate `--cov-fail-under=80` on every pytest run incl. CI (#557); frontend all-src `lines: 80` via `pnpm test:coverage` in CI (#558); baselines 98.3% / 87.8% at flip).
**Week-7 exit gate:** Production-ready v1 deployed to Azure, CI/CD live, team onboarded. — **met** (app live on ACA behind the sole-public frontend; Deploy workflow green end-to-end with retry/verify hardening; six demo users onboarded across every ADR-0027 access tier).
**Week-6 exit gate:** Full results dashboard live across all source types; alerts firing with suppression. — **met** (Enhanced Monitoring Dashboard + Results page + run-detail across Snowflake/flat-file/UC; the `ResultPublisher`→Teams alerting path with severity-aware routing, dedup, per-check snooze suppression, and per-suite config + UI). The deferred live warehouse/file smoke was discharged 2026-07-02 (Flows A/B/C green via the #531 lane).
**Week-5 exit gate:** Async runs with live progress across all datasource types; scheduling operational. — **met** (run paths Snowflake + flat-file batch + UC; `GET /runs/{id}/progress` + the live-progress drawer; cancel; cron `schedules` + 60s dispatcher + scheduled-runs UI; ADF/Airflow polling + gap recovery + `/orchestration/pipelines`; PII-minimisation retention sweep). The deferred live warehouse/file smoke was discharged 2026-07-02 (Flows A/B/C green via the #531 lane).
**Week-4 exit gate:** Users can configure any connection type and author checks end-to-end in the UI. — **met** (connection manager UI for all six types + edit/re-auth/delete; suites list/detail; catalog-driven check editor + Monaco custom-SQL; dry-run preview; column profiler panel; run-target editor; export/import + sharing panels; check version-history drawer; **admin control centre** — #289). The deferred live warehouse/file smoke was discharged 2026-07-02 (Flows A/B/C green via the #531 lane).
**Week-1 exit gate:** GX against Snowflake DEV persists a result row. — **met** via `POST /api/v1/_probe/snowflake-suite` → Celery `run_suite` → `run_service` → `results` (live Snowflake run fails-soft pending creds — smoke discharged 2026-07-02 via the #531 lane).
**Week-2 exit gate:** All six connection types configurable + testable via API, credentials in the SecretStore. — **met** (Snowflake / ADF / Airflow / ADLS Gen2 / S3 / Unity Catalog behind the `ConnectionAdapter` seam + registry; real Key Vault provisioning landed with the W7 deploy — ADR 0024, #406/#408).
**Week-3 exit gate:** Full check CRUD across Snowflake / flat files / Unity Catalog + column profiler live. — **met** (suite & check CRUD + sharing + export/import + dry-run; severity tiers + monitor-kind seam; column profiler on all 4 datasources; the three GX `CheckRunner`s — Snowflake / flat-file / UC — behind the shared `gx_runner`; flat-file batch resolution; end-to-end datasource-run integration tests). The deferred live warehouse/file smoke was discharged 2026-07-02 (Flows A/B/C green via the #531 lane).
**Completed since project start (2026-05-24):** see [docs/progress-v1.md](docs/progress-v1.md) (the archived v1 per-PR ledger). Headlines:
- **Week 1** — governance + tooling lock (#1–#37), structlog/error-envelope/FastAPI skeleton + SQLAlchemy/Alembic baseline (PR 2), Azure AD SSO end-to-end (PR 3), async backbone + Snowflake GX adapter + run/result persistence (PR 4).
- **Week 2** — connection manager for all six types (Snowflake/ADF/Airflow/ADLS/S3/UC) behind the `ConnectionAdapter` seam; ADF + Airflow orchestration event receivers (secret-in-URL / HMAC) + connection adapters; re-auth endpoint; ADRs 0005/0006/0007/0010/0011/0012.
- **Week 3** — suite & check CRUD + sharing + export/import + dry-run; severity tiers (ADR 0005/0016) + monitor-kind seam (ADR 0012); column profiler (all 4 datasources); GX `CheckRunner`s (Snowflake/flat-file/UC) on the shared `gx_runner`; batch resolution; integration tests. Plus the testing-discipline upgrade (adversarial harness + mutation spikes, CONTRIBUTING rule 4a).
- **Week 4 (complete, 26/26)** — frontend: app-router shell + connections list (#191), spec-driven add-connection drawer (#196) + Snowflake key-pair (#193), connection edit/re-auth/delete (#198), suites list/detail two-panel (#200), catalog-driven check editor (#203). Plus Week-5 early-credit (worker runner-dispatch #146, ADF/Airflow 10-min polling #171, `trigger_bindings` CRUD #172, per-suite run target + dispatch ungate #215 — `Suite.target` + `run_target` resolver, `_trigger_suites` now dispatches `run_suite`; **manual run trigger + run/result read API** PR-C0b — `POST /suites/{id}/run` (edit-gated, resolves the target up front) + `api/v1/runs.py` `GET /runs`·`/runs/{id}`·`/pipeline_runs`, suite-authz-scoped, the read surface the Results page consumes) and the Python 3.13 + Snowflake 4 CVE refresh (ADR 0017, #129) + Dependabot batch (#202 pyarrow direct dep + 10 bumps). Plus the **custom-SQL check editor** (ADR 0019; backend #258 + Monaco frontend #259), the **`.env`/`.env.app` split** unblocking `Settings` `extra="forbid"` (#209), and the testing/CI hardening (frontend Stryker mutation harness #255; **CI is now an enforced merge gate** — 12 required checks on the `main` ruleset). Plus the **check version-history drawer** (#280) and the **admin control centre** (#289 — see below) closing out the week.
- **Week 5 (complete, 18/18)** — execution engine + scheduling. Async run paths across all datasource types (Snowflake + flat-file batch #298 + UC #299); `error`/`skip` operational statuses (#297/#298, closes #122); run progress API #301 + **live run-progress drawer (A3)**; cancel run #302 (folds #227); **scheduling backend (A7)** — `schedules` table + `dispatch_due_schedules` 60s beat (DST-aware, no-backfill, `FOR UPDATE SKIP LOCKED`) + CRUD — and the **scheduled-runs UI (A6)**; **run-now panel**; ADF/Airflow 10-min polling #171 + **gap recovery #307** + provider-agnostic `GET /orchestration/pipelines` #305; **run-history retention sweep (A8)** (PII-minimisation, not a row delete — keeps `metric_value` trends). +re-tracked: check target-table #215, Suite Triggers UI #216, run-enablement read API PR-C0b. Closeout: #147/#317 merged, #327 filed. Recent-runs audit table moved to Week 6.
**Results surface (Phase C, done through PR-C1):** the in-app **`/results` page** shipped — runs table + run-detail drill-down (per-check results, severity tags) + orchestration pipeline-runs tab + sidebar nav (Connections · Suites · Results · Profile), on the C0b read API; ADR 0018 (in-app page over Grafana) accepted; demo seed lands runs/results/pipeline-runs. Rich dashboard widgets (health cards, trends, per-suite bars, export) and the redacted sample-row drill-down ([#226](https://github.com/TheurgicDuke771/DataQ/issues/226), closed by #365) shipped in Week 6.
**Admin control centre (#289, closes Week 4):** workspace-admin authz (config `WORKSPACE_ADMIN_EMAILS` allowlist — generic identity axis, no Azure/Entra claim read, no migration) + `admin_service` + `GET /admin/{suites,users,access}` (unscoped — bypasses owned-or-shared) behind `require_workspace_admin` (403); `/me` exposes `is_workspace_admin`; frontend `/admin` page (Suites · Users · Access) + `Forbidden` 403 + admin-only nav via a shared `MeProvider`/`useMe`. Pulled the Week-7 prototype-adoption admin tasks forward.
**Next milestone:** **v1.1 Week 3 — Azure wind-down + local-first posture (due 2026-07-25).** **SECURITY — PATs WERE LOGGED IN PLAINTEXT (#849, fixed #851; both prod PATs REVOKED + re-minted).** `fastapi_azure_auth` logs the raw bearer on a failed JWT decode, and a PAT is never a valid JWT — and `Security(azure_scheme)` is a FastAPI **dependency**, so it resolves BEFORE `get_current_user`'s body: the "PAT tried first by prefix" ordering the code documents was **never actually first**. Every PAT request wrote a live bearer credential (incl. the workspace-admin's) into App Insights and then succeeded — so nothing looked wrong. Fixed in two layers: the PAT never reaches the validator, AND the logger scrubs bare credentials. **The load-bearing lesson: the call site that leaked was NOT OURS.** §10 already says redact at the *logger*, not the call site — grepping our own code for "places we log tokens" could never have found this, because we don't log it; a dependency does. (MCP had it right: it checks the prefix *before* calling the verifier.) Same session, **#852**: the OTel exporter was **amplifying its own logs** — `azure.core`'s HTTP policy logs every request+response *including the exporter's own uploads*, which re-enter the bridge (19,206 traces/30 min, ~10/sec); the loop-breaker had `opentelemetry` + `azure.monitor.opentelemetry` but missed `azure.core`. **The cost wasn't volume — the noise DROWNED THE SIGNAL**: mid-outage, a query for the app's own `orchestration_poll_completed` returned nothing usable. **Asset visibility corrected (#845/#846/#847, PR #848 — ADR 0034 decision 5 AMENDED):** the lineage graph was **defeating the no-leak 404**, handing a non-grantee the name/namespace/env of assets the endpoint 404s precisely so it cannot confirm they exist (found in prod: a click → "asset not found"). Neighbours outside your grants are now **redacted, not omitted** (anonymous node + count) — dropping them would assert "nothing consumes this table", and **we do not fix a leak by shipping a lie**. And a **suite-less** asset was wrongly treated as "outside your grants": redaction protects a *grant*, and an asset nobody granted is protected by nothing — so browse (which hid them even from admins, contradicting the detail endpoint), the detail endpoint, and the graph now derive from **one rule**, pinned by a test that all three agree. **PROD LINEAGE WAS DARK FOR SIX DAYS — RESTORED 2026-07-13 (#828).** The dbt connection's ADLS SAS **expired 6 Jul**, so every 10-min poll failed `ClientAuthenticationError` and DataQ never read `manifest.json`; the dbt builds stayed green and kept publishing artifacts nobody consumed. (Marquez was a red herring — the live lineage path is the #759 dbt-manifest one.) Re-minted the SAS (1y expiry), re-ran the build, and the chain fired end to end. **Both systemic defects are now FIXED and merged (#839): (1) a failing orchestration poll is a fact about the connection (`last_polled_at`/`last_poll_error`/`consecutive_poll_failures`, migration `d1e2f3a4b5c6`) — the connections list shows a failure badge with the count and the lineage card warns instead of showing a confident empty state; the stored reason is CLASSIFIED, never raw exception text (the real message carried the SAS query string). **Alerting-after-N is now SHIPPED too (#837, PR #841):** after 3 consecutive failed polls (~30 min) DataQ pushes an alert through the existing Teams/Slack/email channels, and signals recovery. It fires on the **crossing** (`streak == threshold`), never on every failing poll — a dead credential fails forever, so a `>=` would have sent ~144 alerts/day until the channel was muted, which recreates #828's blindness in a mute rule. A connection has no suite, so the `RunReport`-shaped `ResultPublisher` seam does **not** fit: #841 adds a sibling **`HealthPublisher`** seam (`ConnectionHealthReport`, workspace destination) that the same composite implements — and the webhook SSRF guard + the SMTP STARTTLS transport are now each written once and shared by both paths. The alert reason is read straight off the classified `last_poll_error`; nothing on that path can re-derive it from an exception. **The agentic review caught two shipping bugs**: the streak was a **racy read-modify-write** (three schedules sweep the same connections — the 10-min poll beat, the 30-min gap recovery, the #492 poll-now — so two overlapping sweeps could both land on `== threshold` and **fire the alert twice**; now row-locked), and `state` was a free-form `str` where anything ≠ `"failing"` rendered as a **recovery** (a typo'd call site would have sent a confident all-clear for a dead connection; now a `Literal`, which immediately caught two loose call sites). Also fixed: Teams checked the webhook host but **not the scheme**, so an `http://` workspace webhook would have shipped alerts in cleartext. Deferred, filed: **#842** (the publish blocks the sweep — the bad case is correlated, e.g. a Key Vault outage crossing every connection at once) and **#843** (edges key off the counter, not off what was actually *delivered*). Pre-expiry credential warning (#838) remains open — deliberately scoped wider than this SAS (it spans PATs / Databricks / Snowflake key-pairs). (2) The 15-min `_POLL_LOOKBACK` backlog gap is documented in docs/orchestration.md (re-run the producer to recover). **Also fixed in #839: the Marquez/catalog lineage pull was NEVER going to work (#823)** — proven with real `openlineage-dbt` 1.51.0 on a real manifest: it emits `DB.ANALYTICS.mart_order_revenue` where DataQ's asset identity is `DB.ANALYTICS.MART_ORDER_REVENUE`. The NAMESPACE joins byte-for-byte; the NAME does not, and not as a simple case flip (mixed case per segment — db/schema from the dbt profile, table from the model filename), so **case variants can't fix it**. Every seed 404'd. **ADR 0034's "a join, not a mapping layer" premise is AMENDED**: reconciliation now happens at the `LineageProvider` seam (`lineage/identity.py`) — engine-correct fold (snowflake→upper, unitycatalog→lower, **NO fold** for abfss/s3/Iceberg, which are case-SENSITIVE), catalog enumeration via `list_datasets` (we can't construct the catalog's string), union-seeding of fold-equivalent names (exact-match-first seeded DataQ's OWN emitted twin and would have PRUNED the real lineage), and canonicalization on ingest. Live-verified: resolved 0→3, fetched_pairs 0→7. Tests ride a CAPTURED REAL payload — the bug survived a green suite because every fixture was hand-written by us.** The old lesson text: (1) a failing orchestration poll had NO user-visible signal, and the UI's empty-lineage state is indistinguishable from "this asset genuinely has no upstreams"; (2) `_POLL_LOOKBACK` is 15 minutes, so restoring the credential records NOTHING — every build produced during an outage longer than the window is stranded in the artifact store, recoverable only by re-running the producer.** Documented in docs/orchestration.md (§When lineage is empty) + docs/runbook-faq.md. Shipped alongside: **#829** (a suite was unshareable from a phone — the Add button painted off-screen at x=407 on a 390px viewport; PR #831) and **#830** (the assets UI printed the raw OL namespace — an Iceberg DSN — instead of a human label like `Snowflake · ACCT`; PR #832; the namespace stays the ADR-0034 identity and the label is presentation-only, and it is deliberately NOT the connection name, since several connections collapse to one namespace by design). Both deployed to prod. **The `comparison` monitor kind is BUILT (2026-07-12):** ADR [0015](docs/adr/0015-two-connection-comparison-check-model.md) (two-connection model — suite = target under test, check adds one source ref) written + merged (#790), then the full build #791–#795 shipped same-session as PRs #798/#807/#808/#809/#810 (schema+authoring → DatasetReader seam → FDC engine port → run path → side-by-side editor UI + derived CSV/XLSX report download); en route, #796 (the #783 rate limiter 429-ing the tokenless CI E2E lane) was fixed via #797. **W2 CLOSED COMPLETE 2026-07-08** (milestone closed; exit gate MET 14/14; all 5 in-week follow-ups cleared — #571 (#699) run-detail checks_total graft, #640 (#700) flaky LiveRunProgress test, #643 (#701) stale-policy event, #605 (#702) redaction-safe run failure_reason, and **#286 Iceberg spike closed via ADR 0030** — engine-level read (Snowflake/UC iceberg tables) is free/zero-code, native `pyiceberg` v2 read proven green end-to-end, self-contained `iceberg` connection (Option A); native build → #716 (W3), Iceberg-v3 revisit → #717). W2 exit gate MET (observability + secrets + alerting vendor-neutral & Azure-verified, dry-run all-datasource): OTel logs #524/#589 · `SecretStore.delete` #372 + least-priv KV role #622 · dbt as a third `OrchestrationProvider` #609/#611 (ADR 0029) · alerting-hardening batch #386–#389/#416 · **#488** workspace-admin visibility in MCP tools + schedules ([#695](https://github.com/TheurgicDuke771/DataQ/pull/695)) · **#584** MCP NL tool-selection spot-check passed vs live `/mcp` (VS Code Copilot + W1 PAT, all 4 canonical queries correct) · **#532** dry-run preview extended to all datasources ([#697](https://github.com/TheurgicDuke771/DataQ/pull/697)). **W1 closed COMPLETE 2026-07-05** (milestone closed; exit gate MET): #194/#195 encrypted key-pair + GX kwargs migration live-verified (#602/#603) · #587 scale baseline captured (#607, docs/perf-baseline.md — extended to all datasources + renamed 2026-07-10) · #588 retirement rehearsed→REVERSED (trial actually runs to ~2026-07-25 — user correction; re-homed W3, #608/#610) · **#461 PATs phase 1 SHIPPED + LIVE** (#613, ADR 0026 Accepted: `dq_live_` sha256-at-rest behind the `get_current_user` seam, REST + `/mcp` identically; live exit met post-deploy — admin PAT `dq_live_NNZ5…` 30d + member PAT `dq_live_uTSi…` 90d exercised vs prod REST + `/mcp/`, 10-vs-4-suite / `/admin` 200-vs-403 authz matrix; **PATs are now the standing headless credential — az-CLI-bearer interim #565 retired**) · #583 MCP `profile_column` run-target default (#614). W1 also redirected the dbt work (user decision): **#609 rescoped to self-hosted dbt Core** + **#611 filed — dbt as a third `OrchestrationProvider`** (webhook + artifact-poll, host-agnostic; dbt Cloud free tier has no API/scheduler, and Snowflake/Databricks hosting would couple observation to the vendor's run API), both W2. The **v1.1 cycle (6 weeks + a W7 stretch, 2026-07-04 → 2026-08-22) is planned** — week-level plan in [docs/progress.md](docs/progress.md) §Cycle plan; GitHub mirror = milestones `v1.1 Week 1..7` (W7 = stretch, due 2026-08-22) + cycle epic #597 + the **DataQ Roadmap** project (`v1.1 week` field; **65 issues scheduled** — #587–#596 filed at planning, then the full backlog remap 2026-07-04: `Backlog (post-v1 / testing)` renamed **`v1.1 Backlog`** and fully drained into W2–W7, leaving the backlog milestone as the default for new filings (it has since re-filled — incl. the G-d build set #757–#762, filed 2026-07-10); every scheduled issue carries an Acceptance-criteria checklist, every milestone its Exit gate). **Sequencing is subscription-driven:** the Snowflake subscription lapses within days (W1 front-loads the last-window live work — #194/#195/#587 — then retires the leg #588, with **PATs #461/ADR 0026 pulled forward right behind them** — breaks the Azure-AD-only auth dependency early in the Azure window; exit = 1 admin + 1 member PAT minted) and the Azure subscription ends ~2026-07-25 (W2 lands the portability seams verified against live Azure — OTel logs #524/#589, `SecretStore.delete` #372, dry-run depth #532 — and W3 ends in the deliberate wind-down #590 + local-first posture #591); W4–6 then run the roadmap's recommended sequence cloud-independently — `schema_drift` #592 → `anomaly` #593 + trend view #594 → scale-aware execution #595 (G-b) → retro + `v1.1.0` tag (the G-d incident/lineage design doc #596 was pulled forward and **done in W3, 2026-07-10** — ADR 0034 + docs/post-v1-assets-lineage-incidents-notes.md, phase-1 build set #757–#762 filed to `v1.1 Backlog`). (Prior-week detail: Week 7 closed **COMPLETE 41/41** on 2026-07-03 (milestone + epic #176 closed; W7 exit gate — production-ready v1 deployed to Azure, CI/CD live, team onboarded — **met**). **Cloud deploy is DONE** (ADR 0024/0025 — app live on ACA; frontend cut over **SWA → Container App** per ADR 0028 §5, #509–#511). **Post-deploy hardening DONE** — Celery beat fix (#407, closes #405) + Key Vault credential fix (#408, closes #406) + image `:v7` redeploy (#409) — orchestration polling (ADF + Airflow), scheduled-suite dispatch, gap recovery, and periodic purge are all live. **Post-deploy feature batch DONE** (prod image now `:v10`, #438) — alerting publishers (Slack+email #413), redaction depth (#417), per-run outcome (#425), and **freshness/volume monitor-kinds end-to-end** (#426/#437, ADR 0012 Theme A pulled forward). **DONE this session:** the **FastMCP server (8 tools at `/mcp`)** (#460, ADR 0008), the **hardening/docs pass** (prod-docs gate #464, Swagger completeness + error-shape audit #465, deployment guide + env-var reference #468), **consistency hardening** (#456/#458, closes #308/#309), and the **visual-fidelity pass** (#459). **W7 close-out batch DONE (2026-07-01/02):** OTel spans #525 (+#524 follow-up) · vault-test #523 · #17 polish #522 · E2E expansion #526/#527 · live-smoke lane #531 · docs enrichment #528 (+#532 filed) · MCP-expansion candidates #530/#529 — **no in-repo Week-7 work remains**. **Live smoke + #492 DONE (2026-07-02):** Flows A/B/C green, spans verified, MCP protocol smoke passed, #492 fired→ingested in 4m14s (#534); fix batch #537/#538/#542 deployed (issues #535/#536/#539/#540). **Externals closed 2026-07-02:** team onboarding (six demo Entra users cross-shared at every ADR-0027 tier — no separate session, solo-dev) + KV purge-protection **decided left off** (`deploy/README.md`). **Final close 2026-07-03:** MCP client-config E2E passed (#550 — all 8 tools end-to-end via a real VS Code `.vscode/mcp.json` against live `/mcp/`; client setup guide at `docs/mcp-setup.md`). **All post-v1 / deferred work is consolidated in [context/post-v1-roadmap.md](context/post-v1-roadmap.md)** (incl. ADR 0026 — DataQ-issued API keys / service tokens, #461).)
**Active blockers:** none — v1 is shipped. Post-v1 follow-ups open by choice: spike survivors [#563](https://github.com/TheurgicDuke771/DataQ/issues/563), threshold-ordering [#568](https://github.com/TheurgicDuke771/DataQ/issues/568), checks_total edge [#571](https://github.com/TheurgicDuke771/DataQ/issues/571), CI flake [#573](https://github.com/TheurgicDuke771/DataQ/issues/573). The qa-verifier go-live workout (2026-07-03) also found and closed the one v1.0.0 blocker — NUL-byte input → unhandled 500 — same-day via [#570](https://github.com/TheurgicDuke771/DataQ/pull/570) (closed #567 + the older #371); non-blocking footguns from the same pass are filed as Backlog issues [#568](https://github.com/TheurgicDuke771/DataQ/issues/568)/[#571](https://github.com/TheurgicDuke771/DataQ/issues/571)/[#573](https://github.com/TheurgicDuke771/DataQ/issues/573). Open follow-ups (full register in [docs/progress.md](docs/progress.md); post-v1 backlog in [context/post-v1-roadmap.md](context/post-v1-roadmap.md)): profiler N+1 batching [#327](https://github.com/TheurgicDuke771/DataQ/issues/327); `SecretStore.delete` secret cleanup [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) (filed in W6); Week-6 follow-ups [#349](https://github.com/TheurgicDuke771/DataQ/issues/349)/[#351](https://github.com/TheurgicDuke771/DataQ/issues/351) (#348 closed); Week-4 nits [#197](https://github.com/TheurgicDuke771/DataQ/issues/197)/[#199](https://github.com/TheurgicDuke771/DataQ/issues/199)/[#204](https://github.com/TheurgicDuke771/DataQ/issues/204); backend [#194](https://github.com/TheurgicDuke771/DataQ/issues/194)/[#195](https://github.com/TheurgicDuke771/DataQ/issues/195). See [docs/progress.md](docs/progress.md).

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
| Auth | Generic OIDC (`oidc-client-ts`, Azure AD validated) + backend `fastapi-azure-auth` |
| Secrets | Azure Key Vault |
| Hosting | Azure Container Apps (API + worker + frontend; frontend = sole public surface, api internal — ADR 0028 §5) |
| Observability | Azure Application Insights + structlog |
| CI/CD | GitHub Actions |
| API docs | FastAPI Swagger + ReDoc |
| MCP | FastMCP (PrefectHQ) — 8 curated tools at `/mcp` |
| Python tooling | conda + Black + Ruff + mypy + pytest + Bandit |
| Frontend tooling | Prettier + ESLint + Vitest + React Testing Library |
| Secret scanning | betterleaks (pre-commit + CI) |
| SAST | Bandit + CodeQL |

### Client-side MCP servers (`.mcp.json`)

Distinct from DataQ's **own** FastMCP server at `/mcp` (ADR 0008 — the 8 tools DataQ *serves* to AI clients), the repo-root **`.mcp.json`** configures MCP servers that AI assistants working in this repo *consume*:

| Server | Package (pinned major) | Publisher | Purpose |
|---|---|---|---|
| `context7` | `@upstash/context7-mcp@1` (npx) | Upstash | Up-to-date library docs lookup while coding |

- **Trust prompt:** Claude Code prompts once per machine before starting servers from a project `.mcp.json`; approve only if the list above matches what's in the file.
- **Pin majors, not `latest`** — same rationale as the GX pin.
- **Supply-chain cadence:** quarterly audit per CONTRIBUTING.md rule 39 (deprecated/yanked/publisher-transfer check before any bump).
