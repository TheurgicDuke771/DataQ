# DataQ

**DataQ is a data-quality monitoring platform.** It runs automated checks against your
data — in Snowflake, ADLS Gen2, AWS S3 (or any S3-compatible store), Databricks Unity Catalog, and Apache Iceberg — tells you when
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
export OPENBAO_TOKEN=$(openssl rand -hex 16)     # root token for the bundled vault
export DATAQ_SIGNIN_EMAIL=you@example.com        # the address allowed to sign in
docker compose -f docker-compose.ghcr.yml up
```

Open **`http://localhost:3000`**, type the address you exported, and read the 6-digit
sign-in code in the bundled inbox at **`http://localhost:8025`** ([Mailpit](https://mailpit.axllent.org)
— no real SMTP relay needed, nothing leaves the host). The stack comes up migrated and
seeded with demo data. API + Swagger at `http://localhost:8000/docs`. The GHCR images
are **multi-arch** (amd64 + arm64, native on Apple Silicon) and all ports bind to
`127.0.0.1` only. To skip sign-in entirely (dev-bypass, a deliberate downgrade — not
the default): `DATAQ_SIGNIN_EMAIL= DATAQ_AUTH_MODE=bypass docker compose -f docker-compose.ghcr.yml up`.
See [Getting started](getting-started.md) for the full flow.

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

- Just want to use it? Follow the **[Tutorial — your first suite](tutorial.md)** end to end.
- New to DataQ? Read **[Concepts](concepts.md)** (datasource vs orchestration is the one
  distinction to internalise), then browse **[Features](features.md)**.
- Setting it up the right way? **[Recommended usage](recommended-usage.md)**.
- Want the big picture? **[Architecture](architecture.md)** · **[Security & data handling](security.md)**.
- Running it for real? **[Deployment](deployment.md)** · **[Troubleshooting](troubleshooting.md)** · **[Observability](observability.md)**.
- Scripting it? The **[REST API](rest-api.md)**. AI assistants (Claude / Copilot / Cursor) can
  drive DataQ over **[MCP](mcp-setup.md)**.
