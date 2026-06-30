# DataQ — System Architecture

> Keep this diagram in sync with the code. When a new component, datasource, or integration is added, update the diagram in the same PR.

```mermaid
%%{init: {'flowchart': {'curve': 'linear'}}}%%
flowchart LR
    subgraph orch["⚙️ Orchestration — monitor + trigger only"]
        ADF["Azure Data Factory"]
        AF["Apache Airflow"]
    end
    subgraph clients["🌐 Clients"]
        AICli["AI clients · Claude/Copilot/Cursor"]
        Web["Web UI · React SPA"]
    end
    subgraph platform["🟦 DataQ Platform · Azure Container Apps + SWA"]
        API["FastAPI<br/>REST /api/v1 + /mcp · Azure AD JWT"]
        Worker["Celery worker<br/>GX Core execution"]
        Infra["PostgreSQL · Redis · Key Vault · App Insights<br/>suites·runs·results·pipeline_runs · queue · secrets · traces"]
    end
    subgraph alerts["🔔 Alerts · ResultPublisher seam"]
        Teams["Teams · Slack"]
        Email["Email · SMTP"]
    end
    subgraph ds["📊 Datasources — GX checks execute here"]
        SF["Snowflake · DEV/QA/UAT"]
        Files["ADLS Gen2 · S3 · flat files"]
        UC["Unity Catalog · Databricks"]
    end

    Web -->|HTTPS| API
    AICli -->|MCP · HTTP| API
    orch -->|"webhook / HMAC callback<br/>+ 10-min REST poll fallback"| API
    API -->|enqueue run| Worker
    API -.-> Infra
    Worker -.-> Infra
    Worker -->|run outcome| alerts
    Worker -->|GX checks| ds

    classDef hub fill:#E6F1FB,stroke:#185FA5,color:#0C449C
    classDef src fill:#E1F5EE,stroke:#0F6E56,color:#085041
    classDef integ fill:#FAEEDA,stroke:#854F0B,color:#633806
    classDef notify fill:#FAECE7,stroke:#993C1D,color:#712B13
    classDef client fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    class API,Worker,Infra hub
    class SF,Files,UC src
    class ADF,AF integ
    class Teams,Email notify
    class Web,AICli client
    style platform fill:#F4F9FE,stroke:#185FA5
    style ds fill:#F0FBF7,stroke:#0F6E56
    style orch fill:#FDF7EC,stroke:#854F0B
    style alerts fill:#FDF3EF,stroke:#993C1D
    style clients fill:#F7F6F2,stroke:#5F5E5A
    linkStyle default stroke:#5B6B7B,stroke-width:1.5px
```

The flow reads left → right: **inputs** (Clients, Orchestration) drive the **DataQ platform** in the centre, which acts on its **targets** on the right (runs GX checks against datasources, publishes outcomes to alert channels).

## Legend

| Colour | Group |
|---|---|
| Grey | Clients — browser web UI + AI clients (over MCP) |
| Orange | Orchestration — ADF · Airflow (monitor + trigger only, **never** datasources) |
| Blue | DataQ platform — FastAPI · Celery worker · PostgreSQL · Redis · Key Vault · App Insights |
| Green | Datasources — GX checks run against these |
| Red | Alert channels — Teams · Slack · Email (the `ResultPublisher` seam) |

## Key invariants

- **Orchestration providers (ADF · Airflow) are not datasources.** They live in `pipeline_runs`, not `runs`. Trigger bindings map `(provider, pipeline_id, env) → suite_id`.
- **Scheduled/triggered suite runs are Celery-only.** FastAPI never enqueues GX itself for a full suite run; it dispatches a task. **Exception — synchronous preview paths:** the check dry-run (`POST /suites/{id}/checks/dryrun`) and the column profiler (`POST /suites/{id}/profile`) run a single GX check / a profiling query against the datasource **synchronously in a threadpool** (persisting nothing) — interactive authoring aids, not scheduled runs.
- **All connection secrets via Key Vault in production / staging.** Local dev may resolve secrets via `KV_SECRET_*` env vars through the `EnvSecretStore` backend (see [ADR 0009](adr/0009-flat-monorepo-layout.md) layout note and `backend/app/core/secrets.py`). No credentials are ever hardcoded.
- **The `/mcp` endpoint exposes the same service layer to AI clients.** The 8 FastMCP tools are thin wrappers reusing the same services + per-suite authz + sample redaction as the REST API — no logic duplication. Validated with the same Azure AD bearer token (a `JWTVerifier` on the same tenant/audience/scope), and **fail-closed** (not mounted without resolvable auth). See [ADR 0008](adr/0008-mcp-server.md).
- **Interactive API docs are off in production.** `/docs`, `/redoc`, and `/openapi.json` are disabled when `ENVIRONMENT=prod` (the prod-docs gate); available in dev/staging.
