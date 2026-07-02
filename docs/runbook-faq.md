# Runbook & FAQ

## Release checklist (v1)

- All CI gates green on `main`; no open release-blocking issues on the current milestone.
- Backend image built + pushed to GHCR with an **immutable** tag (not `latest`).
- DB migrations are backward-compatible; the migrate job runs `alembic upgrade head`
  **before** the app rolls.
- Deploy via the **Deploy** workflow (`workflow_dispatch`); verify `/healthz` 200,
  `/api/v1/me` 401 (auth enforced), SPA + deep links load.
- Docs site published (this site) and linked from the README.

Full deploy steps + verification: **[deploy/README.md](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md)**.

## Live smoke (deployed stack + harness data)

Automated, opt-in (never CI):

1. **API-level:** `DATAQ_API=https://<frontend-host> DATAQ_BEARER=<AAD token> python -m
   backend.scripts.e2e_smoke` — read + authoring round-trips against the live API
   (12 checks).
2. **Browser-level:** `E2E_LIVE_BASE_URL=https://<frontend-host> pnpm e2e` in
   `frontend/` — a headed one-time OIDC sign-in, then read-only specs (dashboard KPIs,
   live suite + checks, run-detail). See
   [frontend/e2e/README.md](https://github.com/TheurgicDuke771/DataQ/blob/main/frontend/e2e/README.md).

Manual checklist (the mutating tail):

- Trigger a live suite run (Run now) on a harness suite → completes green on real
  warehouse/file data.
- Let a harness pipeline (ADF or Airflow) succeed → the bound suite auto-runs and
  correlates on **Results → Pipelines**.
- Force a failing run → the Teams/Slack/email alert arrives with the right severity;
  a repeat failure is deduped.
- MCP: point Claude Desktop at `https://<frontend-host>/mcp` and run the 4 canonical
  queries (what failed today / run suite X / why did pipeline Y fail / add a null check).

## Known limitations (v1)

- **GX-only** check engine (Databricks DQX deferred to v1.1); batch-oriented (not
  streaming).
- **Single tenant**, suite-level access sharing; workspace-admin is a config allowlist.
- Interactive **datasource browsing** (container browser, 3-level UC catalog picker) is
  deferred — you specify targets explicitly. JSON flat files deferred (CSV/Parquet in v1).
- Auth is **Azure AD only** (no API keys / service tokens yet — see ADR 0026 / #461).

## FAQ

**Is ADF/Airflow a datasource?** No — they're orchestration providers DataQ monitors and
can trigger from. You never write checks against them. See **[Concepts](concepts.md)**.

**Do I need Azure to run it locally?** No — local dev uses a dev-bypass auth and
docker-compose. Azure is one deployment target behind the app's seams (ADR 0010/0013).

**Where do failed-row samples go?** Stored with the result, **PII-redacted**, and purged
after a retention window — never written to logs.

**Can an AI assistant use DataQ?** Yes — 8 MCP tools at `/mcp` (Claude Desktop / Claude.ai
/ Copilot / Cursor), Azure-AD authenticated. See the README's MCP section.
