# Changelog

Notable, user-facing changes. Dates are the release/merge date. This is a curated summary —
the per-PR history lives in the repo's commit log and pull requests.

## Unreleased

### Breaking

- **A connection that fails its own test is no longer saved.** Creating a connection, changing
  its settings or credential, and re-authenticating it now run the connection's test first. If
  the test fails, the request returns `422` with `error.code`
  `connection_test_failed_on_save` and nothing is written — a re-authentication keeps the
  stored credential instead of replacing it with one that does not work (before, it replaced
  it and then reported the failure). The reason is the same safe message **Test** gives. An
  Admin can still save on purpose, for a store the API cannot reach while you set it up or a
  new dbt project with no `run_results.json` yet: **Create/Save/Rotate without testing** in the
  UI, or `"skip_test": true` in the request body; the audit log records it. Saving an
  **enabled** LLM provider (`PUT /admin/llm`) likewise runs its test first
  (`llm_test_failed_on_save`); a disabled one is saved untested. API clients that create
  connections with a config that fails its test must fix the config or send `skip_test`. See
  [A connection is tested before it is saved](../guides/datasources-checks.md#a-connection-is-tested-before-it-is-saved)
  and [API compatibility](api-compatibility.md#recorded-exceptions).

- **The prebuilt-image stack publishes only the UI, over HTTPS.** `docker-compose.ghcr.yml`
  now runs the way a production deployment does: the UI on `:3000` is the only way in, it
  serves HTTPS with a certificate from a CA the stack generates on first start (your browser
  warns until you trust it — `scripts/local-ca.sh install` does that, and `uninstall`
  reverses it; plain HTTP redirects), and the API is reached through it at
  `https://localhost:3000/api` (MCP at `https://localhost:3000/mcp/`).
  The API (`:8000`), Postgres (`:5432`), Redis (`:6379`), the vault (`:8200`) and the mail
  catcher's SMTP port (`:1025`) no longer have host ports, and the interactive API page at
  `/docs` is off. Point scripts that called `http://localhost:8000` at
  `https://localhost:3000` and give them the CA certificate (see Getting started). Inside
  the stack, Postgres, Redis, the mail catcher, the vault and the API are all reached
  over TLS verified against the same CA, and Postgres refuses a client without it. The inbox
  stays at `http://localhost:8025`. The from-source stack (`docker-compose.yml`) is unchanged.

- **The prebuilt-image stack keeps its data in a directory.** `docker-compose.ghcr.yml`
  stores the database, the sample warehouse and the vault under `./dataq-data`
  (`DATAQ_DATA_DIR` moves it) instead of Docker volumes, and the vault is no longer
  in-memory: stored credentials survive a restart. Data in the previous `ghcr_postgres_data`
  volume is not migrated; the stack starts fresh in the new directory. To reset, run
  `down -v` and delete the directory.

- **A check's severity thresholds can be cleared.** Emptying a warn, fail or critical
  threshold in the check editor and saving used to leave the old value in place. `PATCH
  /suites/{id}/checks/{id}` now treats an explicit `null` for `warn_threshold`,
  `fail_threshold` or `critical_threshold` as *clear it*; leaving the key out still keeps
  the stored value. **A client that sent `null` for these to mean "unchanged" must omit
  the key instead.** A check that needs a fail or critical threshold (freshness, anomaly) still
  refuses to lose its last one. Over MCP, `update_check` gains `clear_thresholds`.

### Added

- **An upgrade guide and a support policy.** [Upgrading](../operate/upgrading.md) covers what to
  back up, the steps for the prebuilt-image stack and a cloud deployment, and rolling back.
  `SUPPORT.md` says where to ask for help. The security page now states outright that DataQ
  sends nothing to its authors.
- **AI suggestions are kept for automatic suites.** Running **Suggest checks** on an
  automatically covered suite now also saves the validated suggestions under **Suggested
  rules**, marked **AI-suggested**, where they can be accepted or rejected later. A rule the
  suite has already seen is not saved again. The invocation result gains `queued_for_review`.
- **Two MCP tools for automatic coverage.** `get_coverage` returns the share of the asset
  inventory watched in the last 7 days and the false-positive rate of automatic checks, with
  the counts behind each. `list_suggested_rules` lists a suite's review queue. Both are
  read-only; accepting or rejecting a rule is still done in the app. `/mcp` now has 54 tools.
- **Say what an incident turned out to be.** Resolving an incident can record whether it was
  **fixed**, an **expected change** or a **false positive**. It is optional; left out, the
  incident is recorded as not stated. In the API, `POST /incidents/{id}/resolve` takes
  `resolution` and incidents return it; the MCP `resolve_incident` tool takes it too.
- **Coverage and false-positive figures on the Dashboard.** A new panel shows what share of the
  asset inventory was watched by a suite in the last 7 days, and what share of resolved
  automatic-check incidents were marked false positives in the last 30, each with the counts
  behind it. In the API, `GET /dashboard/coverage`.
- **Apply a suite file onto an existing suite, and see drift.** `POST /suites/{id}/apply`
  creates, updates and (with `prune`) deletes checks to match a JSON or YAML document,
  matching checks by name; it is idempotent. With `dry_run` it changes nothing and reports
  how the suite differs from the file. The `dataq` command gains `validate`, `drift` and
  `apply`, and reads and writes YAML. See [Suite document](suite-document.md).
- **Bulk snooze, unsnooze and delete for checks.** The suite page's check list has a
  checkbox per check and **Select all**; the selected checks can be snoozed, unsnoozed or
  deleted in one action. It is all-or-nothing, and each check gets the same audit event a
  single action writes. In the API, `POST /suites/{id}/checks-bulk/snooze`,
  `/checks-bulk/unsnooze` and `/checks-bulk/delete`.
- **Bulk severity thresholds.** **Set thresholds** on the same selection gives every selected
  check the same warn, fail or critical value; each tier can be set, cleared or left as it
  is. Refused as a whole, naming the checks at fault, if the selection mixes check kinds
  or any check cannot take the result. In the API, `POST /suites/{id}/checks-bulk/thresholds`.
- **A data-quality score per connection.** Each connection card shows a **DQ score** across
  every suite on that connection, from each suite's latest run if it completed. It is the same
  for every member, and absent when nothing has evaluated. In the API, connections gain
  `health_score`.
- **Each quality dimension across the whole workspace.** The Dashboard has a new panel
  with one row per dimension over every suite in the workspace, with the same coverage
  rules as the asset scorecard: a dimension with no checks anywhere is listed as not
  covered, and checks with no dimension are counted separately. In the API,
  `GET /dashboard/dimensions`.
- **Suites as YAML files, and a way to validate them.** A suite document can now be YAML as
  well as JSON: `POST /suites/import` accepts `document_yaml`, and
  `GET /suites/{id}/export?format=yaml` produces it. The new `POST /suites/validate`
  reports every problem an import would be refused for and creates nothing. The format is
  now documented field by field in [Suite document](suite-document.md). Plain YAML values
  follow JSON's rules, so `NO` and `2026-01-01` stay text.
- **An asset health score.** The asset page's scorecard now leads with one 0–100 number
  for the whole asset, and **Assets → All assets** shows it in a **Score** column and can
  sort the whole workspace by it, lowest first. It counts every check that evaluated in
  each suite's latest run, if it completed, including checks with no dimension. Beside it, the
  asset page shows the change against the same score 7 days ago. In the API, assets gain
  `health_score`, `GET /assets` gains `sort=health_score`, and `GET /assets/{id}` gains
  `previous_health_score`, `health_score_delta` and `score_delta_days`. See
  [Datasources & checks](../guides/datasources-checks.md#seeing-coverage-the-asset-scorecard).
- **Real mailboxes from the prebuilt-image stack.** Point the sign-in mailer at your own
  relay and supply its password once as `DATAQ_SMTP_PASSWORD`: a start-up step stores it in
  the stack's vault, where it persists, and the API never holds it. The alert mailer's
  `EMAIL_*` settings are now passed through as well, with `DATAQ_ALERT_SMTP_PASSWORD`. With
  a relay configured and no password stored, the start-up step says so instead of storing a
  random one. See Getting started.

- **Python client and CLI (`dataq-client`).** Install the wheel attached to the release (or the
  latest from `main`), then
  `dataq run <suite> --wait` gates a CI step on a suite's result with a documented exit code
  (0 ok · 1 warn · 2 fail · 3 run error · 4 client error). The Python API keeps a run's
  lifecycle and its worst severity as separate fields, and exposes every REST endpoint through
  a layer generated from the API's own specification. See
  [Python client & CLI](../guides/python-client.md).
- **API compatibility policy.** What stays stable across upgrades for the REST API, MCP tools,
  the suite export document and the Python client, and how a breaking change is announced. See
  [API compatibility](api-compatibility.md).
- **Custom Snowflake DMFs as checks.** On a Snowflake connection, a **Custom DMF** check runs
  a data metric function your team created, named `DATABASE.SCHEMA.FUNCTION`, over columns of
  the suite's table. Its return value is banded by the check's thresholds. Testing the
  connection lists the custom DMFs its role can use, and the editor suggests them. DMF checks
  can now be dry-run from the editor too. See
  [Datasources & checks](../guides/datasources-checks.md#snowflake-dmf-adr-0036).

### Fixed

- **"Triggered by" names who started a run.** The Dashboard, Results, the run page and the
  printed report showed an internal identifier such as `manual:d7c88410-…`. They now show
  the person's name or email (`Manual — Olivia Admin`), `Schedule`, or the orchestration
  provider. In the API, `triggered_by` is unchanged and runs gain `triggered_by_label`.
- **The Custom SQL card opens the plain SQL editor.** Both cards in the Custom SQL step used
  to open a form with the **Generate from a description** box. It now appears only when you
  start from the **Generate from a description** card. Editing an existing custom SQL
  check no longer shows it either.
- **Check suggestions on a wide table stay within the model's limits.** The suggestion prompt
  grew with every column, so a table with hundreds of columns could cost far more or fail at
  the provider. Only the first 100 columns are now profiled and sent, long values are
  shortened, and the drawer says when part of the table was not looked at. The invocation's
  result gains `column_coverage` (`{profiled, total}`).

- **A completed run with no results no longer says it did not complete.** A check's results
  are removed when the check is deleted, so a finished run can be left with none (as can a
  run of a suite that had no checks). The run
  page and the printed report now say that, keep "did not complete" for runs that failed
  or were cancelled, and say a queued or running run has not finished yet.

- **A batch-target preview no longer comes back empty just because signing in was slow.** The
  preview's time budget now starts when the store returns its first object, not before the
  client authenticates; a service principal's token request alone could use the whole budget.

- **The batch-target preview no longer scans unbounded, or matches a regex it hasn't
  vetted, in the API process.** The suite editor's live "resolves to" hint now stops at a
  small object-count/wall-clock budget (`BATCH_PREVIEW_MAX_OBJECTS`/`_MAX_SECONDS`) and
  reports `truncated` honestly instead of scanning until it can be sure; a batch pattern is
  refused up front if it is too long or shaped for catastrophic regex backtracking (the
  same check applies whether the pattern is being saved or previewed). The frontend cancels
  a superseded preview request instead of merely ignoring its answer.

### Added

- **ADLS Gen2 connections can authenticate as an Entra ID service principal — which also
  opens Microsoft Fabric OneLake lakehouse files.** Pick *Service principal* and give the
  tenant ID, client ID and client secret; point the account URL at
  `https://onelake.blob.fabric.microsoft.com` with the workspace as the container to run any
  flat-file check, freshness, volume, profile or browse against `<lakehouse>.Lakehouse/Files/`.
  SAS connections are unchanged. Changing the tenant, client, auth type or account URL
  requires re-entering the secret. A client secret's expiry is not readable by DataQ, so the
  connection card says so rather than showing nothing. See
  [OneLake](../guides/datasources-checks.md#onelake-fabric-lakehouse-files).

- **MySQL / MariaDB datasource.** Any MySQL or MariaDB server, on the same generic SQL
  base as PostgreSQL and through the MIT-licensed PyMySQL driver: every SQL-capable check,
  all monitors, the profiler, browsing and inventory sync. Sessions are read-only and UTC;
  TLS is `require` by default. *Column values unique* needs the `CREATE TEMPORARY TABLES`
  grant — see [Datasources & checks](../guides/datasources-checks.md#mysql-mariadb).

- **dbt artifacts on OneLake, or as a service principal.** A dbt connection can read its
  ADLS artifacts as an Entra service principal, and from an ADLS-compatible endpoint such as
  Fabric OneLake — see [Orchestration](../guides/orchestration.md).

- **Amazon Redshift datasource.** Connect a provisioned Redshift cluster or a Serverless
  workgroup with a database user and run every SQL-capable check, custom SQL, all monitors,
  the profiler, schema → table browsing and inventory sync. Sessions are read-only at the
  server, and every TLS mode checks the certificate chain against Amazon's certificate
  authorities — see [Datasources & checks](../guides/datasources-checks.md#amazon-redshift).

- **Amazon Athena datasource.** Connect Athena by region and IAM access key — one data catalog
  per connection, a Glue database as the schema — and run every SQL-capable check, custom SQL,
  all monitors, the profiler, database → table browsing and inventory sync. Every check is a
  billed Athena query. Athena has no read-only session, so give DataQ an IAM principal that can
  only read — see [Datasources & checks](../guides/datasources-checks.md#amazon-athena).

- **Trino datasource.** Connect any Trino (or Starburst) cluster — one catalog per connection
  — and check whatever that catalog federates (Hive, Iceberg, PostgreSQL, Cassandra, Kafka…)
  with every SQL-capable check, all monitors, the profiler, schema → table browsing and
  inventory sync. Password, JWT (its expiry is shown) or no authentication; TLS always verifies
  the server, with an optional private CA bundle, and a credential is never sent in plaintext.
  Trino has no read-only session, so give DataQ a Trino user with read-only access — see
  [Datasources & checks](../guides/datasources-checks.md#trino).

- **PostgreSQL datasource.** Connect any PostgreSQL server — self-hosted or a managed
  service — and run every SQL-capable check on it: GX expectations and custom SQL by pushdown,
  freshness / volume / anomaly / schema-drift monitors, comparisons, the column profiler,
  schema → table browsing for the run target, and inventory sync into the asset view. TLS is
  `require` by default and every session is read-only at the server. It is the first engine on
  a new engine-generic SQL base (ADR [0045](../adr/0045-engine-generic-sql-datasource-base.md)).
  See [Datasources & checks](../guides/datasources-checks.md#postgresql).

- **SQL Server / Azure SQL / Fabric datasource.** One `mssql` connection type for anything that
  speaks SQL Server's TDS protocol — SQL Server, Azure SQL Database, Synapse, and Microsoft
  Fabric SQL endpoints — with a SQL login or an Entra ID service principal. Every SQL-capable
  check runs by pushdown, plus monitors, comparisons, the profiler, schema → table browsing and
  inventory sync. TLS is always on and always verified (certificate and hostname). The default
  driver is pure-Python and shipped; Fabric SQL endpoints currently need an optional ODBC lane
  you install yourself, and
  DataQ tells you so instead of showing a driver error. On Fabric, the uniqueness and
  column-pair expectations are refused (Fabric does not support the temporary table GX builds
  for them). Regex
  expectations are not available on SQL Server. ADR
  [0044](../adr/0044-mssql-tds-driver-and-entra-auth.md); see
  [Datasources & checks](../guides/datasources-checks.md#sql-server-azure-sql-fabric-t-sql).

### Security

- **The custom-SQL guard now lexes each engine's own quoting.** A T-SQL `[bracket]` or a
  Databricks `` `backtick` `` identifier containing a quote could make the read-only check
  mistake a following statement for part of a string; the guard now reads those identifiers
  as identifiers, rejects a nested block comment (engines disagree where one ends), and on
  SQL Server also refuses `OPENQUERY`, `OPENROWSET`, `OPENDATASOURCE`, `BULK`, `DBCC` and
  `WAITFOR`. A query whose engine is unknown must pass every engine's rules.

- **Sensitive columns stay masked downstream.** If a warehouse tag marks a column sensitive,
  every column that recorded column lineage shows is copied or derived from it is masked as well,
  across REST, MCP, alerts and incident evidence. This only ever adds masking: a column's own tag
  still wins, and nothing inherits `public`. `LINEAGE_CLASSIFICATION_PROPAGATION=false` turns it
  off. See [security](../security/overview.md).

- **Check suggestions know where a column comes from.** A suggested check on a column that
  is copied unchanged from an upstream table now says so — "an equivalent check already runs
  upstream" or "place it at the origin" — so one bad load fires one alert, not one per copy.
  Derived columns get their provenance only; the advice never hides a suggestion. See
  [AI features](../guides/ai-features.md).

- **Trace one column through lineage.** The asset page's *Column lineage* card (and
  `GET /assets/{id}/column-lineage`, MCP `trace_column_lineage`) follows a single column up to
  where it originates and down to every column derived from it. Every lineage edge now says
  why it has no column pairs when it has none (`column_coverage`: none recorded · unavailable ·
  unknown · not captured), so an empty column trace is never presented as "unrelated".
  Snowflake column pairs are now read from `ACCESS_HISTORY` even when `GET_LINEAGE` answers —
  previously an Enterprise account recorded none. See
  [orchestration & lineage](../guides/orchestration.md).

- **Browse for a run target instead of typing it.** On a Unity Catalog suite, **Browse
  catalog…** walks catalogs → schemas → tables and fills all three fields; on an ADLS Gen2 or
  S3 suite, **Browse files…** (single file) or **Browse folders…** (batch prefix) walks the
  connection's container or bucket. Each level is one bounded listing (up to 200 names) and
  says so when there are more; a name DataQ cannot target is shown but not pickable. Nothing
  is saved until you save the suite, and the typed fields keep working exactly as before.
  Needs the Member role, like testing a connection.

- **Offboarding is one guided pass.** Admin → Members → **Offboard** hands a departing
  member's suites to somebody else, revokes every API key and browser session they hold,
  and withdraws their membership — in a single transaction, so a half-finished departure
  is not a state the workspace can end up in. A preview says what will be touched before
  anything is typed, the last Admin is refused, and the confirmation is the member's own
  address typed out. Their authored history is kept. Where an environment allowlist would
  still admit the address, the step is skipped and the variable named rather than a
  withdrawal being reported that did not happen. See the
  [admin control centre guide](../guides/admin.md).

- **Health-score weights are a workspace setting.** Admin → Settings → Scoring changes the
  penalty each severity tier carries (`warn` / `fail` / `critical`; defaults 0.5 / 1.0 / 2.0)
  and every change is audited. Scores are computed on read, so a change recolours every score
  at once, past and present — the audit log is where the step is explained. See the
  [admin control centre guide](../guides/admin.md).

- **Zero-sample mode is a workspace setting.** Admin → Settings → Privacy & failing samples
  turns it on without a restart; the environment variable stays the floor and cannot be
  turned off from the app. `GET /admin/deployment` now says which of the two is in force.

### Changed

- ⚠️ **Suites alert through admin-configured channels only.** The per-suite Notifications
  panel no longer takes a Teams or Slack webhook URL or a recipient list: it offers the
  channels a workspace Admin created under Settings, and that is all. Existing inline
  destinations keep delivering and are shown as a *Legacy inline destinations* card with a
  Clear per entry. On the API, setting one is refused for every caller (the field is
  named); clearing stays open to anyone with edit access.

- ⚠️ **Every DataQ user signs in.** The local stack has one sign-in mode, emailed
  codes, and a mis-set or empty sign-in configuration now stops the API with a message
  naming it instead of coming up open. Existing local setups: an `.env` with an empty
  `DATAQ_SIGNIN_EMAIL=` no longer boots — set an address or re-run `setup.sh`, which
  now re-asks.

- **The admin area is now six routed, deep-linkable pages.** `/admin` splits into
  `overview`, `members`, `suites`, `settings`, `compliance` and `integrations` —
  the tab you are on is the URL, so any tab can be bookmarked, shared or
  reloaded in place, and each page loads only its own data instead of one long
  scroll fetching everything. The standalone workspace **Settings** page folds
  in: `/settings` redirects to `/admin/settings`, and the sidebar carries a
  single **Admin** entry instead of two links to the same area. Every admin
  route is gated at the route, so a deep link (or a demoted user's bookmark)
  gets the Forbidden page and fetches nothing. See the
  [admin control centre guide](../guides/admin.md).

### Added

- **Admin → Integrations is an operations page.** Regenerate any provider's webhook secret
  or signing key (shown once; the previous value keeps working for a short grace window),
  see per-connection polling health with **Poll all now**, and turn warehouse inventory
  sync on or off per connection with **Run now**.
- **Admins can now operate the workspace, not just observe it.** **Admin → Members**
  gains **Revoke** on any per-suite access grant — previously only a suite's own owner
  could remove a share, so cleaning up after a departure meant first being granted access
  to every suite. **Admin → Suites** gains **Transfer**, the offboarding primitive: a suite
  moves to a new owner, who gets full control, while the previous owner keeps an editor
  grant unless you clear the checkbox (workspace viewers cannot own a suite and are not
  offered). It also gains **Delete** for any suite, behind a confirmation that states the
  exact number of checks, runs, results, schedules and trigger bindings the cascade would
  destroy and requires the suite's name to be typed. All three are audited with the
  admin-override recorded, and the delete's event carries the counts. See the
  [admin control centre guide](../guides/admin.md).

- **Workspace membership is managed in the app.** **Admin → Members** gains an
  **Add member** dialog (email plus an optional initial role) and per-row removal,
  so admitting or removing somebody no longer means editing deployment config and
  restarting. Removal takes effect on that person's **next request** for every
  credential kind — an identity-provider sign-in, a live browser session, and every
  API key they hold — which closes a gap where a departed member's API key kept
  working indefinitely. Adding a member does not create an account at your identity
  provider; that stays a prerequisite, and the dialog says so.

    ⚠️ **Adding the first member turns enforcement on for the whole workspace.**
    Until then nothing changes: who may sign in is decided entirely by your existing
    allowlist settings. The first add also admits every existing user in the same
    transaction, so nobody signed in is evicted — those rows are flagged under a
    **review imported members** banner to confirm or remove, because a user record
    proves somebody signed in once, not that they still belong. The allowlist
    settings stay available as grant-only break-glass. See the
    [admin control centre guide](../guides/admin.md).
- **Admin → Overview is now a workspace-health page.** Four counts — members, suites
  (and the distinct connections they target), open incidents with the acknowledged
  subset, and today's runs by status over the UTC day — above a **needs-attention**
  feed and a **workspace-health** checklist covering the audit chain, the scheduler
  heartbeat and queue depth, the orphan-secret sweep (with a report-only **Run sweep**),
  and orchestration polling. Every row links to the thing that fixes it. A signal that
  could not be read, or that has genuinely observed nothing — a connection never polled,
  a heartbeat that has never ticked, an unreachable broker, a sweep that has never run —
  renders as **unknown** or **not monitored** with the reason, never as a zero, a green
  tick, or a missing row. See the [admin control centre guide](../guides/admin.md).

- **Four admin capabilities that had no UI now have one.** On **Admin → Compliance**:
  audit-chain verification behind an explicit **Verify now** (it reads the whole hashed
  set, so it never runs on page load) reporting intact / broken-at-an-event / nothing-to-
  verify / not-verified as four distinct answers, plus the legacy-row count and whether an
  external anchor exists; and the **data-subject rights** tools — GDPR Art 15/20 export
  and Art 17 (CCPA delete) erasure over the samples DataQ has captured, with erasure gated
  on retyping the subject value exactly and both actions producing an on-screen receipt.
  On **Admin → Settings**, the email pre-flight result now stays on the card with the
  failing transport stage and the request ID instead of passing by in a toast. On
  **Admin → Integrations**, each webhook row states its auth mode, so it is obvious which
  URLs are themselves credentials. See the
  [admin control centre guide](../guides/admin.md) and the
  [data-subject-rights runbook](../security/compliance/data-subject-rights-runbook.md).
