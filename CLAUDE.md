# CLAUDE.md — DataQ project guide for AI assistants

> Single source of truth for any Claude / AI assistant working in this repo. Read this end-to-end before touching code.

---

## 1. Project summary

**DataQ** is a single-tenant data-quality control plane built around Great Expectations (GX Core). It runs DQ checks across **13 datasources** and integrates with **3 orchestration providers**.

| Layer | Components |
|---|---|
| **Datasources (you can write checks against)** | Snowflake (DEV/QA/UAT), ADLS Gen2, AWS S3 **and any S3-compatible store** (MinIO/Ceph/R2/Wasabi/Backblaze, via the optional `endpoint_url` — #1063), Google Cloud Storage (`gcs`, #1683), Unity Catalog (Databricks), Apache Iceberg (native `pyiceberg` read — ADR 0030), PostgreSQL, MySQL/MariaDB and Trino (any server/cluster, on the engine-generic SQL base — ADR 0045), SQL Server / Azure SQL / Synapse / Fabric SQL (`mssql`, same base — ADR 0044), Amazon Athena (`athena`, same base — region + IAM key, every check a billed query, IAM policy as the read-only guarantee), Amazon Redshift (`redshift`, same base — clusters + Serverless, read-only sessions), Google BigQuery (`bigquery`, same base — service-account key, a per-query `maximum_bytes_billed` cap, IAM roles as the read-only guarantee) |
| **Orchestration providers (monitor + trigger only — NOT datasources)** | Azure Data Factory (ADF), Apache Airflow, dbt (ADR 0029) |
| **Backend** | FastAPI + Celery + Redis + PostgreSQL + Alembic |
| **Frontend** | React + Vite + Ant Design + Monaco editor (generic OIDC — `oidc-client-ts`) |
| **Auth / secrets** | Three-mode ladder (ADR 0032): dev-bypass (eval) · **email OTP** (IdP-less, default for the local stack — #1150) · OIDC — **now genuinely provider-neutral end-to-end** (ADR 0026 amendment): the frontend's generic `AUTH_*` contract (ADR 0028) is matched on the backend by `core.auth.OidcBearerScheme` (OIDC discovery + JWKS via `PyJWT`, any standards-compliant issuer — Azure AD validated in prod, AWS Cognito the second target) alongside the original `fastapi-azure-auth` validator; mutually exclusive per deployment. Plus PATs (`dq_live_`) for API/MCP clients. **Authorization is two axes (ADR 0033):** a stored workspace role (`admin | member | viewer`) × the per-suite `view/edit` ladder (ADR 0027) — connection mutations are Admin-only, Viewers are read-only everywhere. Secrets: Azure Key Vault / OpenBao / AWS Secrets Manager (ADR 0039) |
| **Deploy** | Azure Container Apps or AWS ECS Fargate, supported at the same level (reference OpenTofu stacks), or any Kubernetes cluster via the Helm chart. API + worker + beat + frontend; the frontend is the sole public surface, the api internal — ADR 0028 §5 |
| **Observability** | structlog + OpenTelemetry (`core/otel.py`), exported to Application Insights on Azure, X-Ray/CloudWatch on AWS, or any OTLP collector |
| **AI integration** | FastMCP (59 curated tools mounted at `/mcp` — 35 read-only, 18 that change state, 6 live-probe tools gated like writes; ADR 0008 + Theme 13 Tiers 1 and 2 and Tier 3A/3B, #529/#1424, + the `get_doc` curated-docs tool (#1626) and the reusable-notification-channel read tools (#1775), + `trace_column_lineage` (#1710), + the seasonal-calendar reads `list_seasonal_periods` / `get_detected_seasonal_periods` (#2440), + the data-contract reads `list_contracts` / `get_contract` / `export_contract` (#1977)) — Claude Desktop / Claude.ai / Copilot / Cursor |

v1 shipped 2026-07-04 after an 8-week plan; the project is now in the v1.3 cycle (§13). Scope: single tenant, suite-level access sharing; hosted on Azure or AWS, or any Kubernetes cluster via the Helm chart.

---

## 2. Architecture at a glance

See [docs/site/architecture/overview.md](docs/site/architecture/overview.md) for the full diagram (Mermaid — renders on GitHub).

```
Browser ──HTTPS──► Frontend Container App (nginx SPA, sole public ingress)
AI clients ──MCP/HTTP──► │  proxies /api + /mcp + /healthz same-origin
                         ▼
                    FastAPI (Container Apps, INTERNAL ingress) ──► PostgreSQL
                         │  │
                         │  └──► Celery worker ──► GX execution ──► Snowflake / ADLS / S3 / UC
                         ├──► Celery beat (own process/service since #1811 — schedules only,
                         │        never executes a task, so a worker OOM can't kill it)
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
│   │   ├── orchestration/       # OrchestrationProvider abstraction (ADF, Airflow, dbt)
│   │   ├── datasources/         # ConnectionAdapter + CheckRunner per type; gx_runner.py (shared GX translation), flatfile.py (flat-file IO + runner + batch resolution), sql.py (shared SQL-identifier allowlist — #428)
│   │   ├── mcp/                 # FastMCP tools (59 at /mcp — ADR 0008)
│   │   ├── alerting/            # ResultPublisher seam: Teams / Slack / email / webhook, routing, dedup, suppression
│   │   ├── lineage/             # ADR 0034 lineage pull (dbt manifest, Marquez, warehouse-native) + OpenLineage emitter
│   │   ├── llm/                 # LLMProvider seam (ADR 0042): anthropic + openai-compatible
│   │   └── worker/              # Celery app + tasks (run_suite, beat-dispatched sweeps)
│   ├── alembic/
│   ├── scripts/                 # operator scripts (perf_baseline, one-off backfills)
│   └── tests/                   # + tests/support/ (adversarial harness), tests/integration/ (end-to-end datasource runs)
├── frontend/                    # React + Vite + Ant Design (Node, pnpm)
│   ├── src/
│   └── tests/
├── packages/
│   └── dataq-client/            # Python client + `dataq` CLI (#1829): generated from docs/site/reference/openapi.json by scripts/generate-dataq-client.py (CI drift-checked), plus a hand-written convenience layer; its wheel + sdist are attached to the GitHub Release by publish-client.yml on a release tag (not on PyPI), or installed from main
├── docs/
│   ├── site/                    # everything PUBLISHED to the docs site (docs_dir) — guides, architecture/, adr/, compliance/
│   └── *.md                     # internal planning docs (progress/retro/ops-log/status-history/…) — outside docs_dir, never built
├── marketing/                   # public GH Pages marketing site (index.html — the landing page); occupies the Pages ROOT, docs build one level down into site/docs/ (mkdocs.yml site_dir)
├── integrations/                # user-deployed snippets (NOT app code; e.g. Airflow DAG callback)
│   ├── adf/                     # ADF gate snippet (ADR 0046)
│   ├── airflow/                 # dataq_airflow_callback.py + setup README
│   └── dbt/                     # dbt post-build callback
├── changelog.d/                 # one changelog fragment per PR (<issue>.<section>.md), assembled at release — CONTRIBUTING rule 43
├── deploy/                      # deploy/README.md runbook + terraform/{azure,aws} (OpenTofu IaC — ADR 0024) + helm/dataq (Kubernetes chart)
├── scripts/
│   ├── setup.sh                 # one-command dev env bootstrap
│   ├── local-ca.sh / local-smoke.sh  # trust the prebuilt stack's CA / smoke it
│   └── check-*.py               # pre-commit + CI guards (identifiers, docs publication, …)
├── context/                     # original product/roadmap context (read-only reference)
│   ├── DataQ_platform_roadmap.md
│   └── post-v1-roadmap.md, roadmap-v1.2-v1.3.md  # deferred-work index + forward roadmap
├── .github/
│   ├── workflows/
│   ├── pull_request_template.md
│   ├── CODEOWNERS
│   └── ISSUE_TEMPLATE/
├── docker-compose.yml           # contributor stack
├── docker-compose.ghcr.yml      # prebuilt-image, production-like local stack (HTTPS, generated CA)
├── environment.yml              # conda env — pip section points at backend/requirements-dev.txt
├── pyproject.toml               # Black + Ruff + mypy config
├── CONTRIBUTING.md
├── CLAUDE.md                    # this file
└── README.md
```

**`packages/`:** exists since #1829 for the one real shared package, the generated OpenAPI client. Everything else stays flat.

---

## 4. Datasources vs orchestration — critical distinction

**Datasources** are stores you write DQ checks against:
- Snowflake (DEV/QA/UAT)
- ADLS Gen2 (flat files)
- AWS S3 — and any S3-compatible store, via the connection's optional `endpoint_url` (#1063)
- Google Cloud Storage (`gcs`, #1683) — flat files on the shared runner; service-account key file or anonymous, optional `endpoint_url`. Verified against `fake-gcs-server` only (#2224).
- Unity Catalog / Databricks
- Apache Iceberg (native `pyiceberg` read — ADR 0030; engine-registered Iceberg tables also work zero-code under the `snowflake`/`unity_catalog` connections)
- PostgreSQL — any server, self-hosted or managed (#1678). The first engine on the **engine-generic SQL base** (ADR 0045, `datasources/generic_sql.py` + one `SqlEngineSpec` per engine, registry in `sql_engines.py`): pushdown only, read-only sessions, every SQL capability set derives from `GENERIC_SQL_TYPES`. MySQL/MariaDB (#1684, PyMySQL — never a GPL driver) is the second engine — **never copy the Snowflake adapter**.
- Trino — any cluster (incl. Starburst), `trino` client, one catalog per connection (#1685), the federation multiplier on the same base. **No read-only session exists on Trino** — the Trino user's access control is the guarantee; names must be lower case; TLS `verify-full` or explicit `disable`, password/JWT only over verified TLS. Amazon Athena was split out to #2131.
- Amazon Athena (#2131, `pyathena`, MIT): the endpoint is derived from the region, the credential an IAM access key (secret key as connect args, never in the URL); a Glue database is the schema. No read-only session — the IAM policy is the guarantee. Every check is a billed Athena query.
- Amazon Redshift (#1682, `sqlalchemy-redshift` dialect over psycopg2, MIT): provisioned clusters and Serverless workgroups, database-user auth. Read-only sessions like PostgreSQL; names lower case; every TLS mode verifies against Amazon's CAs. GX is handed the schema as well as the scoped session (`gx_schema_with_session`) because GX's Redshift column lookup otherwise matches the table name in every schema.
- Google BigQuery (#1681, `sqlalchemy-bigquery` dialect over `google-cloud-bigquery`, MIT/Apache-2.0): one project per connection, a dataset is the schema, a service-account key file becomes a `bigquery.Client` passed as a connect arg (never in the URL). Every query carries the connection's `maximum_bytes_billed` (default 10 GiB) on the client's default job config. No read-only session — `roles/bigquery.dataViewer` + `roles/bigquery.jobUser` are the guarantee. Names case-sensitive; catalog reads use the free metadata API (`SqlCatalog.api`), not `INFORMATION_SCHEMA`. Verified against `goccy/bigquery-emulator` only (#2224).
- SQL Server / Azure SQL / Synapse / Fabric SQL (`mssql`, #1679, ADR 0044) on the same base. Driver `python-tds` (MIT, shipped) with DataQ's own SAN/hostname validator + named-instance routing fix (`datasources/mssql_tds.py`); TLS always verified; SQL login or Entra service principal. **Not read-only at the session** (TDS has no such setting) — the custom-SQL gate + a `db_datareader` login are the guard. Fabric SQL endpoints go through the **optional user-installed ODBC lane** (`driver: odbc`) permanently — the python-tds Fabric fix (#2126) was closed not-planned and the default driver refuses a Fabric host with an explained error; DataQ never ships msodbcsql18/pyodbc.
- OneLake (Fabric lakehouse files) is not a separate type — an ADLS Gen2 connection with `auth_type: service_principal` pointed at `onelake.blob.fabric.microsoft.com` (#1680).

**Orchestration providers** are NOT datasources. They are workflow engines whose pipelines/DAGs we observe and react to. Their *only* four responsibilities in DataQ:

1. **Monitor** pipeline/DAG runs → stored in `pipeline_runs` table (separate from `runs` / `results`).
2. **Detect failure** in near-real-time via provider-specific event channels (webhook for both).
3. **Trigger suite execution on successful completion** via `trigger_bindings` (`provider`, `pipeline_or_dag_id`, `suite_id`, `env`). Failure events alert the user but do NOT trigger suite runs.
4. **Answer a gate request** (ADR 0046): `POST /api/v1/orchestration/gate` lets a pipeline stage start its bound suites for this run and wait for the verdict, or only read it. Provider-agnostic (keyed by bindings and the `triggered_by` marker, which also keeps it idempotent with responsibility 3). DataQ still never pauses or fails a pipeline — the customer's DAG acts on the answer.

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
- **v1.1+:** platform-native engines are **connection-anchored** (ADR [0036](docs/site/adr/0036-connection-anchored-check-engines.md)): every datasource connection offers GX; a Snowflake connection additionally offers **DMF**, Unity Catalog offers **DQX** (DLT/streaming — GX is batch-only), a future BigQuery type offers **Dataplex**. Engine selection is **per check** (`check.engine`, default `gx`, validated against the connection's capability set; `kind` stays orthogonal to `engine`) — supersedes the earlier suite-level `engine: gx | dqx` toggle sketch. DMF was the first native build. **DQX shipped 2026-09-28**: it is never a DataQ dependency (the Databricks License permits use only with Databricks services), so each run submits one serverless job to the user's own workspace that installs pinned DQX there and returns failing-row counts only. DQX evaluates bare strings as Spark SQL, so rules come only from a closed vocabulary with typed arguments (ADR 0036 amendment). Dataplex stays trigger-gated.
- **Monitor-kind seam (do-now, Week 3):** not every monitor is a GX expectation. A `check.kind` discriminator (`expectation` in v1; `freshness | volume | schema_drift | anomaly | comparison` reserved — freshness/volume shipped post-v1 (#426/#437) and **`comparison` shipped v1.1 W3** (ADR 0015, #791–#795) and **`schema_drift` shipped v1.1 W4** (#592) and **`anomaly` shipped v1.1 W5** (#593, rolling z-score baseline with optional seasonality, PRs #1117/#1119) and **`pii_drift` shipped v1.3 W1** (#1675, ADR 0049 phase 2 — profiles the target every run, counts newly PII-looking columns nobody classified, baseline-and-skip on run 1)) + numeric `metric_value` on results let v1.x auto-monitors slot in without a check/result schema rewrite. This seam is **orthogonal to the datasource seams** (`CheckRunner`, `ConnectionAdapter`): it varies by *monitor kind*, not datasource. See ADR `0012` (and `0014` for the reserved `comparison` / cross-dataset reconciliation kind) and post-v1 roadmap Theme A. Most real incidents are freshness/volume, not value-level — this is the leap from "GX runner" to DQ platform.
- **DQ-dimension axis (v1.1 W4, ADR [0038](docs/site/adr/0038-dq-dimension-classification.md)):** every check also carries a `dimension` — accuracy / completeness / consistency / integrity / timeliness / uniqueness / validity — a **third axis orthogonal to both `kind` (how the monitor works) and `engine` (what evaluates it)**. Closed vocabulary via table `CHECK`; derived by default from the check type, **stored** (so #889 aggregates in SQL and an override survives), and overridable at any time. Derivation is **deliberately partial** — `accuracy`/`integrity` are never derivable from a rule shape and custom SQL is an arbitrary predicate — so **NULL means unclassified and must render as a coverage gap**, never silently bucketed. Existing rows were backfilled (#952, §5 amended). It is the input to the asset DQ scorecard (#889), whose value is *coverage* ("this asset has no Timeliness checks") more than score.
- **Week-3 outcome (done):** the UC run path is thin behind `UnityCatalogCheckRunner` (reads the table into a GX DataFrame asset — the DQX swap-in shape), and `check.kind` + `metric_value`/`duration_ms` shipped in the one threshold migration, so the monitor-kind impls won't ripple into the suite/check/result layer later. **Amended v1.1 W6 (#1179):** the UC runner now builds **two** batches — a GX **Databricks-SQL** batch (custom SQL; since #1532 also the default for the audited ordinary expectations — pushdown, worker memory flat) and the DataFrame one (`to_be_of_type`, sampled suites, the `UC_SQL_PUSHDOWN=false` rollback). `UnexpectedRowsExpectation`'s metrics have a SqlAlchemy provider and no pandas one, so ADR 0019's UC support had never once worked in a run *or* a dry-run; it is SQL by definition and was never going to be a DQX rule, so the DQX swap-in argument is unaffected for everything it actually covers. **Standing rule (user direction, 2026-08-28): on Databricks/UC, pushdown is the DEFAULT for any new check type** — it joins `SQL_PUSHDOWN_EXPECTATION_TYPES` after the #1532 live-Databricks-SQL vetting; the pandas frame batch is the exception and needs a recorded reason (no GX SQL provider, dtype-vocabulary semantics, sampling). Conversion of the 14 eligible legacy frame types is #1624.

---

## 6. Working agreements (rules above feature work)

Full list (45 rules across 8 categories) lives in [CONTRIBUTING.md](CONTRIBUTING.md). Highlights:

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
- `main` is protected: PR + passing required checks + resolved review threads + no force-push, and the branch must be **up to date with `main`** (merge `origin/main` in after another PR lands, then let CI re-run). (≥1 approving review is disabled during solo-dev phase; re-enable before onboarding a second contributor.)
- **Squash-merge only into `main`.**
- **Stack dependent PRs** (CONTRIBUTING rule 9a): when PRs build on each other (feat → its docs, backend → UI, migration → code), cut each branch from the previous PR's branch and open it with `--base <parent-branch>`. Merge bottom-up with `gh pr merge --squash --delete-branch` (GitHub retargets the next PR to `main`), then merge `origin/main` into the next branch, keep the branch's side of any conflict, check `git diff origin/main...HEAD --stat` shows only its own files, and push normally — never rebase + force-push. Unrelated work still branches from a freshly pulled `main`.
- **Conventional commits** (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).

### CI/CD quality gates (block merge)
- Ruff (lint), Black `--check` (format), mypy (types), pytest (from W8), frontend lint/format/test.
- `betterleaks` secret scanning (pre-commit + CI).
- Bandit (Python SAST) + CodeQL.
- **Dependency CVE audit (CI): `pip-audit -r backend/requirements-dev.txt` (full backend runtime + test surface) + `pnpm audit --audit-level=high` (frontend).** Synchronous merge gate; complements the async Dependabot layer below.
- **Python deps have one source of truth: `backend/requirements.txt`** (runtime hub) → `requirements-dev.txt` (`-r` it + test toolchain) → `environment.yml` + CI all install from it. The re-listed subsets `requirements-dev.txt` pulls are `requirements-typecheck.txt` (the typed deps mypy needs) and `requirements-tooling.txt` (Black/Ruff/mypy/Bandit/pre-commit); the pre-commit ruff/black/mypy hooks are `language: system` and run the conda env's copies of exactly these files, so `.pre-commit-config.yaml` holds no second version list (#2080 — Dependabot only edits pip files, so that list turned every bump red). `scripts/run-pinned-tool.py` fails a hook whose env drifted from the pins. `requirements-mutation.txt` (mutmut) is **standalone — not `-r`'d by anything**, so it stays off CI's install + `pip-audit` surface (manual tool, CONTRIBUTING rule 4a). Bump a Python version in `requirements.txt` only.
- Dependabot for npm + pip + github-actions — **version updates + security alerts/updates both enabled** (alerts scan the full pip+npm dependency graph).

### Tooling (locked in Week 1, do not drift)
- **Python:** conda env (`conda create -n dataq python=3.13`) — *not* venv, *not* poetry. (3.13 since ADR 0017; was 3.11.)
- **Black** formatter (CI-enforced).
- **Ruff** lint, **mypy** types, **structlog** logging, **Pydantic Settings** config (12-factor).
- **Frontend:** Prettier + ESLint.

### Observability
- **Structured logging everywhere.** structlog, JSON, `request_id` correlation propagated FastAPI → Celery → GX.
- **PII redaction at logger level** (failed-check sample rows may contain sensitive data).
- **Exception tracking and tracing through the OTel seam** (`core/otel.py`), exported to whichever backend the deployment uses.

### Database
- **Backward-compatible migrations only.** No `DROP COLUMN` + code change in same PR; two-step deploys.
- Migration PR checklist: rollback plan + "tested up + down locally."

### Documentation
- **ADRs in `docs/site/adr/`** — one short markdown per significant decision.
- `scripts/setup.sh` — one command from clone to working dev env.

### Security cadence
- End-of-week quick scan, every week: vuln alerts (Dependabot alerts + CI `pip-audit`/`pnpm audit`), secret scan, OWASP spot check, Key Vault audit.
- Hard security review gate before each release (each cycle's bug-fix & security week).

---

## 7. Required reading before coding

1. [CONTRIBUTING.md](CONTRIBUTING.md) — full 45-rule working agreements + DoD + commit/branch conventions
2. [docs/site/adr/](docs/site/adr/) — all ADRs (architecture decisions with rationale)
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

The full decision index — one line per ADR with status — lives at **[docs/site/adr/README.md](docs/site/adr/README.md)** and is the **single source of truth** (this section used to duplicate it as a table and the two drifted; it no longer does). Read the index before coding and open the individual ADR whenever a decision bears on your change. The day-to-day operating rules those decisions distill into are already captured in §4–§6, §10 and §11 of this file.

---

## 10. Critical pointers (easy to get wrong)

- **`pipeline_runs` ≠ `runs`.** Orchestrator pipeline executions live in `pipeline_runs`; DQ suite executions live in `runs`. They link via `triggered_by: '<provider>:<pipeline_or_dag_id>:<provider_run_id>'`.
- **`trigger_bindings` is provider-agnostic.** Composite key (`provider`, `pipeline_or_dag_id`, `env`) → `suite_id`. Don't add an ADF-specific bindings table.
- **PII redaction at the logger level**, not at every call site. The redactor sits in `backend/app/core/logging.py`.
- **Backward-compatible migrations only.** Code that depends on a new column ships in a separate PR *after* the migration is deployed.
- **The Week-3 threshold migration already added the schema seams (done).** It landed `check.kind` (default `'expectation'`), `results.metric_value` (NUMERIC) + `duration_ms` (INT), and the severity thresholds — see ADR `0012`. `metric_value` is the SQL-aggregatable scalar a monitor measured; **don't store metrics only in JSONB `observed_value`** (you can't `AVG()`/`STDDEV()` it for trends or anomaly baselines), and **don't add a second migration re-introducing these columns**.
- **Secret scanning in pre-commit AND CI.** Don't rely on one alone.
- **ADF alerting needs the deployment's public URL.** The Azure Monitor action group posts to `<frontend>/api/v1/orchestration/events/adf`, so the deployment and its public ingress come first.
- **MCP tool descriptions are LLM-facing, not REST-API-facing.** Write them for natural-language selection; test against the 4 canonical NL queries in the roadmap.

---

## 11. What NOT to do

- ❌ Don't add ADF or Airflow as a queryable datasource in the connection editor / check editor / suite model.
- ❌ Don't bypass the `OrchestrationProvider` abstraction with provider-specific branching in service code.
- ❌ Don't deepen Azure lock-in: no reading Entra/OIDC provider claims in route/service code (depend on the generic `get_current_user`), no hardcoded Azure resource names/endpoints in business logic, no Azure-only assumptions baked into container images. Azure is one impl behind each seam — see ADR [0010](docs/site/adr/0010-provider-agnostic-infrastructure-seams.md) / [0013](docs/site/adr/0013-marketplace-distribution-and-anti-lock-in.md).
- ❌ Don't `git commit --no-verify` past hooks. If a hook fails, fix the underlying issue.
- ❌ Don't commit `.env` files. Use `.env.example` / `.env.app.example` as the templates.
- ❌ Don't put a credential — **even a local/mock one** — in any git-tracked file (templates, `scripts/`, CI, compose). Env templates ship the secret keys **blank** with the shape in a comment; `scripts/setup.sh` generates the local-dev password into the gitignored `.env`/`.env.app` on first run. Non-secret config defaults and non-secret identifiers (db/user name) may stay populated.
- ❌ Don't drop columns in the same PR as the code change that stops using them. Two-step it.
- ❌ Don't fix bugs silently. Raise a GitHub issue, then PR with `Fixes #N`.
- ❌ **Don't dismiss a finding because it's pre-existing.** DataQ is in production — a defect that predates your change is live for users right now, so its age is irrelevant to whether it matters. Every finding ends **fixed or filed**, never "noted in a review reply" or "documented in a docstring" (CONTRIBUTING rule 3a). Concluding it is genuinely not a defect is fine, but record the determination and evidence — verified-benign is a valid outcome, unexamined is not.
- ❌ Don't batch unrelated changes into one commit. One functionality per commit.
- ❌ Don't track GX Core at "latest." Pin the version in `environment.yml` — GX v1 API has drifted across point releases. The same goes for **pandas and numpy**, which sit under GX and every frame lane: both are pinned in `backend/requirements.txt` (#2148 — unpinned, the image ran pandas 3 while a dev env ran 2).
- ❌ Don't add a dependency under a strong-copyleft or source-available license (GPL, AGPL, SSPL, BUSL/Elastic, Commons-Clause) — DataQ ships MIT (ADR [0031](docs/site/adr/0031-oss-byol-distribution-licensing.md), CONTRIBUTING rule 40); weak copyleft (LGPL/MPL) is OK with notices. Exceptions need an ADR.
- ❌ Don't use venv or poetry for backend dev. Conda only.

---

## 12. Where things live

| Artifact | Location |
|---|---|
| Product roadmap (100 tasks, 8 weeks) | [context/DataQ_platform_roadmap.md](context/DataQ_platform_roadmap.md) |
| Forward roadmap (v1.2 thesis + proposed v1.3) | [context/roadmap-v1.2-v1.3.md](context/roadmap-v1.2-v1.3.md) |
| Story ledger (curated narrative wins — internal) | [docs/stories.md](docs/stories.md) |
| System architecture diagram | [docs/site/architecture/overview.md](docs/site/architecture/overview.md) |
| Architecture Decision Records | [docs/site/adr/](docs/site/adr/) |
| Working agreements (full 45-rule list) | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Live task tracker (post-v1, per-PR status) | [docs/progress.md](docs/progress.md) — completed ledgers are archived at [docs/progress-v1.md](docs/progress-v1.md), [docs/progress-v1.1.md](docs/progress-v1.1.md) and [docs/progress-v1.2.md](docs/progress-v1.2.md) |
| **Deploy runbook + pre-/post-deploy checklists** | [deploy/README.md](deploy/README.md) — provisioning, the `workflow_dispatch` Deploy flow, and the **pre-deploy** (CI green, docs current, migration-safe) + **post-deploy smoke** (login, UI renders, every high-level flow works, infra rolled) checklists. **Run both around every deploy.** |
| **Static read-only demo** (Pages `/demo/`, #2419) | [frontend/e2e-demo/README.md](frontend/e2e-demo/README.md) — the real SPA built with `VITE_DEMO`, answering from API responses that CI's `frontend-e2e` job records off the seeded stack on every run (never committed); `publish-demo` republishes on each push to `main`. **A new `<Route>` must be added to `e2e-demo/routes.ts`** (a Vitest check fails otherwise). |
| Memory (cross-session AI context) | `~/.claude/projects/-Users-arijit-Coding-Python-DataQ/memory/` |
| **Ops log** | [docs/ops-log.md](docs/ops-log.md) — append-only, git-tracked: harness lifecycle and credential rotations up to the estate's retirement (2026-10-03), with absolute UTC timestamps, identifiers and dates only — **never a secret value**. Later harness and teardown activity is internal and kept outside the repo. A live product deployment's credential rotations still get an entry. |

---

## 13. Status & current milestone

> Task-level status lives in [docs/progress.md](docs/progress.md) (the live tracker, updated per PR). This section carries only the headline. The long-form history this section used to hold (v1 → v1.3 Week 6, with every incident and lesson) is archived verbatim in [docs/status-history.md](docs/status-history.md).

**Releases:** `v1.0.0` 2026-07-04 · `v1.1.0` 2026-08-21 · `v1.2.0` 2026-10-04. Frozen ledgers: [progress-v1.md](docs/progress-v1.md), [progress-v1.1.md](docs/progress-v1.1.md), [progress-v1.2.md](docs/progress-v1.2.md); retros: [v1](docs/retro-v1.md), [v1.1](docs/retro-v1.1.md), [v1.2](docs/retro-v1.2.md).

**Current cycle: v1.3 — 8 weeks, 2026-10-05 → 2026-11-29, no net-new features** (user direction 2026-10-05): roadmap and filed features (Weeks 1–5), bug fixes and security (Week 6), polish (Week 7), debt, decisions and the close (Week 8). Theme in [context/roadmap-v1.2-v1.3.md](context/roadmap-v1.2-v1.3.md).
- **Current week: Week 7 — polish: MCP & API, performance, accessibility (due 2026-11-22).**
- Weeks 1–3 closed 2026-10-20/21, Weeks 4–5 on 2026-10-25 (two weeks early), Week 6 on 2026-11-08 (a week early, nothing rolled). Highlights: GCS and BigQuery datasources, PII-drift monitor, data contracts on ODCS v3 (ADR 0052), suite-on-suite triggers (ADR 0051), tags, run comparison, notification centre, command palette, the audited audit-log scrub (ADR 0041 amendment) and the per-query time limit on every SQL engine.
- **Exit gate (every week):** each item shipped or rolled by name with a reason, every one meeting CONTRIBUTING rules 44 (new code meets the standards in force, accessibility included) and 45 (interfaces use their open standard).
- **Active blockers:** none.

**Deployment status.** The project's own dev/test cloud estate (Azure and AWS reference deployments, the harness, the Snowflake and Databricks test accounts) was retired 2026-10-03. That is our test environment only: **Azure and AWS are both supported for users at the same level**, with reference OpenTofu stacks in `deploy/terraform/{azure,aws}`, a Helm chart, and both deploy workflows; cloud hosting stays the recommended production path. Live verification against Snowflake, Unity Catalog, Azure or AWS (the #953 rule) waits on a cloud test instance (#2224): say so rather than assume one. The local stacks are the working environment — `docker-compose.ghcr.yml` is the production-like one (HTTPS from a generated CA, TLS on every hop, persistent `DATAQ_DATA_DIR`); `docker-compose.yml` is the contributor stack.

**Standing lessons** (each cost real time; the archive has the incidents):
- "Merged" is not "deployed", and "deployed" is not "works" — verify a fix in the running artifact and in observed behaviour.
- For any value that crosses a driver boundary, only a live run is evidence; fixtures encode our model of the driver.
- Prove a regression test can fail: mutation-check it against the unfixed code, especially for ordering, concurrency and stale state.
- The test suite is blind to privileges (it runs as a superuser) and to commits (a shared transaction); use the real-DB harnesses for those.
- A guard applied at one door is usually missing at its sibling (a second route, the MCP twin, the import path) — check them all.
- LLM-facing tools must state their blind spots in fields, not just prose; a new MCP tool ages the docstrings of its neighbours.
- Check what the code does, not what the ticket or doc says.

Update this section when a week closes: the current week, its exit gate, and any open blocker by number. Per-PR ticks go in `docs/progress.md`.

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
| Auth | Three modes (ADR 0032): dev-bypass · email OTP (`dq_sess_` cookie sessions, local-stack default) · generic OIDC (`oidc-client-ts`, Azure AD + Cognito validated) + backend `fastapi-azure-auth`; PATs (`dq_live_`) for API/MCP |
| Authorization | **Two axes (ADR 0033):** stored workspace role `users.role` (`admin \| member \| viewer`) × per-suite grants (`view \| edit`, ADR 0027). Both enforced on REST and MCP; both resolved per request, so a role change applies to existing PATs immediately |
| Secrets | `SecretStore` seam: Azure Key Vault · AWS Secrets Manager · OpenBao (KV v2, ADR 0039) · `env` (local dev only) |
| Hosting | Azure Container Apps · AWS ECS Fargate · Helm (Kubernetes); frontend = sole public surface, api internal — ADR 0028 §5 |
| Observability | structlog + OpenTelemetry → Application Insights · X-Ray/CloudWatch · any OTLP collector |
| CI/CD | GitHub Actions |
| API docs | FastAPI Swagger + ReDoc |
| MCP | FastMCP (PrefectHQ) — 59 curated tools at `/mcp` (35 read-only, 18 that change state, 6 live-probe tools gated like writes; ADR 0008 + its #529 Tier-1/Tier-2 and #1424 Tier-3A/3B amendments + #1626's `get_doc` + #1775's channel read tools + #1710's `trace_column_lineage` + #2440's seasonal-calendar reads + #1977's data-contract reads) |
| Python tooling | conda + Black + Ruff + mypy + pytest + Bandit |
| Frontend tooling | Prettier + ESLint + Vitest + React Testing Library |
| Secret scanning | betterleaks (pre-commit + CI) |
| SAST | Bandit + CodeQL |

### Client-side MCP servers (`.mcp.json`)

Distinct from DataQ's **own** FastMCP server at `/mcp` (ADR 0008 — the 59 tools DataQ *serves* to AI clients), the repo-root **`.mcp.json`** configures MCP servers that AI assistants working in this repo *consume*:

| Server | Package (pinned major) | Publisher | Purpose |
|---|---|---|---|
| `context7` | `@upstash/context7-mcp@1` (npx) | Upstash | Up-to-date library docs lookup while coding |

- **Trust prompt:** Claude Code prompts once per machine before starting servers from a project `.mcp.json`; approve only if the list above matches what's in the file.
- **Pin majors, not `latest`** — same rationale as the GX pin.
- **Supply-chain cadence:** quarterly audit per CONTRIBUTING.md rule 39 (deprecated/yanked/publisher-transfer check before any bump).

**Not added, and why** (so the decision isn't re-litigated): a **Postgres MCP** was evaluated for run/result triage and rejected on supply-chain grounds. The reference `@modelcontextprotocol/server-postgres` is **deprecated**; the npm `postgres-mcp` is published by a single unaffiliated maintainer; the PyPI `postgres-mcp` declares **no license**, which CONTRIBUTING rule 40 / ADR 0031 forbids outright. `psql` over Bash already covers the need with zero added surface.

<!-- entire-agent:begin -->
Read .entire/agent-guide.md for this repository's workflow, source inspection, and verification guidance.
@.entire/agent-guide.md
<!-- entire-agent:end -->
