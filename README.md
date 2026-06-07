# DataQ

> Data quality monitoring platform built around Great Expectations.
> v1 evolution of SnowQ — Snowflake (DEV/QA/UAT), ADLS Gen2, S3, Unity Catalog (Databricks), with ADF + Airflow orchestration integrations.

**Status:** pre-v1 — Week 4 in progress (execution backend). Weeks 1–3 complete. Live task-level progress at [docs/progress.md](docs/progress.md).

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · Celery · Great Expectations · SQLAlchemy + Alembic · PostgreSQL · Redis |
| Frontend | React · Vite · Ant Design · MSAL React |
| Auth / secrets | Azure AD (MSAL) · Azure Key Vault |
| Hosting (planned) | Azure Container Apps + Static Web App + Application Insights |
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
