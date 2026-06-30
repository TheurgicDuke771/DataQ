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
