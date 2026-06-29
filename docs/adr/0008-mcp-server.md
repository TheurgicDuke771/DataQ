# ADR 0008 — FastMCP server mounted at `/mcp`, Azure AD token-validated, all-tools

- **Status:** Accepted
- **Date:** 2026-06-29
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0003](0003-gx-only-for-v1.md) (unified suite/check/result model the tools read), [0010](0010-provider-agnostic-infrastructure-seams.md) / [0013](0013-marketplace-distribution-and-anti-lock-in.md) (generic `get_current_user`; no Azure claim-reading in business logic), CLAUDE.md §10 (MCP tool descriptions are LLM-facing)

## Context

Week 7 calls for a FastMCP server exposing 8 curated tools at `/mcp`, reachable from Claude Desktop / Claude.ai / Copilot / Cursor. Three design questions had to be settled against the *installed* library (`fastmcp` v3, not the v2 API the roadmap snippet assumed):

1. **How to mount** into the existing FastAPI app.
2. **How to authenticate** — reusing the same Azure AD bearer token the web UI already carries, not a second login.
3. **Tools vs resources** for the 4 read operations the roadmap labelled "resource".

## Decision

**Mount** — `mcp.http_app(path="/")` returns an ASGI app mounted at `/mcp`. Its streamable-http session manager needs its lifespan run, so the app's own startup is combined with it via `combine_lifespans(lifespan, mcp_app.lifespan)` (fastmcp's documented FastAPI pattern). The roadmap's `get_asgi_app()` is a stale v2 name.

**Auth** — a fastmcp `JWTVerifier` configured from the *same* tenant / audience / scope as `core.auth`: Azure JWKS (`https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys`), issuer (`…/v2.0`), audience = the API app's client id, required scope = `azure_api_scope`. This validates the identical token the REST API accepts, without depending on `fastapi-azure-auth` internals (a Starlette-request-bound dependency that can't verify a raw token string). The full OAuth `AzureProvider` was rejected — it drives an authorization-code flow and needs a client secret; clients already hold a token. Inside a tool the validated claims (`oid`, `preferred_username`, `name`) resolve + upsert the `User` via the shared `core.auth._upsert_user`, so the row is identical to a web-UI login and queries scope by the generic user id (no Azure claim read in service code — ADR 0010/0013).

**Two modes + fail-closed** — real Azure mode uses the `JWTVerifier`; local **dev-bypass** (`ENVIRONMENT=dev` + `AUTH_DEV_BYPASS=true`, no Azure vars) mounts unauthenticated and resolves the fixed dev user, exactly like the REST API. If **neither** is configured the server is **not mounted at all** — `/mcp` never goes live without auth (CLAUDE.md §10 security note).

**All 8 are MCP `tools`, not `resources`** — despite the roadmap labelling 4 as "resource". An LLM client invokes *tools* from natural language; fastmcp resource-templates with required arguments aren't reliably auto-called. The acceptance bar is "Claude answers the canonical NL queries" (`what failed today?` / `run the orders suite on DEV` / `why did the customer pipeline fail?` / `add a null check on email`), which is best served by tools. Read tools (`list_suites`, `get_suite_results`, `get_health_score`, `get_adf_pipeline_status`) + action tools (`trigger_suite_run`, `get_run_status`, `create_check`, `profile_column`).

**No logic duplication** — each tool is a thin wrapper: open a session → resolve the caller → call the same service function with the same `require_permission` / `accessible_suite_ids` authz the REST routers use → return an LLM-shaped dict. `get_suite_results` reuses `run_service.redact_sample_failures` so failing-row PII is masked exactly as in the REST results path (#226/#415).

## Consequences

- The MCP surface inherits per-suite sharing, existence-hiding, and sample redaction for free — there is one authz + redaction implementation, not two.
- The `JWTVerifier` audience assumes a v2 token whose `aud` is the API client id (the single-tenant config `core.auth` uses); the deferred "test end-to-end with Claude Desktop" task validates this against a live token, since it needs the deployed tenant.
- Tool bodies remain plain, directly-callable functions (the `@mcp.tool` decorator returns the function unchanged), so they're unit-tested by calling them with a test session + a stub user — no MCP transport needed.
- The roadmap's resource/tool split is superseded here; the progress ledger's "Resource: X" items are delivered as tools.

## Alternatives considered

- **`AzureProvider` (full OAuth)** — rejected: needs a client secret and an auth-code/redirect flow; clients already present a token.
- **Bridge to `fastapi-azure-auth`** by faking a Starlette `Request` — rejected as brittle coupling to that library's request-bound internals; `JWTVerifier` is the clean, documented path and validates the same token.
- **Resources for the reads** — rejected for LLM invocability (above); revisit if a client surfaces resources usefully.
