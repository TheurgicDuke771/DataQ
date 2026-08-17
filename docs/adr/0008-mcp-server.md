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

## Amendment — Tier 1 expansion to 19 tools (2026-08-17, issue [#529](https://github.com/TheurgicDuke771/DataQ/issues/529))

The original 8 tools were deliberately the smallest set that answered the roadmap's canonical
NL queries. [`context/post-v1-roadmap.md`](https://github.com/TheurgicDuke771/DataQ/blob/main/context/post-v1-roadmap.md) Theme 13 catalogued
the rest of the REST surface as MCP candidates, tiered by risk. This amendment ships **Tier 1 —
high-value safe reads** in full: `list_checks`, `get_check`, `get_check_history`, `list_runs`,
`get_run_results`, `list_connections`, `list_schedules`, `list_trigger_bindings`,
`get_notification_config`, `get_suite_performance`, `export_suite` — bringing the server to
**19 tools: 17 read-only, 2 mutating** (the original `trigger_suite_run` and `create_check`).

**The decision above is unchanged, not revisited.** Every new tool is the same thin wrapper this
ADR already describes — open a session, resolve the caller, call the same service function,
return an LLM-shaped dict. No new authz path was introduced; each tool reuses whichever gate its
REST counterpart already applies, which is deliberately **not** uniform:

- **Suite-scoped reads** (`list_checks`, `get_check`, `get_check_history`, `get_run_results`,
  `get_notification_config`, `export_suite`) gate on `suite_authz.require_permission(minimum="view")`,
  so ADR 0027 per-suite sharing and the ADR 0033 Viewer clamp apply exactly as on the original 8.
- **Suite-scoped lists** (`list_runs`, `list_schedules`, `list_trigger_bindings`) scope through
  `suite_service.accessible_suite_ids` — with the workspace-admin view where their REST route has
  it — and additionally call `require_permission` up front when the optional `suite_id` is given,
  so naming a suite you cannot see is an error rather than an empty list.
- **`get_suite_performance`** is scoped by the dashboard's own accessible-suite subquery.
- **`list_connections` calls neither**, because connections are workspace-scoped rather than
  suite-scoped — the same rule its REST route follows. That is why what it *returns* is
  constrained instead (below); the ADR 0033 role axis gates connection **mutations**, and MCP
  deliberately has none.

Three standing exclusions carried forward, made explicit because Tier 1 sits right next to them:

- **`list_connections` is workspace-scoped and returns metadata + health only** — id, name,
  type, env, whether a credential is stored, and health signals. It never returns a
  connection's configuration (account identifiers, hosts, paths) or a secret reference.
- **`get_notification_config` reports channel presence, never webhook URLs.** A webhook URL is
  itself a bearer credential; the tool answers "is Teams/Slack/email wired up, and from where"
  without ever resolving the secret.
- **Connection create/update/reauth remain excluded** — a credential must never transit an LLM.
  This was true before Tier 1 and stays true after it; no mutating connection tool exists.

**Tier 2** (mutating, edit-permission-gated: `dryrun_check`, `update_check`/`delete_check`,
`snooze_check`, `cancel_run`, schedule/trigger-binding CRUD, `import_suite`, `test_connection`,
etc. — see the roadmap's Theme 13 table) stays deferred; nothing in this amendment ships it.

## Amendment — Tier 2 expansion to 30 tools (issue [#529](https://github.com/TheurgicDuke771/DataQ/issues/529))

This amendment ships the **Tier 2** set deferred above, in full: `update_check`, `delete_check`,
`snooze_check`, `dryrun_check`, `cancel_run`, `create_schedule`, `delete_schedule`,
`create_trigger_binding`, `suggest_column_policy`, `test_connection`, `import_suite` — 11 new
tools, bringing the server to **30 tools total**. Theme 13 is now fully delivered.

**The decision above is still unchanged.** Every Tier 2 tool is the same thin wrapper — open a
session, resolve the caller, call the same service function, return an LLM-shaped dict — reusing
whichever gate its REST counterpart already applies.

**The 30 tools split three ways, and the split is deliberately not "read vs mutate":**

- **16 read-only** — **one fewer than the 17 the Tier 1 amendment above states.**
  `profile_column` was reclassified out of read-only when the gate table was built (#1418): it
  persists nothing, but it opens a live datasource with stored credentials and had always gated
  on `edit`. The behaviour did not change; the label was wrong, and a comment could not catch it.
- **10 that change state** — `create_check`, `update_check`, `delete_check`, `snooze_check`,
  `trigger_suite_run`, `cancel_run`, `create_schedule`, `delete_schedule`,
  `create_trigger_binding`, `import_suite`. All except `import_suite` gate on
  `suite_authz.require_permission` (`minimum="edit"`) against the suite they act on, so per-suite
  sharing (ADR 0027) and the ADR 0033 Viewer read-only clamp apply exactly as on every existing
  mutating tool. **`import_suite` is the exception**: it *creates* a suite, so there is no
  existing resource whose ladder could gate it — it takes the coarse `role:member` gate below.
- **4 that persist nothing but open a live datasource connection using stored credentials** —
  `profile_column`, `dryrun_check`, `suggest_column_policy`, `test_connection`. None of these
  write a row, but all four spend a real credential against a remote system, which is not a
  read-only action even though nothing is saved. `profile_column`, `dryrun_check` and
  `suggest_column_policy` are suite-scoped and gate on `require_permission(minimum="edit")` like
  a write. `test_connection` has no suite to gate on at all — a connection is workspace-scoped,
  not suite-scoped — so it gates on the **coarse** axis instead: `server._require_role(user,
  "member")`, the MCP-side twin of `core.auth.require_role` (ADR 0033), asserting the caller
  holds at least the `member` workspace role. `import_suite` gates the same way, for the
  symmetric reason: creating a suite has no *existing* suite to check permission against either.

**MCP exposes no admin-only tool at all.** Every Admin-only capability in ADR 0033's
authorization matrix is a connection *mutation* — create, edit, delete, re-auth — and none of
those are exposed here, before or after this amendment: a credential must never transit an LLM.
`test_connection` is the closest any tool comes to touching a connection, and it deliberately
stops at reporting whether the live probe succeeded; it never returns a credential or a secret
reference, matching the standing exclusions the Tier 1 amendment already recorded for
`list_connections` and `get_notification_config`.

The three standing exclusions from the Tier 1 amendment are unaffected and still hold in full.

## Amendment — Tier 3A coherence tools, 30 → 33 (2026-08-17, issue [#1424](https://github.com/TheurgicDuke771/DataQ/issues/1424))

Three tools that close **dead-ends in the existing surface** rather than adding reach:
`update_suite`, `get_column_policy`, `set_column_policy`. The split becomes
**33 tools: 17 read-only, 12 that change state, 4 live-probe**.

They exist because two shipped tools could not finish their own job:

- `import_suite` creates a suite with **no run target**, and `trigger_suite_run` fails fast
  without one — so an assistant could create a suite it had no way to make runnable.
  `update_suite` sets the target (validated through the same `SuiteTarget` model the REST route
  uses) and reports `runnable` explicitly. It also fires the same `dispatch_auto_classify` the
  REST route does (#634), so a suite made runnable here still derives a redaction policy.
- `suggest_column_policy` could propose a policy that nothing could read back or apply.

All three gate on `suite_authz.require_permission` against an existing suite, so no new authz
path is introduced and `tests/support/mcp_gates.GATES` picks them up in the four sweeps.

The exclusions are unchanged. `DELETE /suites/{id}` is now **explicitly** excluded as well: it
cascades every run and result the suite ever produced, and unlike `delete_check` there is no
lesser action to steer an assistant toward.

## Amendment — Tier 3A batch 2, 33 → 38 (2026-08-17, issue [#1424](https://github.com/TheurgicDuke771/DataQ/issues/1424))

Five more coherence tools, closing the last three **asymmetric-verb pairs** in the surface:
`update_schedule`, `update_trigger_binding`, `delete_trigger_binding`, `list_check_versions`,
`restore_check_version`. The split becomes **38 tools: 18 read-only, 16 that change state,
4 live-probe**.

Each pair was create-without-update, or create-without-delete, or a mutation with no way to
inspect or undo it:

- `create_schedule` + `delete_schedule` with no update meant "pause the nightly run" had only
  one available answer — *delete it* — which discards the cron expression the user would need to
  restore it. `update_schedule` makes pause a first-class, reversible action, and both tools now
  point at each other so the destructive one is not chosen by default.
- `create_trigger_binding` had neither a delete nor a disable, so an assistant could wire a
  trigger and then had no way to unwire it.
- `update_check` / `delete_check` snapshot every edit into `check_versions`, and none of that was
  readable over MCP. `list_check_versions` exposes the edit history — deliberately distinct from
  `get_check_history`'s *result* history, with both docstrings cross-referencing the other, since
  "did this start failing because the data moved or because someone changed the check?" needs
  both and the names are otherwise easy to confuse.
- `restore_check_version` is also the **only** path that can clear a field back to empty:
  `update_check`'s PATCH convention reads an omitted argument as "leave alone", so it structurally
  cannot. That correction was applied to `update_check`'s own docstring, which had said
  recreating the check was the only option.

All five gate through `require_permission` on the owning suite (the schedule and binding tools
resolve it from the row), so again no new authz path. The gate rows in
`tests/support/mcp_gates.GATES` drive the four sweeps as before — with one trap worth recording:
`restore_check_version` takes a `version_no`, and a probe check inserted directly rather than
through `check_service` has **no version rows**, so the tool raised "check version not found"
*before* reaching authz and the sweep passed with the gate deleted. That is the same vacuous-pass
shape `_REAL_RUN` and `_REAL_CHECK` were each added to close, one level deeper; the fix inserts a
real `CheckVersion` alongside the check. Every gate here was mutation-verified by removing it and
confirming the sweep goes red.

The exclusions are unchanged.
