# Changelog

Notable, user-facing changes. Dates are the release/merge date. This is a curated summary —
the per-PR history lives in the repo's commit log and pull requests.

## Unreleased — v1.1 (in progress)

Portability, auto-monitors, and polish on top of v1:

- ⚠️ **Workspace roles — Admin / Member / Viewer (ADR 0033)** — authorization is now two
  axes. Your **workspace role** says what kind of user you are; the existing per-suite
  `view / edit / admin / owner` grants say what you can touch. Neither replaces the other:
  a Member with no share on a suite still cannot see it.

  | Capability | Admin | Member | Viewer |
  |---|---|---|---|
  | See/use suites shared to them | ✅ | ✅ | ✅ (view only) |
  | Create/import suites (become owner) | ✅ | ✅ | ❌ |
  | Receive `edit` shares | ✅ | ✅ | ❌ — capped at `view` |
  | Connections: create / edit / delete / re-auth | ✅ | ❌ | ❌ |
  | Connections: list & reference in suites | ✅ | ✅ | list only |
  | Connections: test | ✅ | ✅ | ❌ |
  | `/admin`, implicit suite-admin, workspace-wide visibility | ✅ | ❌ | ❌ |

  **BREAKING — Members lose connection-write.** Previously *any* authenticated user could
  delete or re-credential the connection every suite in the workspace ran on. If people who
  are not workspace admins manage connections in your deployment, **promote them to Admin
  before upgrading**, or those operations will start returning 403.

  Everything else upgrades with **no config change**: existing users become Members (which
  is exactly what they could already do), and `WORKSPACE_ADMIN_EMAILS` keeps resolving to
  Admin — it is now a **bootstrap seed and lockout break-glass** rather than the admin
  mechanism, and it only ever grants, never demotes. New: `AUTH_OTP_DEFAULT_ROLE` /
  `AUTH_OIDC_DEFAULT_ROLE` set the tier new signups land on (`member` by default; set
  `viewer` when your signup allowlist is a whole domain).

  Roles resolve **per request**, so a change takes effect on the target's next request —
  including requests made with their existing API tokens, which authenticate as their user.
  There is no token to revoke.

- **Security hardening across both reference deployments** —
  ([#1386](https://github.com/TheurgicDuke771/DataQ/issues/1386),
  [#1387](https://github.com/TheurgicDuke771/DataQ/issues/1387),
  [#1388](https://github.com/TheurgicDuke771/DataQ/issues/1388)):
  - **Who may hold an account is now an explicit decision.** DataQ provisions a user on first
    successful OIDC sign-in, so the identity provider's registration policy was in effect the
    product's access policy. The AWS reference pool is now admin-create-only, and a new
    app-side allowlist (`OIDC_ALLOWED_EMAILS` / `OIDC_ALLOWED_DOMAINS`) is enforced on every
    request — on REST **and** MCP — so it revokes as well as admits.
  - **Browser security headers on every response** — CSP, HSTS, `X-Frame-Options`,
    `nosniff`, `Referrer-Policy`, `Permissions-Policy`. The CSP matters here because the UI
    renders warehouse-supplied content (failing-row samples, error text).
  - **Edge protection on AWS** — a CloudFront WAF per-IP rate ceiling in front of the in-app
    limiter (which fails open by design), plus edge caching of the fingerprinted bundle so
    the distribution actually absorbs load instead of passing everything through.

- **AWS as a second deployment target** — a live-verified OpenTofu reference stack
  ([`deploy/terraform/aws/`](https://github.com/TheurgicDuke771/DataQ/tree/main/deploy/terraform/aws)):
  ECS Fargate (api / worker / frontend) + RDS + ElastiCache + **Amazon Cognito** (via the
  same generic OIDC contract) + **AWS Secrets Manager** (`SECRET_STORE=aws_secrets_manager`,
  a fourth secret backend) behind **CloudFront** with an nginx-enforced origin secret;
  **SES email alerts**, **X-Ray tracing** through an ADOT sidecar on the app's
  vendor-neutral OTLP export, and a dedicated **Deploy (AWS)** GitHub Actions workflow.
  Azure remains the primary reference deployment.
- **dbt as a third orchestration provider** — observe dbt builds and trigger suites on
  success, via a post-build HMAC callback + a `run_results.json` artifact poll (ADR 0029).
- **Apache Iceberg as a fifth datasource** — native `pyiceberg` read (v2 baseline; ADR
  0030, #716/#722), with natively-computed freshness/volume monitors, a column profiler +
  column listing, and dry-run preview (#721).
- **Freshness & volume monitors** — the first auto-monitor kinds (is the data stale? did the
  load land whole?) on SQL datasources plus Iceberg.
- **Vendor-neutral observability** — OpenTelemetry logs + traces, exportable to Application
  Insights and/or a generic OTLP endpoint.
- **Personal access tokens (PATs)** — `dq_live_` tokens for headless / AI-client use, on REST
  and MCP (ADR 0026).
- **Workspace-admin visibility** extended to the MCP tools + schedules; **dry-run preview**
  extended to every datasource.
- **MCP server expanded to 30 tools** — the Tier 1 read-only batch (checks, runs,
  connections, schedules, trigger bindings, notification config, suite performance,
  suite export) alongside the original 8, plus the Tier 2 batch (update/delete/snooze
  check, dry-run preview, cancel run, schedule + trigger-binding CRUD, suggest a PII
  policy, test a connection, import a suite) — each reusing the same authorization its
  REST counterpart applies (ADR 0008 amendments, #529). The 30 split three ways: 16
  read-only; 10 that change state, gated on `edit` access to the suite they act on; and
  4 that persist nothing but open a live datasource connection with stored credentials
  (`profile_column`, `dryrun_check`, `suggest_column_policy`, `test_connection`) — gated
  like writes, not reads, and requiring the **member** workspace role where there is no
  suite to gate on. No MCP tool is Admin-only: every Admin-only capability in ADR 0033's
  matrix is a connection mutation, and none are exposed here. `list_connections` returns
  metadata + health only, never config or secrets; `get_notification_config` reports
  channel presence, never webhook URLs; `test_connection` reports only pass/fail, never
  a credential; connection create/update/reauth remain excluded — a credential must
  never transit an LLM.
- **Run failure reasons** — a run that fails to execute now shows a redaction-safe reason.
- **Secret lifecycle** — connection delete cleans up its stored secret.
- **Assets as the primary lens** — a data asset (table/file) is now a first-class entity
  with its own page: health rolled up across every suite that targets it, open incidents,
  and lineage. The dashboard and sidebar lead with assets; suites and runs link back to
  the asset they touch (ADR 0034).
- **Lineage graph** — an asset's provenance and blast radius render as one left-to-right
  graph, one column per hop, with clickable nodes.
- **Asset browse by source** — drill down datasource → database → schema → table, with a
  flat searchable table as the second lens.
- **Two health axes on an asset** — "could DataQ reach the datasource?" is shown separately
  from "is the data good?", so an unreachable datasource no longer masquerades as a data
  failure, and a run that evaluated nothing no longer reads as a green pass.
- **Datasources read as names** — the UI shows `Snowflake · ACCT` / `ADLS · account/container`
  / `iceberg_catalog` instead of the raw connection string (an Iceberg namespace is a full
  DSN). The raw identifier stays available on hover and via copy.
- **Mobile** — the sidebar becomes an overlay drawer on a narrow viewport, and the share /
  edit panels reflow so their controls stay on screen (previously the "Add" button in the
  share drawer was painted off the right edge, making a suite unshareable from a phone).
- **A broken orchestration poll now tells you** — an expired credential silently stopped
  pipeline-run ingest, suite triggering, and lineage refresh for six days in our own
  production. A failing poll is now a fact about the connection: the connections list badges
  it with a failure count, the lineage panel warns instead of showing a confident empty
  graph, and after 3 consecutive failures an alert is pushed to the workspace channel — once
  on the way down and once on recovery, never once per poll. The reason is always the
  **classified** one, never raw error text (the real failure carried a SAS token in its
  message).
- **Email OTP sign-in** — a third authenticator alongside dev-bypass and OIDC (ADR 0032):
  a one-time code emailed to an allow-listed address, `dq_sess_` cookie sessions, no
  Identity Provider required. Now the default for the local/eval stack (bundled Mailpit
  mail catcher), gated by one switch.
- **Workspace roles (Admin / Member / Viewer)** — stored, in-app-managed roles (ADR 0033)
  replace the old email-allowlist-only admin model; connection mutations are now
  Admin-only. `WORKSPACE_ADMIN_EMAILS` is a bootstrap/break-glass allowlist, not the
  primary mechanism.
- **`schema_drift` monitor kind** — flags added/removed/type-changed columns against a
  captured baseline, with a one-click re-baseline.
- **`comparison` checks** — cross-dataset reconciliation (row counts, column-level
  mismatch detection) between a suite's target and a second dataset (ADR 0015).
- **`anomaly` monitor kind** — a rolling z-score baseline with optional seasonality over a
  check's `metric_value` history; skips on cold start rather than false-alerting.
- **DQ-dimension classification** — every check carries a dimension (accuracy /
  completeness / consistency / integrity / timeliness / uniqueness / validity, ADR 0038),
  derived by default and overridable, feeding the new **asset DQ scorecard** — coverage by
  dimension, not just a pass rate.
- **Metric trend view** — a per-check history chart with threshold bands and an
  anomaly-baseline overlay.
- **Self-hosted secret store (OpenBao)** — a `SecretStore` backend speaking the KV v2 HTTP
  API (ADR 0039), alongside Azure Key Vault, for a non-Azure or self-hosted deployment.
- **Request rate limiting** — a fixed-window throttle across REST, webhooks, and `/mcp`
  (ADR 0035), with separate per-token / per-IP / per-webhook-provider classes.
- **S3-compatible endpoints** — the `s3` datasource and the `dbt` orchestration provider
  both accept an optional `endpoint_url` (+ addressing style), unlocking MinIO / Ceph / R2
  / Wasabi / Backblaze alongside AWS S3 (#1063).
- **Warehouse inventory sync** — an opt-in per-connection sweep (ADR 0040) that enumerates
  every table in a database, so a table with no suite/run/lineage edge shows up as
  visible-and-unmonitored instead of invisible.
- **PDF report export** — a zero-dependency, one-click PDF of a run's results.

### Fixed

- **Iceberg catalog credential exposure** — the SQL-catalog password had to be carried
  inline in the `catalog_uri` (the connection type had only one secret slot), which meant
  it was persisted in the connections table, copied into the asset identity, returned by
  the API, and rendered in the UI. Iceberg connections now take a **second secret**
  (`catalog_secret_name`), the config **rejects** a password in `catalog_uri`, and a
  migration scrubs existing rows. **Action required:** an existing Iceberg connection whose
  `catalog_uri` carried a password must be re-pointed at a secret and **the password
  rotated** — treat it as disclosed.
- **`/assets` deep links 404'd** in the production image — the nginx rule for the hashed
  bundle directory also swallowed the `/assets` app route.

- Docs: Features overview, Recommended usage, this changelog, security, REST API,
  troubleshooting, deployment, and a first-suite tutorial.

## v1.0.0 — 2026-07-04

First production release. Deployed to Azure Container Apps.

- **Data-quality checks** across four datasources — Snowflake, Unity Catalog, ADLS Gen2,
  AWS S3 — on Great Expectations, with a catalog-driven check editor and custom SQL.
- **Suites** with per-suite view/edit sharing and export/import for env promotion.
- **Runs** — run-now with live progress + cancel, cron **scheduling**, and **pipeline
  triggers** from ADF & Airflow.
- **Results & dashboard** — severity tiers, a health score, trends, and **column-aware
  redacted** failing-row samples.
- **Alerting** — Teams / Slack / email with severity routing, dedup, and per-check snooze.
- **AI assistants** — an 8-tool MCP server for Claude / Copilot / Cursor.
- **SSO** (OIDC) and secrets in a managed vault; the frontend is the sole public surface.

See the [Features](features.md) page for the full current capability set.
