# AI assistants (MCP setup)

DataQ ships a built-in [MCP](https://modelcontextprotocol.io) server so AI assistants — Claude Desktop, Claude.ai, VS Code / GitHub Copilot, Cursor — can answer questions like *"what failed today?"* or *"run the orders suite"* against your live DataQ instance, with the same per-suite permissions as the signed-in user.

## Endpoint & authentication

The server is mounted on the deployed app at:

```text
https://<your-dataq-host>/mcp/
```

!!! warning "Keep the trailing slash"
    `/mcp` answers with a **307 redirect** to `/mcp/`, and some HTTP clients drop the
    `Authorization` header when following redirects — which then surfaces as a
    confusing 401. Always configure clients with the `/mcp/` form.

The endpoint accepts the **same credentials as the REST API** (ADR [0008](adr/0008-mcp-server.md) / [0026](adr/0026-auth-api-keys-and-principal-seam.md)): an OIDC bearer token (Azure AD or Cognito), or a **DataQ API key** (`dq_live_…`). Without auth configured, the endpoint is only mounted in local dev-bypass mode — never unauthenticated in a deployed environment.

!!! info "Email-OTP deployments: MCP works, with an API key"
    A deployment running **email one-time codes instead of SSO** (ADR
    [0032](adr/0032-email-otp-signin.md)) has no identity provider to issue bearer
    tokens, so an **API key is the only `/mcp` credential** there — mint one as
    below and use it exactly the same way. Everything else is identical, including
    all 48 tools and per-suite permissions. Two rejections are deliberate in that
    mode: a raw JWT is refused (there is nothing to validate it against), and your
    **sign-in session is never accepted** — it is a browser credential and does not
    authenticate `/mcp`, whether presented as a bearer or carried as a cookie.

### Getting a token

**Recommended — a DataQ API key (PAT):** mint one via `POST /api/v1/me/api-keys`
(see [API keys](api-keys.md)) and use it as the bearer. It lives up to a year,
is revocable per-integration, and runs with exactly your per-suite access —
built for always-on MCP configs.

**Quick one-off — your web session's OIDC token:** sign in to the DataQ web
app, open your browser's developer tools → **Application → Session Storage** →
the `oidc.user:…` entry → copy the `access_token` value.

!!! note "OIDC tokens expire after ~1 hour"
    The pasted browser token is short-lived; when the client starts getting
    401s, paste a fresh one and restart the MCP server/connection — or switch
    to an [API key](api-keys.md) and stop re-pasting.

!!! danger "Never commit a config that carries a token"
    Keep token-bearing MCP config files out of version control (in the DataQ repo
    itself, `.gitignore` already covers `.vscode/*`).

## Client configuration

**Claude Desktop / Claude.ai** (`claude_desktop_config.json`) — and **GitHub Copilot** (`mcp.json`):

```jsonc
{
  "mcpServers": {
    "dataq": {
      "url": "https://<your-dataq-host>/mcp/",
      "headers": { "Authorization": "Bearer <AZURE_AD_ACCESS_TOKEN>" }
    }
  }
}
```

**VS Code** (workspace `.vscode/mcp.json`, used by Copilot agent mode) uses a `servers` key — not `mcpServers` — plus an explicit `type`:

```jsonc
{
  "servers": {
    "dataq": {
      "type": "http",
      "url": "https://<your-dataq-host>/mcp/",
      "headers": { "Authorization": "Bearer <AZURE_AD_ACCESS_TOKEN>" }
    }
  }
}
```

Start it via the command palette (`Cmd/Ctrl+Shift+P`) → **MCP: List Servers** → *dataq* → Start (or open Copilot Chat in agent mode — configured servers start on demand).

**Cursor** (`~/.cursor/mcp.json`) uses the same `mcpServers` shape as Claude Desktop.

## The 48 tools

Each tool is a thin wrapper over the same service layer as the REST API — per-suite
authorization (`view` for a read, `edit` for a mutation) and failing-sample redaction apply
identically. The 48 split three ways, not two (one of the 25 reads, `get_adf_pipeline_status`,
is a deprecated alias kept only for backward compatibility — see below); the counts here are
derived from `tests/support/mcp_gates.GATES`, not hand-typed, so they can't drift the way an
earlier pass of this page did.

- **25 read-only** — `export_suite`, `get_adf_pipeline_status` (deprecated, use
  `get_pipeline_status`), `get_asset`, `get_check`, `get_check_history`, `get_column_policy`,
  `get_doc`, `get_health_score`, `get_incident`, `get_near_misses`, `get_notification_config`,
  `get_pipeline_status`, `get_run_results`, `get_run_status`, `get_suite_performance`,
  `get_suite_results`, `list_assets`, `list_check_versions`, `list_checks`, `list_connections`,
  `list_incidents`, `list_runs`, `list_schedules`, `list_suites`, `list_trigger_bindings`.
- **18 that change state** — `create_check`, `update_check`, `delete_check`, `snooze_check`,
  `restore_check_version`, `trigger_suite_run`, `cancel_run`, `update_suite`, `set_column_policy`,
  `create_schedule`, `update_schedule`, `delete_schedule`, `create_trigger_binding`,
  `update_trigger_binding`, `delete_trigger_binding`, `ack_incident`, `resolve_incident`,
  `import_suite`. All gate on `edit` access to the affected suite (the schedule, binding and
  incident tools via the suite they target); `import_suite` additionally requires the **member**
  workspace role, since it has no existing suite to gate on.
- **5 that persist nothing but open a live datasource connection using stored credentials** —
  `profile_column`, `list_columns`, `dryrun_check`, `suggest_column_policy`, `test_connection`.
  These are gated like writes, not like reads, because they spend a real credential against a
  remote system even though nothing is saved: the first four require `edit` on the suite whose
  connection they probe, and `test_connection` (which has no suite at all) requires the **member**
  workspace role.

`get_adf_pipeline_status` predates dbt (ADR 0029) and Airflow support and was never ADF-only
despite the name; `get_pipeline_status` is the current name and states its actual scope. The old
name stays registered — a client with it pinned in a saved prompt or static config keeps
working — and delegates to `get_pipeline_status` with identical behavior. New integrations
should use `get_pipeline_status`.

No MCP tool is Admin-only. Every Admin-only capability in ADR 0033's authorization matrix is a
connection *mutation* (create/edit/delete/re-auth), and none of those are exposed here at all —
a credential must never transit an LLM. `test_connection` reports only whether a live probe
succeeded; it never returns a credential or a secret reference.

### Reading the results honestly

A REST caller wrote their own query and a UI user reads the screen — the row
count, the timestamps, the "running" badge, the filter they chose. An AI client
has neither, so several tools return **fields whose whole job is to say what the
answer does not cover**. A well-behaved client should branch on these rather
than summarising the payload as-is.

| Field | Appears on | What it prevents |
|---|---|---|
| `total` · `returned` · `truncated` | `list_runs`, `list_checks`, `list_check_versions`, `list_incidents`, `list_assets`, `get_check_history`, `get_pipeline_status` | reporting one page as the whole set. `truncated` is computed against a real total, never inferred from page length. The unpaged tools — `list_suites`, `list_connections`, `list_schedules`, `list_trigger_bindings`, `get_near_misses` — return every row and carry no page fields at all |
| `oldest_in_page` · `newest_in_page` | `list_runs`, `list_incidents`, `get_check_history`, `get_pipeline_status` | answering a time-bounded question ("what failed today?") from a **count**-capped page. `list_runs`/`list_incidents` now take `since_hours`/`until_hours`; `get_check_history`/`get_pipeline_status` still have no time filter — these fields say what window you actually saw regardless |
| `results_final` | `list_runs`, `get_run_results`, `get_run_status`, `get_suite_results` | reading a mid-run partial as a verdict. A 30-check suite three checks in genuinely has "3/3 passed" — and no result yet |
| `redaction` · `redacted_columns` | per-check results | describing masked rows as "no failing rows", or mask tokens as data |
| `sampling` · `sampled` · `sample_row_limit` | results, `profile_column` | stating a sample statistic as a fact about the full dataset |
| `runnable` | `update_suite`, `import_suite` | reporting a suite as ready when it has no run target and cannot run |
| `is_recurrence` · `prior_incident_id` | incidents | describing a recurring problem as brand new |
| `window_hours` | `get_near_misses` | quoting a default window on a deployment that changed it |
| `restricted_suite_count` | `get_asset` | presenting the suites you can see as the whole explanation for a workspace-wide health number |
| `column_policy_pending` · `column_policy_may_be_stale` | `update_suite` | leaving a redaction policy stranded after re-pointing a suite |

The same principle runs through the tool descriptions themselves: each states its
population (so an empty result is never read as "nothing exists"), whether it is a
snapshot or a live read, and what it structurally cannot see.

### Suites & results

| Tool | What it answers |
|---|---|
| `list_suites` | "What suites can I see?" — id, datasource, env, check count, last run |
| `get_suite_results` | "What failed in suite X?" — latest run's per-check outcomes |
| `get_suite_performance` | "Which suites are in the worst shape?" — a worst-first health ranking |
| `get_health_score` | "How healthy is data quality overall?" — score and pass rate over a window. Its `trend` is a per-day count of **runs** by lifecycle status, **not** a per-day score; a null score means nothing was evaluated, not zero |
| `update_suite` | "Point the orders suite at ANALYTICS.ORDERS_V2" — renames a suite or sets **what it runs against**; an imported suite has no target and cannot run until this sets one. Returns `runnable` |
| `export_suite` | "Show me the whole orders suite" — every check's definition as one portable document |

### Checks

| Tool | What it answers |
|---|---|
| `list_checks` | "What does the orders suite actually check?" — every check's config, kind, dimension |
| `get_check` | "What is this check actually asserting?" — one check's full definition |
| `get_check_history` | "Has the row-count check been flaky?" — recent *result* history for one check; for how its definition changed, use `list_check_versions` |
| `list_check_versions` | "Who changed this threshold?" — one check's *edit* history: every snapshot's config and thresholds as they were at that version |
| `create_check` | "Add a null check on email" — authors a check on a suite you can edit |
| `update_check` | "Loosen the null check on email to warn at 2%" — a partial update; `config` replaces the whole configuration rather than merging into it |
| `delete_check` | "Remove the row-count check from orders" — permanently deletes a check **and every result it ever recorded, plus every incident it raised, including open ones**; prefer `snooze_check` to just stop alerting |
| `snooze_check` | "Stop alerting on the freshness check until tomorrow" (or, with no duration, "turn alerts back on") — mutes alerts only; the check still runs, still fails, and still opens an incident. **Suppression is per _run_**: the alert is withheld only when every failing check in that run is snoozed |
| `restore_check_version` | "Undo that threshold change" — puts a check back to a snapshot from `list_check_versions`; additive (nothing is renumbered or deleted), and the only path that *clears* a field back to empty |
| `dryrun_check` | "Would a not-null check on email pass right now?" — previews a check against live data without saving anything. Reads what a real run would read, so on flat-file / Iceberg targets it inherits the run target's sampling |

### Runs & profiling

| Tool | What it answers |
|---|---|
| `list_runs` | "Show me the recent runs" / "find the run that failed". Takes `since_hours`/`until_hours` (relative "N hours ago" offsets) for time-bounded questions ("what ran today" = `since_hours=24`); without them the page is capped by **count**, and `oldest_in_page` / `newest_in_page` say what window you actually saw |
| `get_run_results` | "Why did last night's orders run fail?" — a specific historical run's per-check results |
| `get_run_status` | "Is it done?" — live status + per-check progress |
| `trigger_suite_run` | "Run the orders suite" — dispatches a run, returns the run id. **The environment and dataset cannot be chosen**: a run always uses the suite's own connection and target |
| `cancel_run` | "Stop the orders run, I triggered the wrong suite" — cancels a queued or still-running run; cooperative, so a fast run may finish first |
| `list_columns` | "What columns are on this table?" — names only, defaulting to the suite's own target. The cheap first step before authoring; guessing a column name produces a check that runs and errors |
| `profile_column` | "Profile the qty column" — live null/distinct/min/max/top-values stats. Snowflake and Unity Catalog are profiled in full; **ADLS, S3 and Iceberg are sampled to 100k rows**, and `sampled: true` means `row_count` is the sample size, not the table's. Values are returned **unredacted** |
| `get_column_policy` | "Is the email column masked in failure samples?" — the suite's redaction policy; an empty one means no suite-level override, **not** that nothing is masked |
| `set_column_policy` | "Mask the email column in failure samples" — applies what `suggest_column_policy` proposed; replaces the whole policy |
| `suggest_column_policy` | "Which columns here are sensitive?" — suggests (never saves) a PII redaction policy by profiling the suite's target live |

### Connections & orchestration

| Tool | What it answers |
|---|---|
| `list_connections` | "What are we connected to?" / "which connections are broken?" — names, types and health **only**, never config or secrets. `consecutive_run_failures` is non-zero only when *every* suite on the connection is failing, so a per-suite problem is invisible here |
| `test_connection` | "Is the Snowflake connection working?" — opens a live connection with the stored credential. A pass proves only that the credential authenticates and the datasource answers, **not** that a suite will run; a failure is deliberately unclassified (driver text can carry credential fragments) |
| `get_pipeline_status` | "Why did pipeline Y fail?" — recent orchestrator (ADF/Airflow/dbt) runs + correlated DQ run |
| `list_trigger_bindings` | "What runs after the nightly load?" — which pipeline/DAG successes trigger which suite |
| `create_trigger_binding` | "Run the orders checks after the nightly load finishes" — binds a pipeline/DAG success in a given `env` to a suite; only success triggers a run |
| `update_trigger_binding` | "Stop that trigger firing, but keep the wiring" — enables or disables a binding; what it points at is immutable, so re-targeting means delete + create |
| `delete_trigger_binding` | "Unhook the orders checks from the nightly load" — removes the binding; prefer `update_trigger_binding` if a pause is meant |
| `get_near_misses` | "The suite was supposed to run after the pipeline and it didn't" — pipelines that succeeded in one `env` while the only enabled binding is scoped to another, so the trigger is inert |

### Scheduling & alerting

| Tool | What it answers |
|---|---|
| `list_schedules` | "When does the orders suite run?" — cron schedules + next fire time |
| `create_schedule` | "Run the orders suite every night at 2am" — returns the resolved `next_run_at` so you can confirm the interpretation, or `null` when created disabled (a disabled schedule does not fire at all) |
| `update_schedule` | "Move the orders run to 3am" / "pause the nightly schedule" — partial update of cron, timezone or enabled. Resuming **re-bases**; it does not backfill runs missed while paused |
| `delete_schedule` | "Stop the nightly orders run" — removes the schedule; the suite and its checks are untouched. Prefer `update_schedule(enabled=false)` if a pause is meant |
| `get_notification_config` | "Who gets told when orders fails?" — channel presence (Teams/Slack/email), never webhook URLs |

### Assets & incidents

Assets are the grain people reason in ("is `orders` healthy?"); suites are the grain checks are
authored in. Incidents are the deduplicated, stateful roll-up of repeated failures.

| Tool | What it answers |
|---|---|
| `list_assets` | "What tables do we monitor?" / "which assets are unhealthy?" — every asset with its health. The numbers are **workspace-true** (ADR [0037](adr/0037-workspace-visible-asset-identity.md)): aggregated over every composing suite, including ones the caller cannot see, so they are not "your" checks |
| `get_asset` | "Is the orders table healthy, and what feeds it?" — the workspace-true summary + per-dimension scorecard + the composing suites the caller may view (`restricted_suite_count` counts the rest) + the lineage neighbourhood, qualified when a lineage source is failing or stale |
| `list_incidents` | "What's broken right now?" — open/acknowledged/resolved incidents, scoped to suites the caller can see, so an empty result means "nothing visible to you", not "nothing is wrong". Also takes `since_hours`/`until_hours`, filtered on `last_seen_at` (most recent breach, not when first opened). Incidents auto-resolve on the first passing result, so this answers "what's unresolved now", not "what failed during period X" — a resolved failure earlier in the window won't appear even under a time filter; use `list_runs` for that question |
| `get_incident` | "Why did this open, and what else broke at the time?" — the evidence card snapshotted at the last occurrence; carries no failing sample rows by design |
| `ack_incident` | "I'm on it" — records that someone owns the incident. Changes nothing about the data and does **not** stop alerts; use `snooze_check` for that |
| `resolve_incident` | "The backfill fixed it" — declares the problem over. Does not re-run anything, and the next failing run opens a **new** incident |

### Suite portability

| Tool | What it answers |
|---|---|
| `import_suite` | "Recreate the orders suite against the QA warehouse" — creates a whole new suite from an `export_suite` document. **Only the checks are copied**: the new suite has no run target (`runnable: false` — fix with `update_suite`), no schedules, no trigger bindings, no notification config, no column policy and no shares. Requires the **member** workspace role; never merges into an existing suite |

Try these natural-language queries once connected:

1. *"What data quality checks failed today?"*
2. *"Run the Retail Orders suite."* … *"Is it done?"*
3. *"Why did the ADF pipeline fail?"*
4. *"Add a not-null check on order_number in the Retail Orders suite."*

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| 401 on every request | Token expired (~1 h) → paste a fresh one. Or the client followed the `/mcp` → `/mcp/` redirect and dropped the header → use `/mcp/` directly. |
| 307 responses | Missing trailing slash — configure `/mcp/`. |
| Server absent / connection refused locally | The MCP server is unmounted unless the deployment has a working sign-in configuration — SSO (`AZURE_*`), email OTP (`AUTH_EMAIL_*` + an allowlist), or local dev-bypass (fail-closed by design). |
| 401 with an API key on an email-OTP deployment | Check you sent the **API key**, not your session cookie/token: in OTP mode a `dq_live_…` key is the only credential `/mcp` accepts. |
| Tool call returns "not found" for a suite you can see in the UI as someone else | MCP calls run as the token's user — suite access is per-user, same as the web app. |
