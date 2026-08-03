# CLAUDE.md — DataQ project guide for AI assistants

> Single source of truth for any Claude / AI assistant working in this repo. Read this end-to-end before touching code.

---

## 1. Project summary

**DataQ** is a single-tenant data quality monitoring platform built around Great Expectations (GX Core). It runs DQ checks across **5 datasources** and integrates with **3 orchestration providers**.

| Layer | Components |
|---|---|
| **Datasources (you can write checks against)** | Snowflake (DEV/QA/UAT), ADLS Gen2, AWS S3 **and any S3-compatible store** (MinIO/Ceph/R2/Wasabi/Backblaze, via the optional `endpoint_url` — #1063), Unity Catalog (Databricks), Apache Iceberg (native `pyiceberg` read — ADR 0030) |
| **Orchestration providers (monitor + trigger only — NOT datasources)** | Azure Data Factory (ADF), Apache Airflow, dbt (ADR 0029) |
| **Backend** | FastAPI + Celery + Redis + PostgreSQL + Alembic |
| **Frontend** | React + Vite + Ant Design + Monaco editor (generic OIDC — `oidc-client-ts`) |
| **Auth / secrets** | Three-mode ladder (ADR 0032): dev-bypass (eval) · **email OTP** (IdP-less, default for the local stack — #1150) · OIDC (Azure AD validated; provider-neutral `AUTH_*` contract) — plus PATs (`dq_live_`) for API/MCP clients. Secrets: Azure Key Vault / OpenBao (ADR 0039) |
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
│   │   ├── datasources/         # ConnectionAdapter + CheckRunner per type; gx_runner.py (shared GX translation), flatfile.py (flat-file IO + runner + batch resolution), sql.py (shared SQL-identifier allowlist — #428)
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
- AWS S3 — and any S3-compatible store, via the connection's optional `endpoint_url` (#1063)
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
- **v1.1+:** platform-native engines are **connection-anchored** (ADR [0036](docs/adr/0036-connection-anchored-check-engines.md)): every datasource connection offers GX; a Snowflake connection additionally offers **DMF**, Unity Catalog offers **DQX** (DLT/streaming — GX is batch-only), a future BigQuery type offers **Dataplex**. Engine selection is **per check** (`check.engine`, default `gx`, validated against the connection's capability set; `kind` stays orthogonal to `engine`) — supersedes the earlier suite-level `engine: gx | dqx` toggle sketch. DMF is the first native build; DQX/Dataplex are trigger-gated (see the ADR §6).
- **Monitor-kind seam (do-now, Week 3):** not every monitor is a GX expectation. A `check.kind` discriminator (`expectation` in v1; `freshness | volume | schema_drift | anomaly | comparison` reserved — freshness/volume shipped post-v1 (#426/#437) and **`comparison` shipped v1.1 W3** (ADR 0015, #791–#795) and **`schema_drift` shipped v1.1 W4** (#592) and **`anomaly` shipped v1.1 W5** (#593, rolling z-score baseline with optional seasonality, PRs #1117/#1119)) + numeric `metric_value` on results let v1.x auto-monitors slot in without a check/result schema rewrite. This seam is **orthogonal to the datasource seams** (`CheckRunner`, `ConnectionAdapter`): it varies by *monitor kind*, not datasource. See ADR `0012` (and `0014` for the reserved `comparison` / cross-dataset reconciliation kind) and post-v1 roadmap Theme A. Most real incidents are freshness/volume, not value-level — this is the leap from "GX runner" to DQ platform.
- **DQ-dimension axis (v1.1 W4, ADR [0038](docs/adr/0038-dq-dimension-classification.md)):** every check also carries a `dimension` — accuracy / completeness / consistency / integrity / timeliness / uniqueness / validity — a **third axis orthogonal to both `kind` (how the monitor works) and `engine` (what evaluates it)**. Closed vocabulary via table `CHECK`; derived by default from the check type, **stored** (so #889 aggregates in SQL and an override survives), and overridable at any time. Derivation is **deliberately partial** — `accuracy`/`integrity` are never derivable from a rule shape and custom SQL is an arbitrary predicate — so **NULL means unclassified and must render as a coverage gap**, never silently bucketed. Existing rows were backfilled (#952, §5 amended). It is the input to the asset DQ scorecard (#889), whose value is *coverage* ("this asset has no Timeliness checks") more than score.
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

> **Note:** `scripts/setup.sh` prompts for your sign-in email on first run (the local stack defaults to **email OTP** with a bundled Mailpit inbox at `http://localhost:8025` — #1150; answer blank for the explicit dev-bypass downgrade) and generates local credentials into the gitignored `.env`/`.env.app`.

```bash
git clone <repo>
cd DataQ
./scripts/setup.sh           # creates conda env, installs pre-commit, pulls images, runs migrations, seeds dev data
conda activate dataq
docker-compose up            # Postgres + Redis + FastAPI + React + Celery worker
# Smoke test:
curl -X POST http://localhost:8000/api/v1/_probe/snowflake-suite
# …then read the run through the REAL API (the probe's own reader was removed in
# #1039 — it had no suite-ownership check):
curl http://localhost:8000/api/v1/runs/<run_id>
# Browse Swagger: http://localhost:8000/docs
```

---

## 9. Key design decisions (ADR index)

The full decision index — one line per ADR with status, 0001–0040 to date — lives at **[docs/adr/README.md](docs/adr/README.md)** and is the **single source of truth** (this section used to duplicate it as a table and the two drifted; it no longer does). Read the index before coding and open the individual ADR whenever a decision bears on your change. The day-to-day operating rules those decisions distill into are already captured in §4–§6, §10 and §11 of this file.

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
- ❌ **Don't dismiss a finding because it's pre-existing.** DataQ is in production — a defect that predates your change is live for users right now, so its age is irrelevant to whether it matters. Every finding ends **fixed or filed**, never "noted in a review reply" or "documented in a docstring" (CONTRIBUTING rule 3a). Concluding it is genuinely not a defect is fine, but record the determination and evidence — verified-benign is a valid outcome, unexamined is not.
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
| **Ops log — harness lifecycle + credential rotation** | [docs/ops-log.md](docs/ops-log.md) — append-only, git-tracked. **Every harness start/stop and every credential rotation gets an entry**, with absolute UTC timestamps. Two incidents forced this: a stopped Airflow that took several Azure queries and two wrong root causes to distinguish from an outage (the answer lived only in `systemData.lastModifiedAt`), and a partial rotation that left two Snowflake connections dead for three weeks because one credential becomes N per-connection Key Vault secrets. Identifiers and dates only — **never a secret value**. A `PostToolUse:Bash` hook in `.claude/settings.json` fires on `az containerapp start/stop`, `--min-replicas`, `harness_window.sh`, `az keyvault secret set` and `/reauth`, so the log cannot quietly rot. |
| **Harness ad-hoc test window script** | `~/Coding/Python/DataQ-harness/scripts/harness_window.sh` (harness-side, **not git-tracked** — ADR 0021). The harness compute is **stopped by default** since 2026-07-04 (Azure cost wind-down, #590 — ~CAD 17/day awake vs ~0 stopped); this script opens a test window: `window [--adf] [--dags] [--dbt]` = wake (redis→Airflow→workers→trigger + ADF triggers) → run the flows (mockdata jobs as manual executions; `--dags` REST-triggers the cron DAGs; `--adf` create-runs the Flow-A ADF pipelines; `--dbt` resume+starts the `dbt-lineage` ACA job (#609 — dbt Core builds the `ANALYTICS_STG` views + `ANALYTICS` mart dynamic tables, artifacts → ADLS `raw/dbt/latest` for the #611 poller) and re-suspends it — the `--adf` pipelines, the `flow_a_snowflake_load` DAG **and `--dbt`** need live Snowflake, the UC/medallion DAGs don't) → sleep again, verified. `status`/`start`/`run`/`stop` also run standalone; `status` and `stop` cover the `dbt-lineage` job (its nightly `0 2 * * *` cron is disarmed by `stop` like the mockdata crons). Full-cycle validated 2026-07-04 (11.5 min, all flows green; `--dbt` leg added 2026-07-05, not yet window-validated). |

---

## 13. Status & current milestone

> **Detailed task-level status** lives in [docs/progress.md](docs/progress.md) — the live post-v1 tracker, updated per PR (the completed v1 ledger, which mirrored the 100-task roadmap, is archived frozen at [docs/progress-v1.md](docs/progress-v1.md)). This section carries only the headline.

**Current week:** **v1 DONE — `v1.0.0` tagged 2026-07-04 (Week 8 closed 29/29 + exit gate MET; epic #177 + the W8 milestone closed; retro at [docs/retro-v1.md](docs/retro-v1.md); **v1.1 cycle planned 2026-07-04** — see [docs/progress.md](docs/progress.md) §Cycle plan).** The gates are live in CI: backend `--cov-fail-under=80` in `pyproject.toml` (98.3% / 1273 tests on main, after closing the four sub-80% modules — #557) + frontend `lines: 80` over ALL of `src/` via `pnpm test:coverage` (87.8% / 334 tests — #558). The W8 batch #556–#560 also closed #385 (CORS activation tests, #556), #205 (catalog↔GX contract test, #559), #352 (dashboard Avg-Duration + real deltas, #560), and #128 (full-stack E2E — the gates were its last half). **Go-live close (2026-07-03/04, all cleared):** pre-tag QA — qa-verifier workout NO-GO on the NUL-byte 500 (#567) → fixed #570 (also closed #371) → re-run GO (21 injection points 422, zero 500s); prod redeployed `2fa05333` + re-probed live; live prod workout as non-admin Olivia 15/15 + webhook-auth hostility 7/7 401s (#569 closed, both halves); **ops/renewals timers consciously SKIPPED** (demo-scoped credentials; expiry self-signals via #419 alerting; recovery = re-mint + KV update; G-i teardown covers the end state). Checklist progress 2026-07-03: #553 closed (#562, bare pip-audit green) · mutation spike done (mutmut `dashboard_service` 436/436 killed; Stryker 82.35%; survivors → #563, config retarget #564) · prod deploy + smoke re-green done (`8dee4f4a` images; Flows A/B/C `succeeded` as dataq-admin — flat-file suite recreated post-#540; Azure CLI pre-authorized on the API scope for non-interactive bearers, #565 + TF import) · decisions recorded: **ADR 0026 deferred post-v1** (PATs-first shape confirmed; Basic auth rejected — see the ADR's decision record) + **Databricks Free-Edition** (demo/eval OK, paid workspace before commercial use — gap G-h) + **pre-marketplace harness teardown** (gap G-i: strip Flows A/B/C + harness connections + demo users before any marketplace/customer-facing artifact; also deploy/README.md). **Week 7 — Deployment, hardening & docs — COMPLETE (41/41, closed 2026-07-03; milestone + epic #176 closed); DataQ v1 is DEPLOYED TO AZURE and reachable** (Weeks 1–7 complete, all exit gates met). **Cloud deploy (2026-06-28):** the in-repo Terraform (`deploy/terraform/azure/`, ADR 0024) stood up the app stack into `dataq-rg` — `dataq-app-{api,worker}` + `dataq-app-migrate` job (GHCR slim image, ADR 0025) on the **shared `dataq-cae`** Container Apps env, `dataq-app-redis` (password-auth), Key Vault (UAMI) + App Insights + Log Analytics, and **`dataq-app-web`** Static Web App with the api **linked as same-origin `/api` backend**. The app's DB is a distinct **`dataq`** database + least-priv **`dataq_app`** role on the **shared `dataq-pg-wus3-*`** server (1-of-each free/trial cap → env + Postgres shared with the harness, neutral-named `purpose=dataq-shared`; harness Postgres backed-up→recreated→restored). Azure AD **SSO app registrations** (API + SPA) created in TF + wired; migrate job ran `alembic upgrade head`; API healthy (401 = auth-enforced), SPA + deep-links 200, GitHub OIDC secrets/vars + `production` env set. Fixed **#393** (opencensus AzureLogHandler `lock=None` on Py3.13) en route. **GHCR package→repo connect done** (Actions-access grant → CI's `GITHUB_TOKEN` can push) and the **Deploy workflow validated end-to-end** (#403 fixed the migrate-command + frontend-pnpm bugs #401/#402; build→push→`alembic upgrade head`→ACA roll + SWA deploy all green on `v6`). **Post-deploy hardening (2026-06-28):** two production bugs surfaced and fixed — **#405** (Celery beat crashed on startup: the embedded `worker -B` beat re-nulled `self.lock` inside the opencensus `AzureLogHandler.createLock` fork on Python 3.13, silently killing ALL periodic tasks — orchestration polling, scheduled-suite dispatch, gap recovery, and sample-failure purge; fixed by making `createLock` idempotent in `backend/app/core/logging.py` + a network-free regression test; **#407** merged) and **#406** (deployed app couldn't read Key Vault: `AzureKeyVaultStore` called `DefaultAzureCredential()` with no args but the api+worker container runs a USER-assigned managed identity and `AZURE_CLIENT_ID` was unset — blocked connection tests, suite runs, AND orchestration polling; fixed by adding `AZURE_CLIENT_ID = azurerm_user_assigned_identity.app.client_id` to `local.app_env` in `deploy/terraform/azure/containerapps.tf`; **#408** merged). Backend image `:v7` built+pushed from main (with #405+#406); api+worker rolled to v7; App Insights re-enabled on the worker (the #405 mitigation of temporarily dropping `APPLICATIONINSIGHTS_CONNECTION_STRING` on the worker is reverted) — landed as **#409** (`image_tag` default v4→v7). **Orchestration polling is now live end-to-end** — beat starts clean (zero NoneType crashes) with App Insights on, api healthy (401, auth-enforced), ADF+Airflow connections polling via the 10-min beat fallback, Key Vault secrets read successfully. **Post-deploy feature batch (2026-06-29, all merged; prod image `:v7`→`:v10`):** Slack + email alert publishers behind the `ResultPublisher` seam (#413, `:v8`), column-aware failing-sample redaction (#417) + the #383/#384/#395/#423 hardening batch (`:v9`, bump #414), URL-encode DB password (#421/#395), always-alert operationally-failed runs (#419), alerting upsert race fix (#420/#384), per-run check outcome in the runs table (#425/#423), mypy gate over `backend/tests` (#418), and — **pulling ADR 0012 post-v1 Theme A forward — freshness & volume monitor-kinds end-to-end** (run engine #426 + authoring path & check-editor UI #437, `:v10`, bump #438). Three post-v1 design docs landed (#422/#430/#436) and are consolidated in **[context/post-v1-roadmap.md](context/post-v1-roadmap.md)** (the single post-v1 home + week-wise-task-generator input). **W7 in-repo work now DONE:** the **FastMCP 8-tool server** at `/mcp` (Azure-AD `JWTVerifier`-validated, fail-closed; ADR 0008, #460); the **hardening/docs pass** — prod-docs gate (#464), Swagger completeness + error-shape audit (#465), deployment guide + complete env-var reference (#468); **consistency hardening** — trigger-dedup index (#456, closes #308) + stuck-run reaper (#458, closes #309); the **visual-fidelity pass** (#459); and the W1–6 deferred + not-started triages (#463/#467, closing #169/#170). **W7 close-out batch (2026-07-01/02, all merged):** OTel **request/task spans** to App Insights (#525 — vendor-neutral core `backend/app/core/tracing.py` + Azure exporter-only, module-scope FastAPI + producer/consumer Celery instrumentation, secret/PII-safe span attributes, `dataq.request_id` span↔log join; opencensus→OTel log migration → #524) · **vault lazy-import test coverage** (#523, `secrets.py` 100%) · **#17 MCP polish** (#522 — this file's `.mcp.json` appendix + CONTRIBUTING rule 39 (numbered 38 pre-#547)) · **Playwright E2E expansion** (#526 schedules/triggers/notifications panels + #527 run-detail sample/dashboard/check-editor variants/admin — 25 specs green in CI) · **opt-in live-smoke lane** (#531 — `frontend/e2e-live/` gated on `E2E_LIVE_BASE_URL` with captured-OIDC session + `e2e_smoke.py` `DATAQ_BEARER` mode + runbook checklist; never in CI) · **user-docs enrichment** (#528 — notifications/scheduling/best-practices/feature-matrix pages; #532 filed) · **MCP tool-expansion candidates** (#530 — post-v1 Theme 13 + issue #529). **LIVE SMOKE RUN + #492 DONE (2026-07-02, via the #531 lane):** browser lane 3/3 · `e2e_smoke.py` bearer-mode vs prod · **Flows A/B/C verified green** (Snowflake ×3 / UC / flat-file) · **#525 spans confirmed in App Insights** · **MCP 4-query protocol smoke passed** vs live `/mcp` · **#492 closed** — Action Group + `PipelineFailedRuns` metric alert on the harness factory; a deliberate `pl_dataq_smoke_fail` failure was visible in DataQ **4m14s** after fire (Common-Alert-Schema → `AlertPing` → immediate-poll ingest, #534). Smoke fallout fixed same-day and deployed: UC dialect regression (#535→#537), traceback-locals credential leak (#536→#538 — **Databricks PAT rotation required**), suite-delete FK cascade (#540→#542); deploy-workflow frontend flake filed (#539). **W7 externals closed (2026-07-02):** team onboarding discharged (six demo Entra users cross-shared at every ADR-0027 tier on the deployed app — no separate session, solo-dev) and KV purge-protection **decided: left off** (demo-scoped vault, destroy/re-apply flexibility; recorded in `deploy/README.md`) — and the last row closed 2026-07-03: **MCP client-config E2E passed** (#550 — a real VS Code `.vscode/mcp.json` against live `/mcp/` exercised all 8 tools end-to-end; client setup guide moved to `docs/mcp-setup.md`, README keeps a lean pointer + the trailing-slash `/mcp/` guidance) — **Week 7 is COMPLETE (41/41)**. Week-6 close: the **alerting backend track** — `ResultPublisher` seam (#366), Teams adaptive card + publisher (#367), severity-aware routing (#368), dedup (#369), suppression/snooze (#370), per-suite notification config (#373) — plus the **prototype Phase 5–6 screens**: Profile content (#374), Workspace Settings (#375), Admin layout-reconcile (#376), standard 4xx/5xx error pages (#377), and the per-suite **notification config UI** (#378). Earlier Week-6: Results scaffold (PR-C1), Enhanced Monitoring Dashboard + run-detail route (#333), results filter bar + orchestration poll/correlation (#347), drawer→page restructures (#350), layout/prototype-fidelity polish (#353), and redacted sample failing rows (#365, closed #226). **Week-7 early-credit:** Azure deploy scaffolding (#379 — frontend Dockerfile + ACA/SWA manifests + parameterized deploy workflow + CORS middleware + prod env reference; manual-trigger only, the actual apply stays blocked on Azure RP registration per ADR 0021). _All Week-6 feature work **merged to `main`** as the stacked PR chain #366→#379; follow-ups #380 (W6 close-out docs), #381 (deploy migration-gate + doc reconcile — a Week-7 CI/CD task landed early), and #390 (live run-progress per-status histogram fix, closes #316) merged after._ **Cloud-neutral cutover (2026-07-01, ADR 0028 §5 — DONE):** the deployed frontend moved **Static Web App → a Container App** (`dataq-app-frontend`), now the **sole public surface**, running the one generic nginx image with runtime `DATAQ_AUTH_*` OIDC config (**MSAL retired** for a generic oidc-client-ts; validated live as Olivia → dashboard). The **api moved to internal ingress** (reached only via the frontend nginx proxy `/api` + `/mcp` + `/healthz`); the SWA (`dataq-app-web`) is **destroyed**. Landed as **#509** (cutover) → **#510** (lifecycle guards — `ignore_changes` on container images so applies never roll prod back to `var.image_tag`, + on the api `identifier_uris` so applies never strip the token audience) → **#511** (three ACA gotchas: nginx must proxy **HTTP/1.1** or ACA ingress 426s; api ingress **HTTP + `allow_insecure_connections`**; **orphaned SWA-EasyAuth** on the api disabled via `az containerapp auth update --enabled false` — it 401'd every request post-SWA-destroy; DataQ does its own `fastapi-azure-auth` validation). Prod frontend image `:v2`; the live URL is deployment-specific and deliberately not tracked here (#730) — read it with `az containerapp show -n dataq-app-frontend -g dataq-rg --query properties.configuration.ingress.fqdn -o tsv`. Follow-up **#512** (multi-arch frontend QEMU build was impractically slow/flaky — 45-min stall → cancel) **fixed in #518**: pin the frontend Dockerfile `build` stage to `--platform=$BUILDPLATFORM` so the arch-neutral Vite build runs natively once and only the per-arch nginx `serve` stage differs — frontend publish dropped from a 45-min stall to **~1min**, and a native-arm64 `:latest` multi-arch is published reliably again. AWS/GCP deploy IaC stays post-v1 (#505).
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
**Next milestone:** **v1.1 Week 6 — scale-aware execution + hardening + cycle close (due 2026-08-15).** **Unplanned, user-directed, shipped 2026-08-01/03 — the ADR-0032 email OTP sign-in track, picked up from the `v1.1 Backlog` beside W6 (umbrella [#738](https://github.com/TheurgicDuke771/DataQ/issues/738), now closed):** the third authenticator (`bypass · otp · oidc`, `dq_sess_` cookie sessions, mandatory signup allowlist, fail-closed) landed as **11 PRs** across all seven design slices + all five review findings, **COMPLETE 2026-08-02** — pre-implementation recon found the stated hard prerequisite (#725 rate limiting) hadn't actually covered the auth-endpoint class, so an `auth` limiter class + per-email counters shipped first ([#1129](https://github.com/TheurgicDuke771/DataQ/pull/1129)); then the nullable-`aad_object_id` + unique-lower-email identity migration ([#1131](https://github.com/TheurgicDuke771/DataQ/pull/1131)); OTP backend core — `sessions`/`otp_codes`, mailer, request/verify/logout ([#1134](https://github.com/TheurgicDuke771/DataQ/pull/1134)); the frontend two-step sign-in + HttpOnly cookie + server-side revocation, plus a new fully-local `frontend/e2e-otp/` lane ([#1148](https://github.com/TheurgicDuke771/DataQ/pull/1148)); the SMTP pre-flight test endpoint ([#1143](https://github.com/TheurgicDuke771/DataQ/pull/1143)); and `/mcp` in otp-only mode, which review found was silently unmounted — fixed with one `mcp_auth_mode()` ladder mirroring the REST gate exactly, so a cookie session still can't reach `/mcp` and only a PAT (`dq_live_…`) can ([#1151](https://github.com/TheurgicDuke771/DataQ/pull/1151)). **Follow-up wave, CLOSED 2026-08-02/03, +4 PRs:** first-login profile completion for OTP-provisioned users — `PATCH /me` `display_name` + a skippable prompt, closing [#1139](https://github.com/TheurgicDuke771/DataQ/issues/1139) ([#1153](https://github.com/TheurgicDuke771/DataQ/pull/1153)); a latency floor on `otp/verify` closing the narrower enumeration channel [#1141](https://github.com/TheurgicDuke771/DataQ/issues/1141) left open after the request-side floor ([#1154](https://github.com/TheurgicDuke771/DataQ/pull/1154)); SMTP TLS mode + private-CA bundle for the mailer, closing [#1146](https://github.com/TheurgicDuke771/DataQ/issues/1146) ([#1155](https://github.com/TheurgicDuke771/DataQ/pull/1155)); and a per-admin throttle on the SMTP pre-flight endpoint, closing [#1147](https://github.com/TheurgicDuke771/DataQ/issues/1147) ([#1156](https://github.com/TheurgicDuke771/DataQ/pull/1156)). **Then, unplanned again — both local/eval compose stacks flipped to OTP-by-default** ([#1150](https://github.com/TheurgicDuke771/DataQ/issues/1150), [#1159](https://github.com/TheurgicDuke771/DataQ/pull/1159)): a bundled **Mailpit** mail catcher (MIT, pinned `v1.30.6`) at `localhost:8025` removes the SMTP relay that made `otp` unusable as a default, so both stacks now boot into email codes instead of dev-bypass, gated by one switch — `DATAQ_SIGNIN_EMAIL` set = OTP on, explicitly empty = the dev-bypass downgrade, unset = the stack refuses to start rather than picking for you. `scripts/setup.sh` asks for the address; see [Getting started](docs/getting-started.md) for the full flow. A same-day test-flake fix ([#1161](https://github.com/TheurgicDuke771/DataQ/pull/1161)) closed out the track. Full slice-by-slice detail — including the five review-caught defects (an `smtplib.__exit__` that replaces in-flight errors, a compose `environment:`-beats-`env_file:` precedence trap, an ACCESS-EXCLUSIVE-lock doc error, and two more) — is in [docs/progress.md](docs/progress.md) under "Backlog track — ADR 0032 email OTP sign-in". **v1.1 Week 5 — `anomaly` monitor kind + metric trends — CLOSED 2026-08-01, 8/8, a week early:** one orchestrated batch, 10 PRs [#1113](https://github.com/TheurgicDuke771/DataQ/pull/1113)–[#1122](https://github.com/TheurgicDuke771/DataQ/pull/1122) — threshold-ordering validation (#568, #1113, closing a `schema_drift` dry-run bypass the review found); the results runs-fetch dedupe + shared date-window presets (#349, #1114, fixing a background-poll-wipes-the-tab bug along the way); an explicit failing-sample redaction state (#424, #1115 — full/partial/none/null, computed read-time via a `_RedactionTracker` so old rows get the correct label for free); Test Connection for unsaved connection drafts (#351, #1116 — `POST /connections/test`, structurally cannot persist; review also surfaced the `*_secret_name` SSRF/exfiltration class on the same auth gate, filed [#1118](https://github.com/TheurgicDuke771/DataQ/issues/1118), mitigation deferred to RBAC #740–#744); the **`anomaly` monitor kind itself** (#593, #1117 backend + #1119 authoring UI) — rolling z-score baseline with optional seasonality over `metric_value` history, reusing the existing `monitor_baselines` table (no migration), `skip` on cold start, dimension deliberately underivable → NULL (ADR 0038); the per-check metric trend view (#594, #1121) with threshold bands plus a second anomaly-baseline panel, seasonality-aware; check-version restore (#283, #1120), minting a new version rather than delegating to the PATCH-semantics `update_check`; and a zero-dependency PDF report export of a run (#345, #1122). **Live-verified 2026-08-01 against both live warehouses** (the #953 rule — for anything crossing a driver boundary, only a live run is evidence): Snowflake `RETAIL.ORDERS_HEADER` row_count=34680.0 + freshness `ORDER_TS`=132.28h; Unity Catalog `dataq_retail.gold.feedback_sentiment` row_count=180.0 + freshness `scored_at`=132.48h — z-scores computed cleanly over both. Every PR carried the mandated `/code-review` agent flow; 15+ real defects were caught pre-merge, including two concurrency bugs (a baseline-UPDATE lost-update race, a stale "Connected" badge) and two honesty-of-display bugs (an under-reported redaction state, a threshold-band caption overclaiming below 2 eligible points); the one deferred finding is #1118 above. **Unplanned, shipped + live-verified 2026-07-28/29 — three batches in one orchestrated session (#1096–#1112, 10 PRs):** Batch 2 reliability (beat **crontabs** #1099 — a 24h interval never fires under embedded beat; **staleness signals** #1100 — stale-lineage surface + de-configured-pull purge + the API-side workspace dead-poll alert on the new `workspace_health` table) and Batches 3+4 assets/lineage (**ADR [0040](docs/adr/0040-warehouse-inventory-sync-table-enumeration-seam.md)** — the table-enumeration seam + opt-in per-connection **inventory sync** #1103, so a table with no suite/run/edge is visible-unmonitored instead of invisible (the prod `reference`-schema report); asset **paging + honest truncation** #1105; S3-compatible **endpoint identity** #1106; **GET_LINEAGE per-seed traversal + Snowpark scratch stitching** #1110 — prod's Snowflake lineage lifted from view-level to full tier, live-verified). The recurring lesson bit twice more (#1112): mocked tests stayed green through **two stacked driver-boundary crashes** in one SQL literal (pyformat `%`, then Snowflake's own string parser eating `ESCAPE '\'`) — for warehouse SQL, run the exact statement live before push. **Unplanned, shipped 2026-07-27 — IaC CLI moved Terraform → OpenTofu (ADR 0024 amendment):** Terraform has been BUSL-1.1 since v1.6 and was the one source-available binary still touching a project ADR 0031 puts on record as MIT-distributed. **Nothing was in violation** — rule 40 governs the dependency tree (what DataQ *ships*) and the CLI never enters an image — so this is coherence, not compliance; the timing argument is #505, since converting one Azure stack is contained and converting three is not. **The acceptance bar was equivalence, not a clean plan**, because the stack already carries pre-existing drift (#1086 — prod's ADR-0034 lineage env vars are absent from `containerapps.tf`, so an apply would DELETE them; the rendered plan hides this behind a positional `env` shuffle and only `show -json` reveals it): both CLIs were run against live Azure, plans exported with `-out`, rendered via `show -json`, and the normalized `resource_changes` diffed — **byte-for-byte identical** (40 changes, `no-op=38 update=2`), provider versions preserved exactly, only the registry host + hashes moved. The *rendered* text plans DO differ (branding, refresh ordering, column alignment, hidden-attribute counts), which is why eyeballing them would not have settled it. State, providers, and the `deploy/terraform/<cloud>/` path are unchanged — `terraform {}` is OpenTofu's own block name. Harness stack converted in the same pass, **plan-only** (a blanket harness apply arms ADF triggers). The swap is **convention, not enforcement** — nothing is OpenTofu-exclusive yet, which is what keeps it reversible; the `encryption {}` follow-up is the one-way door and is deliberately separate. **Unplanned, shipped 2026-07-27 — S3-compatible endpoints (#1063/#1065):** optional `endpoint_url` + `addressing_style` on the `s3` datasource **and** the `dbt` provider, via one shared `core/s3_endpoint.py`; unlocks MinIO / Ceph / R2 / Wasabi / Backblaze. `auto` → **path** addressing when an endpoint is set is load-bearing (MinIO serves the bucket in the path only, so boto3's virtual-host default fails every read), and `endpoint_url` rejects an embedded credential per the #754/#826 rule. **Live-proven against MinIO** — suite 4/4 over 643 real rows, arrival-time freshness read off `LastModified` — because a non-AWS server is a different implementation and those values cross a driver boundary. It arose from an **Azure-free harness readiness exercise** (local Airflow + MinIO landing zone, all flows green incl. a live Snowflake load; Azure untouched, all five harness apps stayed `Stopped`), whose Snowflake half also produced a standing rule: **nothing in the harness may require a commercial licence** — stricter than rule 40, which governs only what DataQ *ships* — after LocalStack for Snowflake was evaluated and rejected (no community tier; image exits 55). fakesnow (Apache-2.0) is the stand-in, and probing it with DataQ's real code paths found **#1067**: a Snowflake connection with no Role tests green (the test is GX-free) and fails every suite run, since GX requires a `role` query param. See [docs/ops-log.md](docs/ops-log.md). Follow-ups #1064/#1066. **The four open ADR-0039 follow-ups were then closed the same day (#1069/#1070/#1071):** GHCR `:latest` now republishes on merge (the eval compose is fetched from `main` while the image moved only on dispatch — verified live); the pooled secret-store clients are released on cache reset, with the AKV **credential** closed alongside the client since they hold separate transport sessions; an **orphan-secret sweep** that reports by default and purges only when configured, because what it deletes is a live warehouse credential; and **AppRole auth** (ADR 0039 phase 2) with proactive renewal and one re-login on 403, so a revoked token self-heals instead of 403ing until restart. Two lessons worth carrying: the sweep's ownership registry was wrong **twice** (Slack and Teams are separate refs on one row; `*_secret_name` keys live in JSONB) and its introspection guard was a **tautology** — it iterated the models already registered, so a new model was invisible to it; and #435 was assessed and left open with a corrected AC, since Azure CMK is **creation-time-only** and our IaC stack does not own the Postgres server. **Also unplanned, shipped 2026-07-27 — the secret-store seam (ADR [0039](docs/adr/0039-openbao-self-hosted-secret-backend.md), #1056/#1061):** a fourth `SecretStore` speaking the **KV v2 HTTP API, not a vendor SDK**, so one mode serves OpenBao / Vault Community / Enterprise / HCP — this closes the last seam where Azure was the only production implementation, which #505 (AWS/GCP IaC) would have walked straight into. **OpenBao** (MPL-2.0) is shipped and pinned; Vault Community is BUSL-1.1 and therefore rule-40-forbidden as a *distributed* component, supported only as a target. `SECRET_STORE=openbao` **replaces `redis`** — the plaintext store was the configured default of the published eval stack, i.e. evaluators were putting real warehouse credentials into an unencrypted key-value store. **Redis stays** (Celery broker + ADR 0035 rate limits). Two defects the review caught are worth carrying forward: **an outage must never be reportable as a state** — every caller branches on the exception TYPE and none reads the message, so folding "vault sealed" into `SecretNotFoundError` made an admin page render "not set" and silently skipped alert delivery (`AzureKeyVaultStore` had shipped that since Week 2); and a **`Settings` validator now fails a bad secret-store config at boot**, because the store built lazily and would otherwise die mid-run in a worker. Prod's 13 `conn-*` Key Vault keys were also renamed to one readable convention (`conn-<type>-<qualifier>-<env>-<shortid>`) — copy → read-back verify → repoint → re-test → purge, piloted on one connection first, 11/11 re-verified, and both Airflow connections verified on Key Vault **and** OpenBao. See [docs/ops-log.md](docs/ops-log.md). **DEPLOYED + LIVE-VERIFIED 2026-07-19** (then `dff6d958`; prod redeployed to `c401572d` 2026-07-26; **prod redeployed again to `bb40f0b7` 2026-08-03** — all three Container Apps (api/worker/frontend) rolled via the Deploy workflow ([run 30789647178](https://github.com/TheurgicDuke771/DataQ/actions/runs/30789647178)), migrate job Succeeded, post-deploy smoke green (healthz/SPA/deep-link 200, `/api`+`/mcp` 401 gates hold, authenticated `/me` verified, beat+watchdog clean on the new revision) — see below): the W4 work is on prod and exercised against real datasources. **ADLS** — volume read 643 rows off the resolved batch and arrival-time freshness reported 531.8h from the blob's last-modified: the harness genuinely stopped producing, which is precisely the incident #520 exists to catch and which an in-file MAX cannot see. **Snowflake** — `order_ts` (lower-case, emitted unquoted so the warehouse folds it) and `ORDER_TS` (upper-case, now quoted) returned **identical** timestamps, proving #937 both fixes mixed case and preserves the fold; volume over 32,840 real rows. **Unity Catalog** — freshness works **for the first time ever** (239.06h, lower/UPPER identical, confirming the backtick dialect). The dimension backfill (#952) classified **all 30 real prod checks, zero unclassified**, and real asset scorecards show genuine signal. **Live testing found a bug three green suites could not: UC freshness had NEVER worked** (#953) — the Databricks connector returns a TIMESTAMP's MAX as a **str** and the age math accepted only datetime/date, so a datasource the feature matrix marks ✅ produced no reading since #426. No unit test could see it: the type comes from the **driver**, and every fixture hands in a real datetime — the same fixture-encodes-our-model shape as #823 and the #520 Parquet bug. **Lesson: for anything whose type or value crosses a driver boundary, only a live run is evidence.** Also filed [#954](https://github.com/TheurgicDuke771/DataQ/issues/954): two prod Snowflake PATs are dead, and a **datasource** connection with a dead credential has no visible state until a run fails — I had to read worker logs to find out why, since #839's health signal covers only *orchestration* connections. That is the #828 blindness in a new place. **W4 CLOSED 2026-07-19, 8/8** — `schema_drift` (#592) shipped 2026-07-17, and this session cleared the remaining four in two groups. **Flat-file (#476, #520):** every backend CSV read hardcoded the pandas default comma, so a `;`/tab/pipe file parsed as ONE column named after the whole header — silently; all four reads now share one sniffing seam ([#934](https://github.com/TheurgicDuke771/DataQ/pull/934)). #476's casing half was **relocated by checking the code instead of the ticket** — the profiler's Core builders already quoted correctly; the only unquoted path was `monitors.py`'s f-string, fixed by returning a Core `Select` so the CONNECTION's dialect quotes it ([#937](https://github.com/TheurgicDuke771/DataQ/pull/937)); hand-rolled `"` quoting would have been actively wrong on UC, which uses backticks. Flat files gained freshness/volume monitors incl. **arrival-time freshness** — the case a warehouse can't express, since an in-file MAX is blind to a producer that stopped sending files ([#940](https://github.com/TheurgicDuke771/DataQ/pull/940)). **Dimension & score (#124, #889):** ADR [0038](docs/adr/0038-dq-dimension-classification.md) — seven canonical dimensions, closed vocabulary, derived default → stored → overridable, derivation deliberately partial so **NULL means unclassified and renders as a coverage gap** ([#945](https://github.com/TheurgicDuke771/DataQ/pull/945)); then `services/rollup.py` as the one histogram + score + latest-run query ([#948](https://github.com/TheurgicDuke771/DataQ/pull/948)) and the asset scorecard on top ([#950](https://github.com/TheurgicDuke771/DataQ/pull/950)).

**The lesson of the week is one shape, seen five times: a test that cannot express the failing case proves nothing, and "passes" is not "passes for the right reason."** (1) #520's Parquet freshness was broken on **every** Parquet file — Arrow-backed timestamps make `is_datetime64_any_dtype` False — and the suite was green because every fixture hand-built a numpy frame (#823 shape). (2) #124's export/import **silently classified every check in prod**, an ADR violation inside the PR that added the ADR; the tests missed it *by construction* — one POPPED the key rather than setting it null, and the only null that round-tripped was custom SQL, where derivation *also* returns `None`. (3) #889's `uncovered` meant "no results", not "no checks", so a check authored today read as missing until its next run — green because every fixture seeded a check AND a result together. (4) The #948 tie-break test was a **coin flip** (`max()` over random UUIDs matches the arbitrary answer ~50% of the time); I had "verified" it once and got lucky. (5) A `defaultdict` whose read created keys defeated my own mutation check for (3). **Mutation-checking every regression test is now the habit** — it caught (5), and (4) was caught by a reviewer doing it for me. Also recorded: the UI found two defects tests could not — a card asserting what the field below it denied, and a "Not covered" list hidden in exactly the state where it mattered most.

**Active blockers:** none — v1 is shipped. The qa-verifier go-live workout (2026-07-03) found and closed the one v1.0.0 blocker — NUL-byte input → unhandled 500 — same-day via [#570](https://github.com/TheurgicDuke771/DataQ/pull/570) (closed #567 + the older #371). All twelve non-blocking footguns/follow-ups this section used to list here as "open by choice" are now closed — spike survivors [#563](https://github.com/TheurgicDuke771/DataQ/issues/563), threshold-ordering [#568](https://github.com/TheurgicDuke771/DataQ/issues/568) (built as the W5 #1113 validation), checks_total edge [#571](https://github.com/TheurgicDuke771/DataQ/issues/571), CI flake [#573](https://github.com/TheurgicDuke771/DataQ/issues/573), `SecretStore.delete` cleanup [#372](https://github.com/TheurgicDuke771/DataQ/issues/372), Week-6 follow-ups [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) (W5 #1114) / [#351](https://github.com/TheurgicDuke771/DataQ/issues/351) (W5 #1116), and the Week-4 nits [#197](https://github.com/TheurgicDuke771/DataQ/issues/197)/[#199](https://github.com/TheurgicDuke771/DataQ/issues/199)/[#204](https://github.com/TheurgicDuke771/DataQ/issues/204)/[#194](https://github.com/TheurgicDuke771/DataQ/issues/194)/[#195](https://github.com/TheurgicDuke771/DataQ/issues/195). **One genuinely open follow-up remains:** profiler N+1 batching [#327](https://github.com/TheurgicDuke771/DataQ/issues/327). The ADR-0032 OTP track (below) also filed and closed several of its own findings same-track; the one it left open by choice is the `*_secret_name` SSRF/exfiltration class [#1118](https://github.com/TheurgicDuke771/DataQ/issues/1118), deferred to RBAC #740–#744. Full open-issue register in [docs/progress.md](docs/progress.md) (Snapshot table — re-counted per PR, not by memory); post-v1 backlog in [context/post-v1-roadmap.md](context/post-v1-roadmap.md).

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
| Auth | Three modes (ADR 0032): dev-bypass · email OTP (`dq_sess_` cookie sessions, local-stack default) · generic OIDC (`oidc-client-ts`, Azure AD validated) + backend `fastapi-azure-auth`; PATs (`dq_live_`) for API/MCP |
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
| `azure` | `@azure/mcp@3.0.0-beta.29` (npx) — **exact**, see caveats | Microsoft (`github.com/microsoft/mcp`) | Container Apps / Key Vault / Monitor + App Insights KQL without hand-rolled `az rest` — the 2026-07-13 outage was invisible in `/healthz` and only the telemetry showed the exporter log loop |

- **Trust prompt:** Claude Code prompts once per machine before starting servers from a project `.mcp.json`; approve only if the list above matches what's in the file.
- **Pin majors, not `latest`** — same rationale as the GX pin.
- **Supply-chain cadence:** quarterly audit per CONTRIBUTING.md rule 39 (deprecated/yanked/publisher-transfer check before any bump).
- **`azure` caveats (deliberate, re-check before relying on it):** `@azure/mcp`'s only dist-tag is a **beta** (`3.0.0-beta.29`) — Microsoft-published, but pre-stable, so treat tool-surface changes as expected. **This is the one entry pinned to an exact version, not a major**, and deliberately so: npm excludes prereleases from a bare major range, so `@azure/mcp@3` resolves to *nothing* (`E404 No match found for version 3`) and the server would fail to launch. The "pin majors" rule assumes a package with a stable major; this one has none, so the version moves only on a conscious bump. It authenticates through the **local `az` CLI credential**, so it inherits whatever that identity can do — use it for reading state, and keep the Key Vault rule intact: **never surface or print a secret value**. Microsoft telemetry is disabled via `AZURE_MCP_COLLECT_TELEMETRY=false` in the server env. It is only useful while the Azure estate is up — drop the entry if the wind-down completes.

**Not added, and why** (so the decision isn't re-litigated): a **Postgres MCP** was evaluated for run/result triage and rejected on supply-chain grounds. The reference `@modelcontextprotocol/server-postgres` is **deprecated**; the npm `postgres-mcp` is published by a single unaffiliated maintainer; the PyPI `postgres-mcp` declares **no license**, which CONTRIBUTING rule 40 / ADR 0031 forbids outright. `psql` over Bash already covers the need with zero added surface.
