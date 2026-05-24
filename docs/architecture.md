# DataQ — System Architecture

> Keep this diagram in sync with the code. When a new component, datasource, or integration is added, update the diagram in the same PR.

```mermaid
flowchart TB
    subgraph clients["Clients"]
        Browser["Browser\nweb UI"]
        AI["AI clients\nClaude · Copilot · Cursor"]
    end

    subgraph platform["DataQ platform — Azure Container Apps · Static Web App"]
        React["React + Vite\nStatic Web App"]
        FastAPI["FastAPI\nREST · /mcp endpoint"]
        Celery["Celery worker\nGX execution"]
        PG[("PostgreSQL\nresults · state")]
        Redis[("Redis\ntask queue")]
        KV["Key Vault\nsecrets"]
        AppIns["App Insights\nobservability"]
    end

    subgraph datasources["Datasources"]
        SF["Snowflake\nDEV · QA · UAT"]
        ADLS["ADLS Gen2 · S3\nflat files"]
        UC["Unity Catalog\nDatabricks"]
    end

    subgraph integrations["Integrations"]
        Orch["ADF · Airflow\npipeline · DAG events"]
        Monitor["Azure Monitor\nalert rule · webhook"]
        Teams["MS Teams\nnotifications"]
    end

    Browser      -->|HTTPS|                              React
    AI           -->|MCP · HTTP|                         FastAPI
    React        -->                                     FastAPI
    FastAPI      <-->                                    PG
    FastAPI      <-->                                    Redis
    FastAPI      -->                                     KV
    FastAPI      -->                                     AppIns
    FastAPI      -->                                     Celery
    Celery       <-->                                    PG
    Celery       <-->                                    Redis
    Celery       -->|GX checks|                          SF
    Celery       -->|GX checks|                          ADLS
    Celery       -->|GX checks|                          UC
    Orch         -->                                     Monitor
    Monitor      -->|POST /orchestration/events/{provider}| FastAPI
    FastAPI      -->|alerts|                             Teams

    classDef platform  fill:#E6F1FB,stroke:#185FA5,color:#0C449C
    classDef infra     fill:#EEEDF8,stroke:#534AB7,color:#3C3489
    classDef source    fill:#E1F5EE,stroke:#0F6E56,color:#085041
    classDef integ     fill:#FAEEda,stroke:#854F0B,color:#633806
    classDef notify    fill:#FAEce7,stroke:#993C1D,color:#712B13
    classDef client    fill:#F1EFE8,stroke:#5F5E5A,color:#444441

    class React,FastAPI,Celery platform
    class PG,Redis,KV,AppIns infra
    class SF,ADLS,UC source
    class Orch,Monitor integ
    class Teams notify
    class Browser,AI client
```

## Legend

| Colour | Group |
|---|---|
| Blue | DataQ services (React, FastAPI, Celery) |
| Purple | Azure platform infrastructure (PostgreSQL, Redis, Key Vault, App Insights) |
| Green | Datasources — GX checks run against these |
| Orange | Orchestration integrations — monitor + trigger only, never datasources |
| Brown/red | Notification channel |

## Key invariants

- **Orchestration providers (ADF · Airflow) are not datasources.** They live in `pipeline_runs`, not `runs`. Trigger bindings map `(provider, pipeline_id, env) → suite_id`.
- **GX execution is Celery-only.** FastAPI never calls GX directly; it enqueues tasks.
- **All secrets via Key Vault.** No credentials in env vars or code.
