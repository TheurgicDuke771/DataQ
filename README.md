# DataQ

> Data quality monitoring platform built around Great Expectations — Snowflake (DEV/QA/UAT), ADLS Gen2, S3, Unity Catalog (Databricks), with ADF + Airflow orchestration integrations.

**Status:** pre-v1 — Week 7 in progress (deployment, hardening & docs). Weeks 1–6 complete; v1 is **deployed to Azure** (Container Apps + Static Web App, with Key Vault, App Insights, and orchestration polling live). Live task-level progress at [docs/progress.md](docs/progress.md).

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · Celery · Great Expectations · SQLAlchemy + Alembic · PostgreSQL · Redis |
| Frontend | React · Vite · Ant Design · MSAL React |
| Auth / secrets | Azure AD (MSAL) · Azure Key Vault |
| Hosting | Azure Container Apps + Static Web App + Application Insights (deployed) |
| AI integration | FastMCP — 8 curated MCP tools at `/mcp` for Claude Desktop / Copilot / Cursor |

## Quick start

```bash
git clone https://github.com/TheurgicDuke771/DataQ.git
cd DataQ
./scripts/setup.sh       # conda env + pre-commit + docker-compose + migrations
conda activate dataq
docker-compose up
```

Backend at `http://localhost:8000` (Swagger at `/docs`), frontend at `http://localhost:3000`.

## MCP (AI assistant access)

DataQ exposes 8 curated MCP tools at `/mcp` (streamable HTTP) — `list_suites`, `get_suite_results`, `get_health_score`, `get_adf_pipeline_status`, `trigger_suite_run`, `get_run_status`, `create_check`, `profile_column`. The endpoint is **Azure AD–protected**: present the same bearer token the web UI uses (validated against the same tenant / audience / scope). Without Azure auth configured the endpoint is only mounted in local dev-bypass mode — never unauthenticated in a deployed environment (ADR [0008](docs/adr/0008-mcp-server.md)).

Point any MCP client at `https://<your-dataq-host>/mcp` with an `Authorization: Bearer <token>` header.

**Claude Desktop / Claude.ai** (`claude_desktop_config.json`) — and **GitHub Copilot** (`mcp.json`):

```jsonc
{
  "mcpServers": {
    "dataq": {
      "url": "https://<your-dataq-host>/mcp",
      "headers": { "Authorization": "Bearer <AZURE_AD_ACCESS_TOKEN>" }
    }
  }
}
```

**Cursor** (`~/.cursor/mcp.json`) uses the same `mcpServers` shape. Once configured, all 8 tools are available to natural-language queries (e.g. *"what failed in the orders suite today?"*, *"run the orders suite on DEV"*).

## Documentation

| | |
|---|---|
| **Working agreements + commit/PR conventions** | [CONTRIBUTING.md](CONTRIBUTING.md) |
| **Project guide for AI assistants** | [CLAUDE.md](CLAUDE.md) |
| **Architecture diagram + invariants** | [docs/architecture.md](docs/architecture.md) |
| **Architecture Decision Records** | [docs/adr/](docs/adr/) |
| **Live task tracker** | [docs/progress.md](docs/progress.md) |
| **Product roadmap (8 weeks, 100 tasks)** | [context/DataQ_platform_roadmap.md](context/DataQ_platform_roadmap.md) |
| **Security policy + responsible disclosure** | [SECURITY.md](.github/SECURITY.md) |

## License

MIT — see [LICENSE](LICENSE).
