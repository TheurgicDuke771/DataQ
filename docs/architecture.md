# DataQ — System Architecture

> Keep these diagrams in sync with the code. When a new component, datasource, integration, or DB table is added, update the relevant diagram in the same PR.
>
> ⚠️ **Mermaid gotcha — syntactically *valid* ≠ *renders correctly*.** In **sequence-diagram** text, `#` starts Mermaid's HTML-entity escape (`#35;` → `#`) and a stray `;` terminates a statement — a bare `#NNN` issue ref or an inline semicolon silently truncates the rendered line while the syntax check still passes. Write issue refs in sequence diagrams as `#35;NNN` (renders as `#NNN`; flowcharts/state/ER diagrams don't need this) and eyeball the rendered diagram before merging, not just the linter.

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
    subgraph platform["🟦 DataQ Platform · Azure Container Apps"]
        Frontend["Frontend · nginx + React SPA<br/>sole public ingress · runtime OIDC config"]
        API["FastAPI · internal ingress<br/>REST /api/v1 + /mcp · OIDC JWT"]
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

    Web -->|HTTPS| Frontend
    AICli -->|MCP · HTTP| Frontend
    orch -->|"webhook / HMAC callback<br/>+ 10-min REST poll fallback"| Frontend
    Frontend -->|"same-origin proxy<br/>/api + /mcp + /healthz"| API
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
    class API,Worker,Infra,Frontend hub
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
| Blue | DataQ platform — Frontend (nginx SPA) · FastAPI · Celery worker · PostgreSQL · Redis · Key Vault · App Insights |
| Green | Datasources — GX checks run against these |
| Red | Alert channels — Teams · Slack · Email (the `ResultPublisher` seam) |

## Data model (ER diagram)

> Source of truth: [`backend/app/db/models.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/db/models.py) (13 tables). Update this diagram in the same PR as any model/migration change.

```mermaid
erDiagram
    users {
        uuid id PK
        string aad_object_id UK
        string email
        string display_name
        timestamptz last_seen_at
    }
    connections {
        uuid id PK
        string name "unique per env"
        string type "snowflake / adls_gen2 / s3 / unity_catalog / adf / airflow"
        string env "dev / qa / uat / prod"
        jsonb config "non-secret datasource config"
        string secret_ref "SecretStore key, never the credential"
        uuid created_by FK
    }
    connection_versions {
        uuid id PK
        uuid connection_id FK
        int version_no "per-connection sequence"
        string name
        string type
        string env
        jsonb config
        uuid changed_by FK "SET NULL"
    }
    suites {
        uuid id PK
        string name
        string description
        uuid connection_id FK
        jsonb target "table / path / UC name the checks run against"
        jsonb column_policy "failing-sample redaction policy"
        uuid created_by FK
    }
    checks {
        uuid id PK
        uuid suite_id FK
        string name
        string kind "expectation (v1) / freshness / volume / ..."
        string expectation_type
        numeric warn_threshold
        numeric fail_threshold
        numeric critical_threshold
        jsonb config "GX expectation kwargs"
        timestamptz alert_snoozed_until
    }
    check_versions {
        uuid id PK
        uuid check_id FK
        int version_no "per-check sequence"
        string name
        string kind
        string expectation_type
        jsonb config
        numeric warn_threshold
        numeric fail_threshold
        numeric critical_threshold
        uuid changed_by FK "SET NULL"
    }
    runs {
        uuid id PK
        uuid suite_id FK
        string status "queued / running / succeeded / failed / cancelled"
        string triggered_by "manual / schedule / provider:pipeline:run_id"
        string celery_task_id
        timestamptz started_at
        timestamptz finished_at
    }
    results {
        uuid id PK
        uuid run_id FK
        uuid check_id FK
        string status "pass warn fail critical + skip error"
        numeric metric_value "SQL-aggregatable scalar (ADR 0012)"
        int duration_ms
        jsonb observed_value
        jsonb expected_value
        jsonb sample_failures "redacted failing rows"
        timestamptz sample_failures_purged_at
    }
    shares {
        uuid id PK
        uuid suite_id FK
        uuid user_id FK
        string permission "view / edit / admin"
    }
    pipeline_runs {
        uuid id PK
        string provider "adf / airflow"
        uuid connection_id FK
        string provider_run_id "unique per provider"
        string pipeline_or_dag_id
        string env
        string status
        timestamptz started_at
        timestamptz finished_at
        string failure_reason
    }
    trigger_bindings {
        uuid id PK
        string provider
        string pipeline_or_dag_id
        string env
        uuid suite_id FK
        bool enabled
    }
    schedules {
        uuid id PK
        uuid suite_id FK
        string cron "5-field, evaluated in timezone"
        string timezone "IANA name, default UTC"
        bool enabled
        timestamptz next_run_at "precomputed next fire"
        timestamptz last_run_at
        uuid created_by FK
    }
    suite_notifications {
        uuid id PK
        uuid suite_id FK "unique - one row per suite"
        bool enabled
        string alert_on "fail / warn / always"
        string webhook_secret_ref "per-suite Teams webhook, SecretStore key"
    }

    users ||--o{ connections : "created_by"
    users ||--o{ suites : "created_by"
    users ||--o{ schedules : "created_by"
    users ||--o{ shares : "grantee (CASCADE)"
    users |o--o{ connection_versions : "changed_by (SET NULL)"
    users |o--o{ check_versions : "changed_by (SET NULL)"

    connections ||--o{ connection_versions : "config history (CASCADE)"
    connections ||--o{ suites : "datasource for"
    connections ||--o{ pipeline_runs : "orchestrator connection"

    suites ||--o{ checks : "contains (CASCADE)"
    suites ||--o{ runs : "executed as (CASCADE)"
    suites ||--o{ shares : "shared via (CASCADE)"
    suites ||--o{ trigger_bindings : "triggered by (CASCADE)"
    suites ||--o{ schedules : "scheduled by (CASCADE)"
    suites ||--o| suite_notifications : "alert config (CASCADE)"

    checks ||--o{ check_versions : "config history (CASCADE)"
    checks ||--o{ results : "evaluated as (CASCADE)"
    runs ||--o{ results : "produces (CASCADE)"

    pipeline_runs ||..o{ runs : "triggered_by marker (no FK)"
    trigger_bindings }o..o{ pipeline_runs : "(provider, pipeline, env) match (no FK)"
```

### Reading notes

- **Conventions (elided from the diagram for noise):** every table has a `gen_random_uuid()` UUID PK and `created_at`; mutable entities also carry `updated_at`. Status/type columns are `TEXT` + `CHECK` constraints, **not** native PG enums (migration ergonomics).
- **Cascade posture (ADR [0020](adr/0020-history-and-audit-strategy.md)):** deleting a suite cascades its checks, runs, results, shares, trigger bindings, schedules, and notification config; deleting a connection cascades its version history. History is not retained past entity deletion — accepted. Version snapshots survive their *author* (`changed_by` is `SET NULL`), not their entity.
- **`pipeline_runs` ≠ `runs` — no FK between them.** Orchestrator pipeline executions correlate to the DQ suite runs they trigger only via the string marker `runs.triggered_by = '<provider>:<pipeline_or_dag_id>:<provider_run_id>'` (dotted lines above); `trigger_bindings` matches pipeline runs by `(provider, pipeline_or_dag_id, env)`, also without an FK. A partial unique index on `runs (suite_id, triggered_by)` dedupes orchestration-triggered runs.
- **Singleton constraints:** at most one orchestrator connection per `(type, env)` (partial unique index over `adf`/`airflow` only — datasources may repeat); one `suite_notifications` row per suite; one live `shares` row per `(suite, user)`.
- **Secrets are never in these tables.** `connections.secret_ref` / `suite_notifications.webhook_secret_ref` hold SecretStore *keys*; version snapshots deliberately omit credentials, so a credential rotation records no version.

## Runtime flows

The diagrams above show *structure* (who talks to whom, what is stored); these two show *ordering* for the flows that cross the most components.

### Suite run lifecycle

Every run — manual (`POST /suites/{id}/run`), scheduled (the 60s beat dispatcher), or orchestration-triggered — converges on the same path once the `Run` row exists:

```mermaid
sequenceDiagram
    autonumber
    participant Trig as Trigger<br/>(manual API · schedule beat · pipeline event)
    participant API as FastAPI / beat dispatcher
    participant PG as PostgreSQL
    participant BR as Redis broker
    participant W as Celery worker (run_suite)
    participant KV as SecretStore (Key Vault)
    participant DS as Datasource
    participant Pub as ResultPublisher (Teams · Slack · email)

    Trig->>API: trigger suite run
    API->>PG: INSERT runs (status=queued, triggered_by marker)
    API->>BR: send_task run_suite(run_id)
    Note over API,BR: broker down → run marked terminal failed,<br/>never left stuck queued (#35;227)
    API->>PG: store celery_task_id (enables cancel/revoke)

    BR->>W: deliver task
    W->>PG: load run — already cancelled? stop (cooperative cancel)
    W->>PG: load suite + connection + checks, resolve target (#35;215)
    W->>KV: get connection credential (secret_ref)
    W->>W: build CheckRunner by connection.type (registry, ADR 0011)
    Note over W,KV: any setup failure → run terminal failed
    W->>DS: materialize flat-file batch path
    Note over W,DS: batch absent → every check skip,<br/>run still succeeds (#35;122)
    W->>PG: UPDATE runs SET status=running
    W->>DS: execute by check.kind (ADR 0012) —<br/>expectation → GX validate · freshness/volume → monitor SQL
    DS-->>W: one CheckOutcome per check
    W->>W: severity.resolve_status — band unexpected-% against<br/>warn/fail/critical thresholds (ADR 0005/0016)
    W->>PG: INSERT results (status, metric_value, redacted sample_failures)
    W->>PG: UPDATE runs SET status=succeeded<br/>(failed = the adapter raised)
    W->>Pub: publish_run_outcome — snooze suppression → alert_on routing → dedup → publish
    Note over W,Pub: best-effort — a publish failure never<br/>affects the persisted run
```

Key sources: [`worker/tasks.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/worker/tasks.py) (`run_suite`), [`services/run_service.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/services/run_service.py) (`execute_run`), [`services/run_dispatch.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/services/run_dispatch.py), [`services/severity.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/services/severity.py), [`alerting/dispatch.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/alerting/dispatch.py).

### Orchestration event flow

How an ADF / Airflow pipeline outcome becomes (at most) a triggered suite run. Everything goes through the `OrchestrationProvider` seam (ADR [0004](adr/0004-orchestration-abstraction.md)) — no provider branching:

```mermaid
sequenceDiagram
    autonumber
    participant ORC as ADF / Airflow
    participant FE as Frontend nginx (public ingress)
    participant API as FastAPI (internal)
    participant KV as SecretStore (Key Vault)
    participant PG as PostgreSQL
    participant BR as Redis broker
    participant W as Celery worker

    Note over ORC: pipeline / DAG run reaches a terminal state
    ORC->>FE: POST /api/v1/orchestration/events/{provider}<br/>(ADF — Azure Monitor alert · Airflow — on_*_callback snippet)
    FE->>API: same-origin proxy
    API->>KV: load receiver secret
    API->>API: authenticate — ADF constant-time token query param (ADR 0006)<br/>· Airflow HMAC-SHA256 over raw body (ADR 0007)
    API->>API: OrchestrationProvider.parse → RunUpdate or AlertPing

    alt AlertPing — run-anonymous Azure Monitor alert (#35;492)
        API->>BR: enqueue targeted poll-now (provider + resource)
        API-->>ORC: 202 reconciling
    else RunUpdate
        API->>PG: upsert pipeline_runs ON (provider, provider_run_id)
        alt status = succeeded
            API->>PG: match enabled trigger_bindings (provider, pipeline_or_dag_id, env)
            API->>PG: INSERT runs, triggered_by = provider:pipeline:run_id<br/>ON CONFLICT DO NOTHING (dedup index, #35;308)
            API->>BR: dispatch run_suite per triggered run
        else status = failed
            API->>API: alert the user only — failures never trigger suites
        end
        API-->>ORC: 200 recorded
    end

    Note over W,PG: polling fallback (#35;171) — a 10-min beat sweeps every orchestrator connection<br/>via the provider REST API (list_recent_runs, 15-min lookback) into the SAME<br/>upsert + trigger path — a 30-min gap-recovery sweep (1-hour window) covers downtime
```

Key sources: [`api/v1/orchestration.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/api/v1/orchestration.py), [`services/orchestration_service.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/services/orchestration_service.py), [`worker/tasks.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/worker/tasks.py) (`poll_orchestration_runs`, `recover_orchestration_gaps`).

## Status semantics

### Run lifecycle

`runs.status` describes **execution, not data quality** — a run whose checks all failed is still `succeeded`.

```mermaid
stateDiagram-v2
    [*] --> queued : created (manual · schedule · trigger)
    queued --> running : worker picks up
    queued --> cancelled : user cancel
    queued --> failed : dispatch/setup failure · reaper (#309)
    running --> succeeded : execution completed
    running --> cancelled : user cancel
    running --> failed : adapter raised · reaper (#309)
    succeeded --> [*]
    failed --> [*]
    cancelled --> [*]
```

- **`succeeded` means executed** — checks may still have failed; the data-quality outcome lives in `results.status`.
- **Cancel works on any non-terminal run:** the API sets `cancelled` and best-effort revokes the Celery task; the worker also honours the status cooperatively (start-check before executing).
- **The reaper (#309)** drives runs orphaned in `queued`/`running` past a threshold (task never published, or the worker died mid-run) to terminal `failed`.

### Result status derivation

`results.status` has two orthogonal families: the four **severity tiers** (ADR [0005](adr/0005-severity-tier-weights.md)) and the two **operational statuses** (#122). Only the tiers carry health-score weight (0.5 / 1.0 / 2.0 for warn / fail / critical); `skip`/`error` **must be excluded from the health-score denominator**.

```mermaid
flowchart TD
    O["CheckOutcome (from the runner)"] --> E{"runner could evaluate it?"}
    E -- "no — evaluation raised" --> ERR["error — operational (#122)<br/>no tier · no metric · error message in observed_value"]
    E -- yes --> S{"flat-file batch landed?<br/>(decided before execution)"}
    S -- no --> SKIP["skip — operational (#122)<br/>not evaluated at all"]
    S -- yes --> M{"thresholds set AND a<br/>bandable metric_value exists?"}
    M -- "no — binary fallback (ADR 0005)" --> BIN{"GX success?"}
    BIN -- yes --> PASS[pass]
    BIN -- no --> FAIL[fail]
    M -- yes --> BAND["band unexpected-% (0-100, higher = worse)<br/>against warn / fail / critical thresholds (ADR 0016)"]
    BAND --> TIER["pass / warn / fail / critical<br/>thresholds are policy — they OVERRIDE GX success"]

    classDef op fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    classDef tier fill:#E6F1FB,stroke:#185FA5,color:#0C449C
    class ERR,SKIP op
    class PASS,FAIL,TIER tier
```

The single decision lives in [`services/severity.py`](https://github.com/TheurgicDuke771/DataQ/blob/main/backend/app/services/severity.py) (`resolve_status`), shared by run persistence and the check-editor dry-run so a preview can never disagree with the run it previews.

## Trust boundaries & authentication

The one-diagram consolidation of ADRs [0006](adr/0006-adf-webhook-authentication.md) / [0007](adr/0007-airflow-callback-model.md) / [0008](adr/0008-mcp-server.md) / [0028](adr/0028-cloud-neutral-image-runtime-config-generic-oidc.md) — what crosses each boundary and what credential it carries:

```mermaid
flowchart LR
    subgraph internet["🌍 Untrusted — public internet"]
        B["Browser (React SPA)"]
        AI["AI clients (MCP)"]
        WH["ADF / Airflow webhooks"]
    end
    IDP["🔑 OIDC authority<br/>(Azure AD behind the generic DATAQ_AUTH_* contract)"]
    subgraph aca["🟦 Trust boundary — Container Apps env"]
        FE["Frontend nginx — SOLE public ingress (TLS)<br/>serves the SPA + runtime auth config (non-secret)"]
        API["FastAPI — internal ingress only<br/>validates EVERY request itself (no EasyAuth)"]
        WK["Celery worker — no ingress"]
    end
    subgraph backing["🔒 Credentialed backing services"]
        KV["Key Vault (all connection + webhook secrets)"]
        PG[("PostgreSQL — dataq db,<br/>least-priv dataq_app role")]
        RD[("Redis — password auth")]
        APPI["App Insights (PII-redacted logs + traces)"]
    end
    subgraph egress["🌐 Outbound — credentials fetched from Key Vault per use"]
        DS["Datasources — Snowflake · ADLS · S3 · Unity Catalog"]
        AL["Teams / Slack webhooks · SMTP"]
        OAPI["ADF / Airflow REST APIs (polling)"]
    end

    B -- "OIDC auth-code + PKCE" --> IDP
    AI -- "same bearer token" --> IDP
    B -- "HTTPS · bearer JWT on /api" --> FE
    AI -- "bearer JWT on /mcp" --> FE
    WH -- "ADF: shared-secret token in URL (ADR 0006)<br/>Airflow: HMAC-SHA256 body signature (ADR 0007)" --> FE
    FE -- "same-origin proxy /api · /mcp · /healthz (HTTP/1.1)" --> API
    API -- "JWT validated (fastapi-azure-auth / MCP JWTVerifier)<br/>+ per-suite authz + sample redaction" --> API
    API -- "enqueue tasks" --> RD
    RD -- "deliver tasks" --> WK
    API -- "UAMI — no stored credential" --> KV
    WK -- "UAMI — no stored credential" --> KV
    API --> PG
    WK --> PG
    API --> APPI
    WK --> APPI
    WK -- "GX checks / monitor SQL" --> DS
    WK -- "run-outcome alerts" --> AL
    WK -- "10-min poll fallback" --> OAPI

    classDef hub fill:#E6F1FB,stroke:#185FA5,color:#0C449C
    classDef ext fill:#F1EFE8,stroke:#5F5E5A,color:#444441
    classDef sec fill:#FAEEDA,stroke:#854F0B,color:#633806
    classDef out fill:#E1F5EE,stroke:#0F6E56,color:#085041
    class FE,API,WK hub
    class B,AI,WH,IDP ext
    class KV,PG,RD,APPI sec
    class DS,AL,OAPI out
    style internet fill:#F7F6F2,stroke:#5F5E5A
    style aca fill:#F4F9FE,stroke:#185FA5
    style backing fill:#FDF7EC,stroke:#854F0B
    style egress fill:#F0FBF7,stroke:#0F6E56
```

Boundary notes:

- **Defense in depth, not perimeter trust:** the API validates every request's bearer JWT itself (`fastapi-azure-auth` for REST, `JWTVerifier` for MCP — same tenant/audience/scope) even though it is only reachable through the frontend proxy. Platform-level auth (SWA EasyAuth) is explicitly disabled (#511).
- **The only endpoints that bypass user JWT auth** are the two orchestration webhook receivers (each with its own secret scheme, above) and the health probe. Webhook secrets live in Key Vault and are compared constant-time; they are never logged.
- **Nothing secret is baked into images or served to the browser.** The frontend's runtime `DATAQ_AUTH_*` config is non-secret OIDC metadata (ADR 0028); all real secrets resolve at use-time from Key Vault via user-assigned managed identity.
- **MCP is fail-closed:** without resolvable auth config the `/mcp` mount does not come up at all (ADR 0008).

## Key invariants

- **The frontend Container App is the sole public surface** (ADR [0028](adr/0028-cloud-neutral-image-runtime-config-generic-oidc.md) §5). It's one generic nginx image whose auth is injected at **runtime** (`DATAQ_AUTH_*` → generic OIDC, validated against Azure AD — no MSAL, nothing cloud-specific baked in), and it reverse-proxies `/api` + `/mcp` + `/healthz` same-origin to the **internal-ingress** API. The API is not reachable directly from the internet; external orchestrator webhooks land on the frontend and are proxied through.
- **Orchestration providers (ADF · Airflow) are not datasources.** They live in `pipeline_runs`, not `runs`. Trigger bindings map `(provider, pipeline_id, env) → suite_id`.
- **Scheduled/triggered suite runs are Celery-only.** FastAPI never enqueues GX itself for a full suite run; it dispatches a task. **Exception — synchronous preview paths:** the check dry-run (`POST /suites/{id}/checks/dryrun`) and the column profiler (`POST /suites/{id}/profile`) run a single GX check / a profiling query against the datasource **synchronously in a threadpool** (persisting nothing) — interactive authoring aids, not scheduled runs.
- **All connection secrets via Key Vault in production / staging.** Local dev may resolve secrets via `KV_SECRET_*` env vars through the `EnvSecretStore` backend (see [ADR 0009](adr/0009-flat-monorepo-layout.md) layout note and `backend/app/core/secrets.py`). No credentials are ever hardcoded.
- **The `/mcp` endpoint exposes the same service layer to AI clients.** The 8 FastMCP tools are thin wrappers reusing the same services + per-suite authz + sample redaction as the REST API — no logic duplication. Validated with the same Azure AD bearer token (a `JWTVerifier` on the same tenant/audience/scope), and **fail-closed** (not mounted without resolvable auth). See [ADR 0008](adr/0008-mcp-server.md).
- **Interactive API docs are off in production.** `/docs`, `/redoc`, and `/openapi.json` are disabled when `ENVIRONMENT=prod` (the prod-docs gate); available in dev/staging.
