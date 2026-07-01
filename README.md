# DataQ

> Data quality monitoring platform built around Great Expectations — Snowflake (DEV/QA/UAT), ADLS Gen2, S3, Unity Catalog (Databricks), with ADF + Airflow orchestration integrations.

**📖 Documentation site: <https://theurgicduke771.github.io/DataQ/>** (MkDocs Material — quickstart, concepts, architecture, guides).

**Status:** pre-v1 — Week 7 (deployment, hardening & docs). Weeks 1–6 complete; v1 is **deployed to Azure Container Apps** — API + worker + a runtime-configured **frontend Container App** (the sole public surface; the api runs on internal ingress behind it), with Key Vault, App Insights, and orchestration polling live. Auth is a **generic OIDC client** (validated against Azure AD; ADR [0028](docs/adr/0028-cloud-neutral-image-runtime-config-generic-oidc.md)). Live task-level progress at [docs/progress.md](docs/progress.md).

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · Celery · Great Expectations · SQLAlchemy + Alembic · PostgreSQL · Redis |
| Frontend | React · Vite · Ant Design · generic OIDC (`oidc-client-ts`) |
| Auth / secrets | OIDC — Azure AD validated (`AUTH_*` contract, provider-neutral) · Azure Key Vault |
| Hosting | Azure Container Apps (API · worker · frontend) + Application Insights (deployed) |
| AI integration | FastMCP — 8 curated MCP tools at `/mcp` for Claude Desktop / Copilot / Cursor |

## Quick start

### Run DataQ — prebuilt images (recommended)

Evaluate or self-host in ~5 minutes: **no source checkout, no Azure tenant.** Just Docker.

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/main/docker-compose.ghcr.yml
docker compose -f docker-compose.ghcr.yml up
```

Open **<http://localhost:3000>** — the stack comes up migrated and seeded with demo data, on **dev-bypass auth** (every request is a fixed demo user; no sign-in). API + Swagger at `http://localhost:8000/docs`. Images are pulled from GHCR and are **multi-arch** (amd64 + arm64), so Apple-Silicon runs native. Ports bind to `127.0.0.1` only. To pin a release instead of the moving tags: `DATAQ_BACKEND_TAG=vX.Y.Z DATAQ_FRONTEND_TAG=vX.Y.Z docker compose -f docker-compose.ghcr.yml up`.

> Self-hosting with **your own** Azure AD? The published frontend is **one generic image** — the compose eval runs it with `DATAQ_AUTH_MODE=bypass` (auth off). For real SSO, **no rebuild**: run that same image with `DATAQ_AUTH_MODE=oidc` + `DATAQ_AUTH_AUTHORITY` / `DATAQ_AUTH_CLIENT_ID` / `DATAQ_AUTH_API_SCOPE` (auth config is injected at runtime, ADR 0028), and run the backend with `AUTH_DEV_BYPASS` off. See [Getting started](https://theurgicduke771.github.io/DataQ/getting-started/).

### Develop DataQ — from source

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
| **Documentation site (user guides)** | <https://theurgicduke771.github.io/DataQ/> · source in [docs/](docs/), built by [.github/workflows/docs.yml](.github/workflows/docs.yml) |
| **Deployment guide + env-var reference** | [deploy/README.md](deploy/README.md) · [.env.app.example](.env.app.example) |
| **Project guide for AI assistants** | [CLAUDE.md](CLAUDE.md) |
| **Architecture diagram + invariants** | [docs/architecture.md](docs/architecture.md) |
| **Architecture Decision Records** | [docs/adr/](docs/adr/) |
| **Live task tracker** | [docs/progress.md](docs/progress.md) |
| **Product roadmap (8 weeks, 100 tasks)** | [context/DataQ_platform_roadmap.md](context/DataQ_platform_roadmap.md) |
| **Security policy + responsible disclosure** | [SECURITY.md](.github/SECURITY.md) |

## License

MIT — see [LICENSE](LICENSE).
