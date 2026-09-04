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

The endpoint accepts the **same credentials as the REST API** (ADR [0008](../adr/0008-mcp-server.md) / [0026](../adr/0026-auth-api-keys-and-principal-seam.md)): an OIDC bearer token (Azure AD or Cognito), or a **DataQ API key** (`dq_live_…`). Without auth configured, the endpoint is only mounted in local dev-bypass mode — never unauthenticated in a deployed environment.

!!! info "Email-OTP deployments: MCP works, with an API key"
    A deployment running **email one-time codes instead of SSO** (ADR
    [0032](../adr/0032-email-otp-signin.md)) has no identity provider to issue bearer
    tokens, so an **API key is the only `/mcp` credential** there — mint one as
    below and use it exactly the same way. Everything else is identical, including
    all 50 tools and per-suite permissions. Two rejections are deliberate in that
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

## The 50 tools

Each tool is a thin wrapper over the same service layer as the REST API — per-suite
authorization (`view` for a read, `edit` for a mutation) and failing-sample redaction apply
identically. **The complete list — every tool, who can call it, and what it does — is the
[MCP tools reference](../reference/mcp-tools.md)**, generated from the server itself and
drift-checked in CI, so it cannot go stale the way an earlier hand-typed pass of this page did.

The tools split three ways, not two:

- **Read-only** (27) — reads gated on `view` where a suite is named; workspace-wide reads such as
  `list_suites` and `get_health_score` need only a signed-in user.
- **Changes state** (18) — every one gates on `edit` access to the affected suite (schedule,
  binding and incident tools via the suite they target); `import_suite` additionally requires
  the **member** workspace role, since it has no existing suite to gate on.
- **Live probes** (5) — `profile_column`, `list_columns`, `dryrun_check`,
  `suggest_column_policy`, `test_connection` persist nothing but open a live datasource
  connection with stored credentials. They are gated like writes, not reads, because they
  spend a real credential against a remote system: the first four require `edit` on the suite
  whose connection they probe, and `test_connection` (which has no suite) requires the
  **member** workspace role.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| 401 on every request | Token expired (~1 h) → paste a fresh one. Or the client followed the `/mcp` → `/mcp/` redirect and dropped the header → use `/mcp/` directly. |
| 307 responses | Missing trailing slash — configure `/mcp/`. |
| Server absent / connection refused locally | The MCP server is unmounted unless the deployment has a working sign-in configuration — SSO (`AZURE_*`), email OTP (`AUTH_EMAIL_*` + an allowlist), or local dev-bypass (fail-closed by design). |
| 401 with an API key on an email-OTP deployment | Check you sent the **API key**, not your session cookie/token: in OTP mode a `dq_live_…` key is the only credential `/mcp` accepts. |
| Tool call returns "not found" for a suite you can see in the UI as someone else | MCP calls run as the token's user — suite access is per-user, same as the web app. |
