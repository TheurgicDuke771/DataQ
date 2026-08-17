# Deployment parity — Azure · AWS · Local

How the three reference installations compare, as of 2026-08-16 (both clouds deployed from
the same commit). The design intent (ADR
[0010](https://github.com/TheurgicDuke771/DataQ/blob/main/docs/adr/0010-provider-agnostic-infrastructure-seams.md) /
[0028](https://github.com/TheurgicDuke771/DataQ/blob/main/docs/adr/0028-cloud-neutral-frontend-and-auth.md))
is that **every difference lives in deploy-time configuration — there are zero
cloud-conditional code paths**, and the same container images run everywhere.

## Hosting

| Concern | Azure (primary reference) | AWS (second reference) | Local (compose) |
|---|---|---|---|
| Compute | Container Apps: api / worker / frontend + migrate **job** | ECS Fargate: same three services + migrate **run-task** | docker-compose services |
| Public surface | Frontend Container App (sole public ingress; api internal-only) | CloudFront → ALB → frontend (the ALB security group admits only the CloudFront managed prefix list; nginx re-verifies an origin-secret header) | `localhost` |
| Images | One generic GHCR image set, tag `<sha>` | Same images, tag `aws-<sha>` | Same images |
| Database | PostgreSQL (dedicated database + least-privilege app role) | RDS PostgreSQL | Postgres container |
| Queue / cache | Redis (password auth) | ElastiCache Redis (TLS `rediss://`) | Redis container |
| Secret store | Key Vault (`SECRET_STORE=azure_key_vault`, managed identity) | Secrets Manager (`SECRET_STORE=aws_secrets_manager`, task IAM role) | OpenBao (`openbao`), or `env` for host-only dev |
| Identity — humans | Entra ID through the generic OIDC contract | Cognito through the same contract | Email OTP (bundled Mailpit) by default; dev-bypass opt-in |
| Identity — machines | PATs (`dq_live_`) — identical everywhere | ← same | ← same |
| Observability | App Insights via OTel | ADOT collector sidecar → X-Ray + CloudWatch | Off by default; Jaeger via `--profile telemetry` |
| Email transport | Any SMTP relay | SES (note: a sandboxed SES account can only mail verified addresses) | Mailpit catcher |
| IaC | [`deploy/terraform/azure/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/azure) | [`deploy/terraform/aws/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/aws) | `scripts/setup.sh` + compose |

## Features

The application feature set — suites/checks across all five datasources, every monitor kind,
assets/lineage/incidents, alerting, scheduling, the 33-tool MCP server, PATs, rate limiting —
is the **same code everywhere**. Where the installations genuinely differ:

| Capability | Azure | AWS | Local |
|---|---|---|---|
| Datasource runs | live-verified: Snowflake, Unity Catalog, ADLS, S3/S3-compatible, Iceberg | live-verified: Snowflake, Unity Catalog, native S3 | flat files, MinIO, Unity Catalog (Free Edition), Iceberg; no live warehouse required |
| Assets / lineage / incidents / DQ scorecard | live-verified (Snowflake full-tier lineage, UC dbt lineage, inventory sync) | deployed — same code; warehouse-lineage sweep enabled but not yet exercised against that account's grants | ✅ (Marquez reference consumer via `--profile lineage`) |
| Alerting — Teams / Slack | live-verified | deployed, unexercised | pointable anywhere |
| Alerting — email | configured | live-verified (SES) | Mailpit |
| MCP (38 tools) | E2E-verified | E2E-verified (PAT) | works; PAT-only under OTP (no IdP ⇒ no bearer) |
| Rate limiting | on (`RATE_LIMIT_XFF_TRUSTED_HOPS=3`, verified against a live XFF) | on (same setting; live-verified 2026-08-16 — an unauthenticated burst through CloudFront allowed exactly the configured 120/min then 429'd with `Retry-After`, and rotating a client-spoofed `X-Forwarded-For` could **not** escape the bucket, proving hops=3 selects the CloudFront-appended viewer IP) | off by design |
| Browser security headers (CSP/HSTS/nosniff/frame-ancestors) | on — `connect-src` narrowed to the Azure AD origin | on — `connect-src` narrowed to both Cognito origins (issuer + hosted UI) | on, permissive `connect-src` default |
| Edge rate limiting (WAF) | **none** — no Front Door/WAF in front of the Container App; the in-app limiter is the only layer | on — CloudFront WAF per-IP ceiling **in front of** the in-app limiter | n/a |
| Edge caching | n/a | fingerprinted `/assets/*.<ext>` cached at the edge; `/api`, `/mcp`, `index.html` always reach the origin | n/a |
| Sign-up gating | invite-only by nature (AAD tenant) | Cognito pool is admin-create-only + `OIDC_ALLOWED_EMAILS` | OTP allowlist (mandatory) |
| Orchestration — ADF | ✅ (Azure-only by nature) | n/a | n/a |
| Orchestration — Airflow / dbt | live-verified | **deployed but never fired** — the receivers are live, but no event source exists on that stack | local Airflow |

The Airflow/dbt row is the one real capability gap: the code is identical and deployed, but
the orchestration slice has never been exercised against the AWS installation. The cheapest
closure is pointing an external Airflow's callbacks at the AWS public URL.

The **edge rate-limiting row now runs the other way**: AWS has a WAF ceiling in front of the
application and Azure has nothing equivalent. The in-app limiter (identical on both) fails
open when its Redis store is unwell, so on Azure a flood that also stresses Redis meets no
limiter at all. Front Door + WAF is the analogous Azure change and is not done —
[#1388](https://github.com/TheurgicDuke771/DataQ/issues/1388). Neither deployment autoscales:
`desired_count = 1` / `max_replicas = 3`.

## Test harness

| | Azure | AWS | Local |
|---|---|---|---|
| Mock-data / flow harness | Full external harness (ADF pipelines, Airflow, dbt job; opened on demand) | **none** | Self-contained mirror: local Airflow + MinIO landing zone + an open-source Snowflake stand-in |
| Orchestration event source | ADF + Airflow + dbt | none | local Airflow |

## Deploying: Azure vs AWS

Both deploy workflows are `workflow_dispatch`-only with the same shape — backend job first
(build → **migrate, gated on success** → roll api + worker), then the frontend job gated on
the backend; a blank `image_tag` input defaults to the immutable commit SHA. The real
differences:

| | Azure `deploy.yml` | AWS `deploy-aws.yml` |
|---|---|---|
| Cloud login | Azure OIDC federated credential | AWS OIDC role, trusting `repo:…:environment:production` |
| Tag namespace | `<sha>` | `aws-<sha>` (parallel deploys from one registry never collide) |
| Migrations | Container Apps job start + poll until `Succeeded` | Register a migrate task-def revision + `run-task` with the api service's live network config, gated on container exit 0 |
| Roll + verify | `az containerapp update` per app + revision verify | Shared `scripts/ci/ecs_roll.sh`: register → `update-service` → `wait services-stable` → verify the **primary deployment's image**, so an ECS circuit-breaker rollback fails the run instead of reporting success |
| In-workflow smoke | none (the deploy README checklists are run manually) | final public-surface smoke against the `AWS_FRONTEND_URL` repo variable |
| Sidecars | n/a | the roll script swaps/verifies the *essential* container (api/worker task defs also carry the ADOT sidecar) |
| Rollout gotcha | — | task definitions are under `ignore_changes[container_definitions]`: env/sidecar changes need a targeted `tofu apply -replace` + `update-service`; the workflow only ever moves the image |

## Configuration: what actually differs

Most backend env is **identical in shape on both clouds** (`ENVIRONMENT`, `DATABASE_URL`,
`REDIS_URL`, `AUTH_DEV_BYPASS=false`, `WORKSPACE_ADMIN_EMAILS`,
`RATE_LIMIT_XFF_TRUSTED_HOPS=3`, `CORS_ALLOW_ORIGINS=""`, `PUBLIC_BASE_URL`, the
`*_WEBHOOK_SECRET_NAME`s, the `EMAIL_*` block, `WAREHOUSE_LINEAGE_ENABLED`). The genuine
differences:

### Backend (api + worker)

| Concern | Azure | AWS |
|---|---|---|
| Secret store | `SECRET_STORE=azure_key_vault` + `AZURE_KEY_VAULT_URL` + `AZURE_CLIENT_ID` (the user-assigned identity must be *selected* — without it every Key Vault read fails) | `SECRET_STORE=aws_secrets_manager` + `AWS_SECRETS_MANAGER_PREFIX` — no credential env at all; the task IAM role carries access |
| Token validation | Azure-native validator: `AZURE_TENANT_ID`, `AZURE_API_CLIENT_ID`, `AZURE_SPA_CLIENT_ID`, `AZURE_API_SCOPE`, `AZURE_ALLOW_GUEST_USERS` | Generic `OidcBearerScheme`: just `OIDC_ISSUER` + `OIDC_AUDIENCE`. The user profile resolves via the **userinfo endpoint**, because Cognito access tokens carry neither `email` nor `aud` |
| Secret injection | Container Apps secret refs | ECS `valueFrom` Secrets Manager ARNs — start-time snapshots, so a rotated infra secret needs a service bounce |
| Telemetry | `APPLICATIONINSIGHTS_CONNECTION_STRING` | `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` (loopback into the ADOT sidecar) |
| Email | Any SMTP relay + tfvars-sourced addresses | SES SMTP host + an IAM-derived SMTP password secret + `alert_email` / `alert_email_to` variables |

### Frontend (runtime `DATAQ_*` contract — one nginx image, envsubst at startup)

Both set `DATAQ_API_UPSTREAM`, `DATAQ_AUTH_MODE=oidc`, `DATAQ_AUTH_AUTHORITY`,
`DATAQ_AUTH_CLIENT_ID`. The differences:

| | Azure | AWS |
|---|---|---|
| Scope | `DATAQ_AUTH_API_SCOPE` (Entra wants the API scope requested) | `DATAQ_AUTH_SCOPE="openid email profile"` |
| Sign-out | standard RP-initiated logout | `DATAQ_AUTH_LOGOUT_STYLE=cognito` (Cognito's `/logout` wants `client_id` + `logout_uri`) |
| Origin guard | unset — open (Container Apps ingress is the only route in) | `DATAQ_ORIGIN_SECRET` (Secrets-Manager-injected; nginx 403s any request without the CloudFront-stamped header, `/_alb-health` exempt) |

For the local stack's configuration, see
[Running DataQ without Azure](deployment.md#running-dataq-without-azure) — the same seams,
pointed at compose services.
