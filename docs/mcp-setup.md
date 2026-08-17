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
    all 33 tools and per-suite permissions. Two rejections are deliberate in that
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

## The 33 tools

Each tool is a thin wrapper over the same service layer as the REST API — per-suite
authorization (`view` for a read, `edit` for a mutation) and failing-sample redaction apply
identically. The 30 split three ways, not two:

- **16 read-only** — `export_suite`, `get_adf_pipeline_status`, `get_check`, `get_check_history`,
  `get_health_score`, `get_notification_config`, `get_run_results`, `get_run_status`,
  `get_suite_performance`, `get_suite_results`, `list_checks`, `list_connections`, `list_runs`,
  `list_schedules`, `list_suites`, `list_trigger_bindings`.
- **10 that change state** — `create_check`, `update_check`, `delete_check`, `snooze_check`,
  `trigger_suite_run`, `cancel_run`, `create_schedule`, `delete_schedule`,
  `create_trigger_binding`, `import_suite`. All gate on `edit` access to the affected suite
  (`create_trigger_binding`, `create_schedule` and friends via the suite they target); `import_suite`
  additionally requires the **member** workspace role, since it has no existing suite to gate on.
- **4 that persist nothing but open a live datasource connection using stored credentials** —
  `profile_column`, `dryrun_check`, `suggest_column_policy`, `test_connection`. These are gated
  like writes, not like reads, because they spend a real credential against a remote system even
  though nothing is saved: the first three require `edit` on the suite whose connection they
  probe, and `test_connection` (which has no suite at all) requires the **member** workspace role.

No MCP tool is Admin-only. Every Admin-only capability in ADR 0033's authorization matrix is a
connection *mutation* (create/edit/delete/re-auth), and none of those are exposed here at all —
a credential must never transit an LLM. `test_connection` reports only whether a live probe
succeeded; it never returns a credential or a secret reference (Tier 1 + Tier 2 expansion, issue
[#529](https://github.com/TheurgicDuke771/DataQ/issues/529)).

### Suites & results

| Tool | What it answers |
|---|---|
| `list_suites` | "What suites can I see?" — id, datasource, env, check count, last run |
| `get_suite_results` | "What failed in suite X?" — latest run's per-check outcomes |
| `get_suite_performance` | "Which suites are in the worst shape?" — a worst-first health ranking |
| `get_health_score` | "How healthy is data quality overall?" — score, pass rate, trend |
| `update_suite` | "Point the orders suite at ANALYTICS.ORDERS_V2" — renames a suite or sets **what it runs against**; an imported suite has no target and cannot run until this sets one. Returns `runnable` |
| `export_suite` | "Show me the whole orders suite" — every check's definition as one portable document |

### Checks

| Tool | What it answers |
|---|---|
| `list_checks` | "What does the orders suite actually check?" — every check's config, kind, dimension |
| `get_check` | "What is this check actually asserting?" — one check's full definition |
| `get_check_history` | "Has the row-count check been flaky?" — recent result history for one check |
| `create_check` | "Add a null check on email" — authors a check on a suite you can edit |
| `update_check` | "Loosen the null check on email to warn at 2%" — a partial update; `config` replaces the whole configuration rather than merging into it |
| `delete_check` | "Remove the row-count check from orders" — permanently deletes a check **and every result it ever recorded**; prefer `snooze_check` to just stop alerting |
| `snooze_check` | "Stop alerting on the freshness check until tomorrow" (or, with no duration, "turn alerts back on") — mutes alerts only; the check still runs and still fails |
| `dryrun_check` | "Would a not-null check on email pass right now?" — previews a check against live data without saving anything |

### Runs & profiling

| Tool | What it answers |
|---|---|
| `list_runs` | "What has run today?" / "show me the failed runs" |
| `get_run_results` | "Why did last night's orders run fail?" — a specific historical run's per-check results |
| `get_run_status` | "Is it done?" — live status + per-check progress |
| `trigger_suite_run` | "Run the orders suite" — dispatches a run, returns the run id |
| `cancel_run` | "Stop the orders run, I triggered the wrong suite" — cancels a queued or still-running run; cooperative, so a fast run may finish first |
| `profile_column` | "Profile the qty column" — live null/distinct/min/max/top-values stats |
| `get_column_policy` | "Is the email column masked in failure samples?" — the suite's redaction policy; an empty one means no suite-level override, **not** that nothing is masked |
| `set_column_policy` | "Mask the email column in failure samples" — applies what `suggest_column_policy` proposed; replaces the whole policy |
| `suggest_column_policy` | "Which columns here are sensitive?" — suggests (never saves) a PII redaction policy by profiling the suite's target live |

### Connections & orchestration

| Tool | What it answers |
|---|---|
| `list_connections` | "What are we connected to?" / "which connections are broken?" — names, types and health **only**, never config or secrets |
| `test_connection` | "Is the Snowflake connection working?" — opens a live connection with the stored credential and reports success or a classified failure; never returns a credential |
| `get_adf_pipeline_status` | "Why did pipeline Y fail?" — recent orchestrator (ADF/Airflow/dbt) runs + correlated DQ run |
| `list_trigger_bindings` | "What runs after the nightly load?" — which pipeline/DAG successes trigger which suite |
| `create_trigger_binding` | "Run the orders checks after the nightly load finishes" — binds a pipeline/DAG success in a given `env` to a suite; only success triggers a run |

### Scheduling & alerting

| Tool | What it answers |
|---|---|
| `list_schedules` | "When does the orders suite run?" — cron schedules + next fire time |
| `create_schedule` | "Run the orders suite every night at 2am" — returns the resolved `next_run_at` so you can confirm the interpretation, or `null` when created disabled (a disabled schedule does not fire at all) |
| `delete_schedule` | "Stop the nightly orders run" — removes the schedule; the suite and its checks are untouched |
| `get_notification_config` | "Who gets told when orders fails?" — channel presence (Teams/Slack/email), never webhook URLs |

### Suite portability

| Tool | What it answers |
|---|---|
| `import_suite` | "Recreate the orders suite against the QA warehouse" — creates a whole new suite from an `export_suite` document. Requires the **member** workspace role (it creates a suite, so there is no existing suite to gate on); never merges into an existing suite |

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
