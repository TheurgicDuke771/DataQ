# DataQ

**DataQ is a data-quality monitoring platform.** It runs automated checks against your
data — in Snowflake, ADLS Gen2, AWS S3, and Databricks Unity Catalog — tells you when
something is wrong (failed checks, stale tables, unexpected row counts), and alerts your
team. It watches your Azure Data Factory, Airflow, and dbt pipelines and can run checks
automatically when a pipeline finishes.

## Who it's for

- **Data engineers / SREs** — author checks, wire up pipelines, triage failures.
- **QA / analysts** — see what passed or failed and why.
- **Product & stakeholders** — a health score and trend at a glance.

## Quickstart (5 minutes)

### Run DataQ — prebuilt images (recommended)

Evaluate or self-host with **no source checkout and no Azure tenant** — just Docker:

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/main/docker-compose.ghcr.yml
docker compose -f docker-compose.ghcr.yml up
```

Open **`http://localhost:3000`**. The stack comes up migrated and seeded with demo
data, on **dev-bypass auth** (every request is a fixed demo user — no sign-in). API +
Swagger at `http://localhost:8000/docs`. The GHCR images are **multi-arch** (amd64 +
arm64, native on Apple Silicon) and all ports bind to `127.0.0.1` only.

### Develop DataQ — from source

```bash
git clone https://github.com/TheurgicDuke771/DataQ.git
cd DataQ
./scripts/setup.sh        # conda env + pre-commit + docker-compose + migrations
conda activate dataq
docker-compose up         # Postgres + Redis + FastAPI + React + Celery worker
```

- Backend API: `http://localhost:8000` (interactive docs at `/docs` in dev).
- Frontend: `http://localhost:3000`.

Then open the UI, add a connection, create a suite of checks, and run it. See
**[Getting started](getting-started.md)** for both paths in depth (incl. self-hosting
with your own Azure AD) and **[Datasources & checks](datasources-checks.md)** to author
your first check.

## Where to go next

- New to DataQ? Read **[Concepts](concepts.md)** (datasource vs orchestration is the one
  distinction to internalise).
- Want the big picture? **[Architecture](architecture.md)**.
- Running it for real? **[Deploying](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md)**
  and **[Observability & troubleshooting](observability.md)**.
- AI assistants (Claude / Copilot / Cursor) can drive DataQ over MCP — see the
  [MCP section in the README](https://github.com/TheurgicDuke771/DataQ#mcp-ai-assistant-access).
