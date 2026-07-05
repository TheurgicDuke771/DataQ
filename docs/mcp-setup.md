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

The endpoint accepts the **same credentials as the REST API** (ADR [0008](adr/0008-mcp-server.md) / [0026](adr/0026-auth-api-keys-and-principal-seam.md)): an Azure AD bearer token, or a **DataQ API key** (`dq_live_…`). Without auth configured, the endpoint is only mounted in local dev-bypass mode — never unauthenticated in a deployed environment.

### Getting a token

**Recommended — a DataQ API key (PAT):** mint one via `POST /api/v1/me/api-keys`
(see [API keys](api-keys.md)) and use it as the bearer. It lives up to a year,
is revocable per-integration, and runs with exactly your per-suite access —
built for always-on MCP configs.

**Quick one-off — your web session's Azure token:** sign in to the DataQ web
app, open your browser's developer tools → **Application → Session Storage** →
the `oidc.user:…` entry → copy the `access_token` value.

!!! note "Azure tokens expire after ~1 hour"
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

## The 8 tools

Each tool is a thin wrapper over the same service layer as the REST API — per-suite authorization and failing-sample redaction apply identically.

| Tool | What it answers |
|---|---|
| `list_suites` | "What suites can I see?" — id, datasource, env, check count, last run |
| `get_suite_results` | "What failed in suite X?" — latest run's per-check outcomes |
| `get_health_score` | "How healthy is data quality overall?" — score, pass rate, trend |
| `get_adf_pipeline_status` | "Why did pipeline Y fail?" — recent orchestrator runs + correlated DQ run |
| `trigger_suite_run` | "Run the orders suite" — dispatches a run, returns the run id |
| `get_run_status` | "Is it done?" — live status + per-check progress |
| `create_check` | "Add a null check on email" — authors a check on a suite you can edit |
| `profile_column` | "Profile the qty column" — live null/distinct/min/max/top-values stats |

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
| Server absent / connection refused locally | In a deployed environment the MCP server is unmounted unless Azure auth is configured (fail-closed by design). |
| Tool call returns "not found" for a suite you can see in the UI as someone else | MCP calls run as the token's user — suite access is per-user, same as the web app. |
