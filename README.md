# DataQ

> Data quality monitoring built on Great Expectations — checks your tables and files, alerts the team that owns them, and reacts to the pipelines that load them.

**📖 Docs: <https://theurgicduke771.github.io/DataQ/docs/latest/>** · **Status:** `v1.1.0` released 2026-08-21; v1.2 in progress ([changelog](CHANGELOG.md) · [tracker](docs/progress.md)) · MIT licensed

## What it does

- **Checks** — GX expectations (incl. **custom SQL**), **freshness / volume / schema-drift / anomaly** monitors and cross-dataset **comparison**; column profiler and dry-run everywhere. [Feature matrix →](https://theurgicduke771.github.io/DataQ/docs/latest/reference/feature-matrix/)
- **Assets, lineage & incidents** — each table/file rolls up health across suites, with table- and column-level lineage (dbt, OpenLineage, warehouse-native) and open incidents. [Concepts →](https://theurgicduke771.github.io/DataQ/docs/latest/get-started/concepts/)
- **Quality by dimension** — every check is classified (completeness, validity, timeliness, …) so the scorecard shows what *isn't* watched, not just what passes.
- **Run anywhere** — run now, cron schedules (DST-aware), or **pipeline triggers** from ADF, Airflow and dbt. [Orchestration →](https://theurgicduke771.github.io/DataQ/docs/latest/guides/orchestration/)
- **Alerting** — warn/fail/critical tiers → Teams / Slack / email, with dedup, snooze and plain-language failure summaries. [Notifications →](https://theurgicduke771.github.io/DataQ/docs/latest/guides/notifications/)
- **Governance** — workspace roles + per-suite sharing (RBAC), PII-redacted failing samples, audit log, optional LLM assist (SQL generation, check suggestions, RCA) that is off by default.

## Datasources

| Kind | Supported |
|---|---|
| Warehouses / lakehouses | Snowflake (incl. native DMF checks) · Databricks Unity Catalog · Apache Iceberg (native read) |
| SQL databases | PostgreSQL · MySQL / MariaDB · Trino · SQL Server — Azure SQL, Synapse, Fabric SQL ([driver notes](https://theurgicduke771.github.io/DataQ/docs/latest/guides/datasources-checks/)) |
| Files (CSV / Parquet, batch patterns) | ADLS Gen2 · Fabric OneLake · AWS S3 and any S3-compatible store (MinIO, R2, Ceph, …) |
| Orchestration (monitor + trigger, not checked) | Azure Data Factory · Apache Airflow · dbt |

## Quick start

**Run it for a team:** deploy on Azure Container Apps (primary) or AWS ECS Fargate with the reference OpenTofu stacks, the recommended path for production and enterprise teams. See [Production deployment](https://theurgicduke771.github.io/DataQ/docs/latest/operate/deployment/).

**Evaluate in ~5 minutes — Docker only, no cloud account or IdP:**

```bash
curl -O https://raw.githubusercontent.com/TheurgicDuke771/DataQ/main/docker-compose.ghcr.yml
export OPENBAO_TOKEN=$(openssl rand -hex 16)   # root token for the bundled vault
export DATAQ_SIGNIN_EMAIL=you@example.com      # the address allowed to sign in
docker compose -f docker-compose.ghcr.yml up
```

Open **<http://localhost:3000>**, enter that address, and read the 6-digit code at **<http://localhost:8025>** (bundled Mailpit — nothing leaves your machine). The stack starts migrated with demo data; API docs at `http://localhost:8000/docs`. Images are multi-arch (amd64/arm64) and bind to `127.0.0.1`. Credentials live in a bundled OpenBao vault in dev mode, so re-enter them after a restart. Pin a release with `DATAQ_BACKEND_TAG=vX.Y.Z DATAQ_FRONTEND_TAG=vX.Y.Z`.

For **SSO**, run the same frontend image with `DATAQ_AUTH_MODE=oidc` and `DATAQ_AUTH_AUTHORITY` / `DATAQ_AUTH_CLIENT_ID` / `DATAQ_AUTH_API_SCOPE` — any standards-compliant OIDC provider works (Azure AD and AWS Cognito are validated). See [Getting started](https://theurgicduke771.github.io/DataQ/docs/latest/get-started/install/).

**Develop from source:**

```bash
git clone https://github.com/TheurgicDuke771/DataQ.git && cd DataQ
./scripts/setup.sh    # conda env, pre-commit, images, migrations, seed data
conda activate dataq
docker-compose up     # backend :8000 · frontend :3000 · mail :8025
```

## AI assistants (MCP)

DataQ serves **52 curated MCP tools** at `https://<your-dataq-host>/mcp/` — 28 read-only, 18 that change state, and 6 that open a live datasource connection (gated like writes). Keep the **trailing slash** and send `Authorization: Bearer <token>`: your OIDC token under SSO, or a DataQ API key (`dq_live_…`) under email sign-in. No connection credential ever passes through the MCP surface. Client setup for Claude, VS Code / Copilot and Cursor: [MCP setup →](https://theurgicduke771.github.io/DataQ/docs/latest/guides/mcp-setup/)

## Stack & deployment

FastAPI · Celery · Redis · PostgreSQL + Alembic · Great Expectations — React · Vite · Ant Design. Secrets in Azure Key Vault, AWS Secrets Manager or OpenBao. Reference deployments (IaC in [`deploy/terraform/`](deploy/terraform/)): **Azure Container Apps** and **AWS ECS Fargate**, behind provider-neutral seams — see [deployment parity](https://theurgicduke771.github.io/DataQ/docs/latest/operate/deployment-parity/) and the [deploy runbook](deploy/README.md).

## Contributing & reference

[CONTRIBUTING.md](CONTRIBUTING.md) (working agreements) · [Architecture](docs/site/architecture/overview.md) · [ADRs](docs/site/adr/) · [Env-var reference](.env.app.example) · [AI-assistant guide](CLAUDE.md) · [Security policy](.github/SECURITY.md) · [LICENSE](LICENSE) (MIT)
