# Third-party services & sub-processor disclosure

> **Who this is for:** the organization deploying DataQ (the data **controller** /
> covered entity) and its privacy/security reviewers. DataQ ships as
> **customer-deployed BYOL** (ADR 0013): there is no DataQ-hosted SaaS, so DataQ
> the company holds none of your data and, in the classic GDPR Art 28 sense, has
> **no sub-processors of your data at all**. What a controller still needs from us —
> and cannot produce themselves — is the list below: **every external service the
> software can send data to**, so your own sub-processor register and DPIA can
> name them. Which entries apply to *your* deployment depends on what you enable
> and where you point it.

## How to read this list

Every row is a **capability of the software**, enumerated whether or not it is
enabled — the same enumerate-don't-derive rule as the
[residency posture](../overview.md#data-residency), so a reviewer can see a vector
was considered rather than inferring its absence. For the live state of *your*
deployment, query `GET /api/v1/admin/deployment` (workspace-admin only), which
reports the enabled transfer vectors without shell access.

The **operator column is you**: in every row, the receiving service is chosen,
contracted, and configured by the deploying organization. DataQ never defaults to
a third-party endpoint.

## The vectors

| # | Service (operator-chosen) | What DataQ sends | Personal data? | When active |
|---|---|---|---|---|
| 1 | **Monitored datasources** — Snowflake, ADLS Gen2, S3 / S3-compatible, Unity Catalog, Iceberg | Read-only queries; DQ check SQL | Reads whatever the monitored tables hold — this is the primary data flow | Always (the product's purpose) |
| 2 | **Orchestration providers** — ADF, Airflow, dbt | Polling reads of pipeline/DAG run status; inbound webhooks | No — pipeline metadata only | When orchestration connections exist |
| 3 | **Alert delivery** — MS Teams / Slack webhooks, SMTP email (incl. SES) | Check names, statuses, suite names and — when a failing sample is attached — **redacted** sample values | Potentially, in redacted failing samples; recipient addresses | When notifications are configured |
| 4 | **Sign-in email (OTP)** — the configured SMTP relay | One-time codes to user email addresses | User email addresses (account identifiers, not warehouse content) | `otp` auth mode |
| 5 | **OIDC identity provider** — Azure AD, AWS Cognito, any standards-compliant issuer | Standard OIDC flows; DataQ receives (never sends) profile claims | User identity (email, name, subject id) held at the IdP | `oidc` auth mode |
| 6 | **Secret store** — Azure Key Vault, AWS Secrets Manager, OpenBao/Vault | Warehouse credentials (write on connection create, read at run time) | No customer data — but the credentials unlock the systems that hold it | Always (one of the four backends) |
| 7 | **Telemetry** — Azure Application Insights, AWS X-Ray/CloudWatch, any OTLP sink | Traces + structured logs, **PII-redacted at the logger** | Operational metadata; request ids, not warehouse values | When an exporter is configured |
| 8 | **MCP AI clients** — whatever model provider stands behind a client holding a valid PAT | Run results, redacted failing samples, check configuration, via the 48 `/mcp` tools | Potentially, in redacted samples — the model provider and its jurisdiction are **chosen by the token holder** | When PATs are issued and `/mcp` is reachable |
| 9 | **Outbound LLM intelligence** — DataQ calling a model on its own behalf | Schema + masked profiler statistics for SQL generation and check suggestions; RCA narratives additionally send the triggering check's own observed value, routed through that same column-policy/warehouse-tag masking floor, and its expected value (a check-authored threshold, not warehouse data, so never masked). Never raw sample rows | Potentially — profiler statistics or an observed value, both subject to the masking floor; model provider and jurisdiction are admin-chosen | **Live, off by default** — an admin must configure a provider + credential before any call leaves |
| 10 | **Lineage provider** — Marquez / OpenLineage-compatible catalog | Pull of lineage graph metadata | No — table/job names, not row data | When `LINEAGE_PROVIDER` is configured |

Cloud-platform services that *host* a deployment (the managed Postgres, Redis,
container runtime, object storage of your chosen cloud) are your cloud provider
acting as **your** processor under your existing cloud agreement — they are in
scope for your register but are not DataQ-specific and are not repeated here.

## Keeping this current — the process

This list changes only when the software's outbound surface changes, so it is
maintained as code review discipline, not a calendar:

1. **Any PR that adds an outbound network call to a new service class must update
   this page in the same PR** — same rule as the residency enumeration.
2. The **quarterly supply-chain audit** (CONTRIBUTING rule 39) re-checks the list
   against the dependency tree as a backstop.
3. The **LLM row flips from "not built"** in the PR that ships the intelligence
   feature — that PR is the Ch. V trigger the DPIA sheet also names.

Last reviewed: 2026-09-01 (originally 2026-08-21).
