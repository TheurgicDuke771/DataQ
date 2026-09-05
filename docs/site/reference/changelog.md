# Changelog

Notable, user-facing changes. Dates are the release/merge date. This is a curated summary —
the per-PR history lives in the repo's commit log and pull requests.

## Unreleased

### Added

- **Admin workspace health API.** `GET /api/v1/admin/health` reports per-connection
  orchestration poll staleness (`on_cadence` / `stalled` / `unknown` — a connection
  never polled never reads healthy), the Celery beat heartbeat (`alive` / `stale` /
  `not_monitored`), and broker queue depth for the `celery` and `llm` queues. Queue
  depth is `null` with a classified reason when the broker can't be reached — never
  a fake `0`.

- **Zero-sample privacy mode.** A deployment-level switch
  (`PRIVACY_ZERO_SAMPLE_MODE=true`) that stops any failing-row sample from
  ever being persisted — aggregates (`metric_value`, unexpected
  counts/percents) only. Gated at the one write-path choke point every check
  kind funnels through, so results, alerts and MCP all inherit the
  suppression; `GET /admin/deployment` reports whether it's on. For
  HIPAA/EU-tier deployments that need a stronger posture than the existing
  column-aware redaction.

- **Generate a custom-SQL check from a plain-English rule (API).** With an LLM
  provider configured, describe a rule ("order timestamps must never be in the
  future") to `POST /api/v1/llm/sql_generation` and DataQ drafts the violation
  query — dialect-aware for Snowflake and Databricks, grounded in the table's
  actual columns and optional masked profile statistics (never sample rows).
  The model's SQL is trusted no more than a human's: it passes the same
  read-only single-statement validator before it is ever stored, and adding it
  to a check goes through the ordinary custom-SQL editor path, dry-run included.
  Generation runs asynchronously with per-request rate limits, and every
  generation is recorded with requester, duration and token counts. The
  in-editor "Generate with AI" surface ships next.

- **Suggest checks for a suite from its column profile (API).** With an LLM
  provider configured, `POST /api/v1/llm/check_suggestions` proposes a curated
  batch of checks for a Snowflake or Databricks suite, grounded in real column
  statistics (null rate, distinct count, masked distributions) rather than a
  generic template. Every suggestion is drawn from DataQ's exact vetted
  expectation vocabulary and passes the same validator a hand-authored check
  would before it is ever returned — one that fails validation is dropped, not
  surfaced. A suggestion run that produces nothing runnable is a failure, not
  an empty success. Applying a suggestion goes through the ordinary check-create
  path unchanged. The review surface ships next.

- **Explain a failed check — LLM root-cause narrative (API).** With an LLM provider
  configured, `POST /api/v1/llm/rca_narrative` generates a narrative over an incident's
  already-captured evidence card plus a longer per-check history: a plain-English summary,
  ranked hypotheses each citing a real evidence layer (upstream pipeline delay, a sibling
  check failing in the same run, a cross-suite pattern on the same asset — never an
  invented cause), and `blind_spots`, a deterministic, non-LLM list of what the snapshot
  could not see (a deleted check, insufficient anomaly history, no lineage). Read-only —
  nothing is saved to the suite, so it's gated the same as reading the incident itself. The
  evidence card's own `observed_value` is routed through DataQ's standard column-policy/
  warehouse-tag redaction before the prompt is built. The review surface ships next.

- **Bring-your-own LLM provider (admin-configured, off by default).** A workspace admin can
  now point DataQ at an LLM — the Anthropic API, Azure OpenAI, AWS Bedrock, or any
  OpenAI-compatible endpoint including a self-hosted local server (Ollama / vLLM / TGI) —
  under **Admin → LLM**, with a live *Test* probe before enabling. This is the seam the
  authoring assists above build on (natural-language → SQL check generation, curated check
  suggestions, failure root-cause narratives); nothing calls the model until one of those
  features is used. The credential is write-only into the configured secret store and
  never returned by any API; every model call is recorded (requester, timing, token counts)
  and surfaced honestly in the deployment-posture disclosure. With no provider configured
  the product is unchanged. Changing the provider or endpoint URL requires re-supplying the
  credential, so a stored key can never be redirected to a new destination.

- **MCP `get_doc` — the published docs, reachable from a conversation (48 tools total at the
  time — since risen to 50; see below).**
  A curated set of user-facing pages (best practices, the feature matrix, security, getting
  started, MCP setup, and the five compliance runbooks/templates) is now readable through the
  MCP surface, verbatim and unsummarized — so "what are DataQ's best practices for authoring a
  check?" or "what compliance mechanisms does DataQ support?" answers from the maintained docs
  rather than training data. Deliberately excludes ADRs and `architecture.md` (contributor
  design-rationale, not this question's audience); an unrecognized page returns the current
  valid list. See [AI assistants (MCP setup)](../guides/mcp-setup.md).

- **Reusable notification channels (API).** A Teams, Slack, or email destination can now be
  defined once by a workspace admin and referenced from any number of suites, instead of
  pasting the same webhook into every suite's settings — rotating a compromised or expiring
  webhook is then one edit, not N. A suite may keep its existing per-suite webhook, link
  channels, or both; an alert fans out to every configured destination, deduplicated when two
  happen to resolve to the same URL, with one failing destination never blocking delivery to
  the others. Channel management is admin-gated (the webhook is a credential); linking a
  channel to a suite follows that suite's own view/edit access. The webhook URL itself is
  never returned by the API, only whether one is set. The admin UI for managing channels
  ships next.

- **A fourth channel type: a signed, generic outbound webhook.** Beside Teams/Slack/email, a
  channel can now be a plain HTTPS endpoint that receives an HMAC-SHA256-signed JSON body on
  every alert — a vendor-neutral way to wire up PagerDuty, Opsgenie, ServiceNow, Jira, or a
  self-hosted receiver with no bespoke integration. DataQ generates the signing key and shows
  it exactly once, at creation or rotation; the destination URL must be HTTPS and must not
  point at a private, loopback, or internal address.

- **A webhook channel can reshape its own payload and add an extra auth header.** An optional
  template lets a webhook channel produce the exact JSON shape a receiver expects (a PagerDuty
  Events-API body, an Opsgenie/ServiceNow/Jira payload) using `{{field}}` placeholders that
  pull from the same data every alert already carries — a template can rename or reshape what's
  there, never reach anything new. Left unset, the channel keeps sending the plain generic
  payload, unchanged. A channel can also carry one extra header (e.g. an API key some receivers
  want beside the signature); the value is write-only, same as every other channel credential.
  Because a template commonly has nowhere else to put a receiver's static integration key than
  as a literal in the JSON, the template itself is only ever shown to a workspace admin — every
  other user sees just whether one is set. Changing a channel's destination URL while it has a
  stored auth header now requires re-supplying that header's value in the same request.

- **MCP read tools for reusable notification channels (50 tools total).**
  `list_notification_channels` lists every channel in the workspace, and
  `list_suite_channels` lists the ones linked to one suite — the read surface
  `get_notification_config`'s `*_source: "channel"` and `*_channel_linked`
  fields could name but never identify. Same presence-not-value discipline as
  every other credential-bearing MCP field: a webhook/HMAC/auth-header value is
  never returned, only whether one is set, and a channel's `payload_template`
  is withheld from every caller including a workspace admin (stricter than the
  REST read, since it can carry a receiver's routing key as a plain JSON
  literal). Channel *mutation* (create/update/delete/link/unlink) stays
  REST-only for now.

- **Alerts now credit the asset owner, not just the suite creator.** When a table has an
  assigned owner, the "Owner" shown on a run alert (Teams card, Slack message, email) is that
  person — the one actually responsible for the data — rather than whoever happened to author
  the monitoring suite. A table with no assigned owner is unaffected: the suite creator still
  shows, exactly as before.

### Changed

- **The two distinct-value set relations no longer accept severity thresholds.**
  *Column distinct values in set* / *…contain set* compare a value SET — their results carry
  no unexpected-% for the warn/fail/critical bands to read (verified on a live Snowflake run),
  so a stored threshold could never fire. The editor now hides the threshold inputs for them
  and the API, MCP and suite import refuse threshold values with a 422 explaining why. A
  migration nulls any previously-stored (always-inert) thresholds on these types; the result
  stays a binary pass/fail. An export file predating this release that carries such thresholds
  is refused at import naming the check — remove the threshold keys from the document.

### Added

- **Nine more GX expectations, and a server-side allowlist of the types DataQ will
  author.** New in the editor: *Column values null*, *Column values not in set*,
  *Column value lengths equal*, *Column values do not match regex*, *Column values match a
  list of regexes* / *match none of a list of regexes*, *Column A equals column B*, *Values
  unique within each row*, and *Column values are valid JSON*. Each one was executed
  against both a dataframe and a SQL batch before being enabled, and each reports the
  unexpected-% the severity bands read.

  The larger change is underneath: `expectation_type` used to be turned into a Great
  Expectations class by string manipulation, so **any** of GX's ~56 built-ins was
  authorable through the API, the MCP tools or a suite import — including ones with no SQL
  implementation (they save cleanly and error on every run) and ones whose result carries
  nothing to band. All four author-time doors (create, update, dry-run preview, import) now
  validate against one vetted list, and the refusal distinguishes "not a Great Expectations
  expectation" from "recognised, but not enabled in DataQ" and names what is accepted.

  **Nothing already stored changes.** The gate is author-time only: a check written before
  this still runs and can still be edited, restored from its version history, or deleted —
  refusal applies only to *changing* a check to an unvetted type, or authoring a new one.
  The one exception is a suite **export → import** round-trip: import creates new checks, so
  a document carrying an unvetted legacy type is refused as a whole, naming the offending
  type — re-author that check as custom SQL (or drop it from the document) before importing.
  One of the new types, *Column values
  are valid JSON*, is implemented by GX for dataframe batches only, so it is offered on flat
  files, Iceberg and Unity Catalog and refused on Snowflake — as *Column values match a date
  format* already was.

  A tenth type, *Column pair values in allowed combinations*, is accepted by the API, the
  MCP tools and suite import but is **not** in the editor: its config is a list of value
  *pairs*, which the editor's comma-separated list field cannot express.

- **Seven more GX expectations in the check editor, and an optional tolerance on the
  ones already there.** The tolerance (`mostly`) is a fraction — `0.95` passes a
  check when at least 95% of rows conform. It moves GX's own success line only: severity
  thresholds still band the full unexpected-%, so a threshold below the tolerance can warn
  on a run the check passed.

  New types: *Compound columns unique* (a multi-column key), *Column A greater than column
  B*, *Columns sum to a total*, *Column values are of one of several types*, *Column
  distinct values in set* / *contain set*, and *Column values match a date format*.

  Two things are worth knowing before you use them. The two distinct-value set relations
  compare a **set**, so GX reports no unexpected-% and any severity thresholds on them are
  ignored — the editor now says so where you'd enter them. And the date-format check is
  implemented by GX for dataframe batches only: it is offered on flat files, Iceberg and
  Unity Catalog, and both the editor and the API refuse it on **Snowflake**, where it
  would save cleanly and then error on every run. Use a custom-SQL check there.

  Aggregate statistics (`expect_column_mean_to_be_between` and its siblings) are
  deliberately **not** part of this: they report a scalar rather than an unexpected-%, and
  are two-sided, which is the monitor-kind `metric_value` shape rather than the GX-banded
  one.

### Changed

- **BREAKING — the REST API now rejects an unknown field in any request body.**
  Every request model previously inherited `ApiModel`'s default
  `extra='ignore'` — the same gap `Settings` previously closed for env config —
  so a misspelled field (`warn_treshold`) or an invented one
  (`target_override`) validated cleanly and
  silently did nothing. It now 422s naming the field. If an API or PAT/MCP
  client sends extra keys in a request body — a stale client built against an
  older/different schema, a copy-pasted payload with leftover fields — those
  calls will start failing instead of quietly no-opping. Response bodies are
  unaffected; only what you *send* is stricter.

- **MCP `list_runs`/`list_incidents` gained `since_hours`/`until_hours` time
  filters:** both were count-capped only, so "what failed today" was
  answered with "the 20 most recent runs" — correct on a quiet workspace,
  wrong on a busy one. The offsets are relative to now ("N hours ago"), not
  clock times, so a caller doesn't need to know the server's current time.
  `list_incidents` also now states its auto-resolve blind spot: an
  incident auto-resolves on the first passing result, so a failure that has
  since recovered won't appear under `status="open"` even inside a matching
  time window — `list_runs`/`get_check_history` answer "what failed during
  period X", `list_incidents` answers "what's unresolved now". Its pagination
  now sorts by `last_seen_at` (the filter field) instead of `created_at`, so a
  truncated page is a contiguous slice of the window being asked about — this
  is a shared service function, so `GET /api/v1/incidents` (and the frontend
  Incidents panel, which renders in received order) now list most-recently-
  *active* first rather than most-recently-*opened*.

- **MCP `get_adf_pipeline_status` renamed to `get_pipeline_status`:**
  the old name predates dbt/Airflow support and was never ADF-only. The old
  name stays registered as a deprecated alias with identical behavior, so a
  client with it pinned keeps working; new integrations should use
  `get_pipeline_status`.

- **MCP `list_connections`/`test_connection` error-classification docstrings
  now cross-reference each other:** the two could read as
  contradictory — one claims a "classified" reason, the other says failures
  are "deliberately unclassified" — without stating that they're different
  things (a stored reason from the last real run/poll vs. a live probe with
  no reason at all).

- **MCP `create_check` gained the honesty fields the rest of the surface
  already has:** `config` is schema-validated only, never against the
  datasource; the `volume` monitor kind counts the true dataset size on every
  datasource (including ADLS/S3/Iceberg), unlike a sampled expectation check;
  `dimension: null` means unclassified, not a save failure; creating a check
  doesn't run it.

- **Unity Catalog checks now execute on the Databricks SQL Warehouse by default:**
  the built-in catalog expectations join custom SQL on one GX
  Databricks-SQL batch, so the warehouse evaluates them and worker memory stays
  flat regardless of table size (the Snowflake execution shape). Suites with a
  declared sample, `expect_column_values_to_be_of_type`, and unrecognised types
  stay on the previous read-into-pandas path; `UC_SQL_PUSHDOWN=false` restores
  the old routing wholesale. ⚠ Checks evaluated by the warehouse follow *its*
  semantics — most visibly `expect_column_values_to_match_regex`, which now uses
  Spark/Java regex instead of Python `re` on Unity Catalog; a pattern relying on
  Python-only constructs may report differently. Flip the flag if a check's
  behavior looks changed and file what you find.

### Fixed

- **Email sign-in codes are now sent by the worker, not on the request path.** The
  code-request endpoint's latency floor is a *minimum* — it could pad a non-member's response
  up to the floor but never an allow-listed address's down — so a mail relay slower than the
  floor made members measurably slower than strangers, and a relay outage answered `502` for
  members and `ok` for strangers. The api now mints the code, hands delivery to the worker
  and answers at the floor regardless of what the relay does; a send failure is a worker log
  line (`otp_send_task_failed`) and the response stays `ok`. Two things to check on upgrade:
  the **worker needs the same `AUTH_EMAIL_*` block as the api** (the reference compose and
  cloud stacks already share it), and the admin SMTP pre-flight (`POST
  /api/v1/admin/auth-email/test`) is unchanged — it still exercises the api's own transport,
  synchronously, so use it to find a broken relay. ADR 0032 carries the amendment.

- **The incident-evidence redaction backfill and the subject-rights incident scan classified
  a stored snapshot by the check's *current* column and type.** Every live surface resolves
  `(tested_column, expectation_type)` as of when a value was written, from the check's
  version history; the required post-deploy backfill
  (`backend/scripts/redact_stale_incident_evidence.py`) and the data-subject-request
  incident scan read the check's current row instead, so a check edited after an incident
  opened could make the backfill rewrite the only stored copy of the evidence differently
  from every other surface (leaving a value raw, or masking a count), and could hide an
  incident snapshot from a subject-access or erasure request. Both now resolve the pair as
  of the incident's `last_seen_at` — the moment its evidence was last written.

- **A scalar `observed_value` with no resolvable column was shown unscreened.** When a
  result's tested column could not be determined — a custom-SQL check (no single column),
  a check deleted after the run, or a caller supplying no context — the scalar branch of the
  results redactor skipped the whole ladder (warehouse tags, suite policy, fail-closed mode,
  the value-shape signal) and showed the value, on every results surface: the REST results
  API, MCP `get_run_results`, alert delivery, incident evidence and the dry-run preview.
  List-shaped values already failed closed on the same condition. A column-less scalar now
  shows only when the check's expectation type makes it a statistic (a row count, a
  custom-SQL unexpected-row count) *and* it passes the same screening as a named column;
  a cell-reporting type (column max/min) or an unknown type masks. Row-count and
  custom-SQL count checks render as before.

## v1.1.0 — 2026-08-21

Portability, auto-monitors, compliance-grade controls, and polish on top of v1. (First
tagged 2026-08-15; the tag was moved to the true cycle close on 2026-08-21 after the
stretch week landed the RBAC, MCP-expansion, security-audit and compliance tracks below.)

- **Append-only audit trail (ADR 0041)** — every config mutation (35 routes: checks,
  connections, shares, roles, schedules, credential rotations, …) writes an `audit_events`
  row **inside the mutation's transaction**, with actor, before/after, and `request_id`;
  **data reads are audited too**: reading a run's results, downloading a comparison
  report, profiling a column or dry-running a check records who accessed which data and
  **whether regulated data was actually surfaced**. Workspace-admin read endpoint
  (`GET /api/v1/admin/audit-events`), own retention clock. Verified in production on both
  clouds.
- **Warehouse-tag PII classification** — DataQ reads the column classifications a
  customer already applied in their own warehouse (Snowflake `dataq_classification` +
  `PRIVACY_CATEGORY`, Unity Catalog `dataq_classification`) and feeds them into the
  redaction ladder as a floor a suite policy cannot lift — on REST, MCP and alert
  delivery. Plus an opt-in **fail-closed mode** (`require_classification`): nothing
  row-level surfaces unless a column is explicitly cleared. Live-verified on both
  warehouses.
- **Redaction follows the destination** — an author's own interactive preview keeps its
  values (and is audited); the same data headed to an LLM context, a file export or an
  alert is redacted. Scalar `observed_value`s (a MAX/MIN is a real cell) now mask under
  the same rules as lists, resolved against the check's **historical** config so editing
  a check can never retroactively relabel old results.
- **Residency & encryption posture** — a declared `DEPLOYMENT_REGION` surfaced at
  `GET /api/v1/admin/deployment`, an IaC postcondition that fails the plan if the compute
  environment leaves the declared region, and a per-resource encryption-at-rest table for
  both reference deployments in the security docs.
- **Compliance document set** — sub-processor disclosure, DPIA input sheet,
  breach-notification runbook, and counsel-gated DPA/BAA templates, published on the docs
  site.
- **Hardened containers** — the backend image now runs as a non-root user.

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

- **Security hardening across both reference deployments:**
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
  0030), with natively-computed freshness/volume monitors, a column profiler +
  column listing, and dry-run preview.
- **Freshness & volume monitors** — the first auto-monitor kinds (is the data stale? did the
  load land whole?) on SQL datasources plus Iceberg.
- **Vendor-neutral observability** — OpenTelemetry logs + traces, exportable to Application
  Insights and/or a generic OTLP endpoint.
- **Personal access tokens (PATs)** — `dq_live_` tokens for headless / AI-client use, on REST
  and MCP (ADR 0026).
- **Workspace-admin visibility** extended to the MCP tools + schedules; **dry-run preview**
  extended to every datasource.
- **Every MCP tool now states what it cannot see.** A REST caller wrote their own
  query and a UI user reads the screen; an AI client has neither, so a tool that
  returns literally-true values while omitting its blind spot produces a
  confident wrong answer. All 46 were audited against six criteria — population,
  time window, truncation, freshness, null/zero semantics, and what the tool
  structurally cannot see — and the limits are returned as **fields** wherever
  possible rather than prose: `truncated` / `oldest_in_page` (the list tools take
  no time filter), `results_final` (a mid-run partial is not a verdict),
  `redaction` / `redacted_columns` (a masked sample is not an absent one),
  `sampled` / `sample_row_limit` (flat-file and Iceberg profiles read at most
  100k rows), `runnable`, `is_recurrence`, `window_hours`. Six docstrings were
  corrected outright, having claimed things the code does not do — see
  [AI assistants (MCP setup)](../guides/mcp-setup.md) under *Reading the results honestly*.
- **MCP server expanded to 46 tools** — the Tier 1 read-only batch (checks, runs,
  connections, schedules, trigger bindings, notification config, suite performance,
  suite export) alongside the original 8, plus the Tier 2 batch (update/delete/snooze
  check, dry-run preview, cancel run, schedule + trigger-binding CRUD, suggest a PII
  policy, test a connection, import a suite), plus the Tier 3A coherence batch
  (set a suite's run target, read/set its column policy, update a schedule,
  update/delete a trigger binding, read and restore a check's version history), plus the
  Tier 3B batch over **assets and incidents** (browse the tables DataQ monitors with
  their health and lineage; see what is broken right now and the evidence behind it,
  acknowledge and resolve it, list a target's columns before authoring, and diagnose
  orchestration triggers that are silently never firing) — each reusing the same
  authorization its REST counterpart applies (ADR 0008 amendments). The 46 split three ways: 23
  read-only; 18 that change state, gated on `edit` access to the suite they act on; and
  5 that persist nothing but open a live datasource connection with stored credentials
  (`profile_column`, `list_columns`, `dryrun_check`, `suggest_column_policy`,
  `test_connection`) — gated
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
  / Wasabi / Backblaze alongside AWS S3.
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

See the [Features](../guides/features.md) page for the full current capability set.
