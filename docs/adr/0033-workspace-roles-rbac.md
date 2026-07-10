# ADR 0033 — Workspace roles: Admin / Member / Viewer RBAC on the two-axis model

- **Status:** Proposed
- **Date:** 2026-07-10
- **Deciders:** @TheurgicDuke771
- **Amends (on acceptance):** ADR [0027](0027-suite-permission-model-workspace-admin.md) — the workspace-admin *source* moves from the `WORKSPACE_ADMIN_EMAILS` env allowlist to a stored `users.role`; the implicit-suite-admin rule itself is unchanged, and the grant model gains one rule (no `edit` shares to Viewers). The inline amendment blockquote lands in 0027 when this ADR flips to Accepted.
- **Related:** ADR [0026](0026-auth-api-keys-and-principal-seam.md) (richer-principals direction; PAT-inherits-user preserved), [0032](0032-email-otp-signin.md) (OTP signup gains a default role), [0010](0010-provider-agnostic-infrastructure-seams.md) (generic identity attributes only — roles are DataQ-stored, no IdP claims read)
- **Issue:** umbrella [#744](https://github.com/TheurgicDuke771/DataQ/issues/744) → slices #740 (role model) · #741 (enforcement) · #742 (management) · #743 (frontend). **Slices are blocked on this ADR's ratification** (Proposed → Accepted).

## Context

Authorization today is two axes with one axis degenerate. The fine axis works: every suite-scoped endpoint (REST and MCP identically) gates through `require_permission` on the `view < edit < admin < owner` ladder (ADR 0027). The coarse axis is a binary env allowlist: `WORKSPACE_ADMIN_EMAILS` makes you workspace-admin; everyone else is an undifferentiated "user". Consequences recorded as gap **G-e** ("config-allowlist admin, one validated IdP — fine internally, not sellable"): admins are managed by env-edit + restart; there is no read-only participant tier; and the largest unscoped hole — **connections are workspace-global**, so any authenticated user (even one holding only `view` shares) can delete or re-credential the Snowflake connection every suite runs on (`connection_service` has no ownership gate; `created_by` is display-only).

What this ADR is *not*: it does not touch the per-suite ladder, build groups/teams/custom roles, or add per-connection ACLs. The suite ladder remains the fine-grained axis; this ADR formalizes the coarse one.

## Decision

1. **Three stored workspace roles — `admin | member | viewer` — as a `users.role` column** (server-default `member`, CHECK-constrained; one additive migration, no roles table). Named **Viewer, not Guest**: `AZURE_ALLOW_GUEST_USERS` already means Entra B2B guests, and two "guest" concepts would collide in config and docs.
2. **The two axes compose; neither replaces the other.** Workspace role says what *kind* of user you are; per-suite grants say what you can *touch*. A Member with no share on a suite still cannot see it (404 existence-hiding unchanged). Workspace-admin remains implicit `admin` on every suite exactly per ADR 0027 — only its source moves to `users.role`.
3. **Capability matrix (the normative table):**

| Capability | Admin | Member | Viewer |
|---|---|---|---|
| See/use suites shared to them | ✅ | ✅ | ✅ (view only) |
| Create/import suites (become owner) | ✅ | ✅ | ❌ |
| Receive `edit` shares | ✅ | ✅ | ❌ — capped at `view` |
| Connections: mutate (create/edit/delete/re-auth) | ✅ | ❌ | ❌ |
| Connections: list/reference in suites | ✅ | ✅ | list only |
| Connections: `test` | ✅ | ✅ | ❌ |
| Mint PATs (token inherits the user, ADR 0026) | ✅ | ✅ | ✅ |
| `/admin` endpoints, implicit suite-admin, workspace-wide visibility | ✅ | ❌ | ❌ |
| Manage roles in-app | ✅ | ❌ | ❌ |

4. **Connection mutations become Admin-only** — the load-bearing row. Connections are shared infrastructure holding credentials; Members consume them, Admins manage them. This is a **breaking change for Members** who managed connections (promote them before upgrading — release-notes obligation in #741). Credentials stay unreadable through every tier; per-connection ACLs/ownership stay deferred and can layer on later without conflicting with this gate.
5. **Viewer is capped belt-and-braces**: granting `edit` to a Viewer is rejected at grant time, AND `effective_permission` caps a Viewer's resolved level at `view` (covers legacy rows and demote-after-grant). Suite creation requires Member+.
6. **`WORKSPACE_ADMIN_EMAILS` demotes to bootstrap + break-glass.** On user upsert, an allowlisted email resolves to `admin` (write-through), so existing deployments upgrade with zero config change; the env path also recovers a workspace whose last admin left. `is_workspace_admin()` = stored role OR allowlist.
7. **In-app management with a last-admin guard**: `PATCH /admin/users/{id}/role` (admin-gated), demoting/deleting the final admin is rejected, every role change emits a structured audit log line (actor, target, old→new; the durable audit *table* remains G-d/#431 scope). Roles resolve per request, so a change — including for the target's PATs, which are their user — takes effect on their next request with no session machinery.
8. **ADR 0032 interplay**: OTP signups get `AUTH_OTP_DEFAULT_ROLE` (default `member`; set `viewer` for cautious domain-wide allowlists).

## Consequences

**Positive** — G-e's "config-allowlist admin" objection is answered with one column and two small gates; the connection-deletion hole closes; a safe read-only tier exists for stakeholders; enforcement stays in the existing seams (`require_permission`, a new `require_role` mirroring `require_workspace_admin`), so REST/MCP can't drift; groups, custom roles, and per-connection ACLs all layer on later without schema rewrite.

**Negative / accepted** — Members lose connection-write (breaking; migration note + promote-first guidance in #741); a fixed three-role enum won't satisfy enterprises wanting custom roles (deliberately deferred); role changes are audit-*logged* but not yet audit-*tabled* (G-d); the allowlist break-glass means an env-level actor can always mint an admin — unchanged from today, now documented.

## Alternatives considered

- **Custom roles / permission-matrix tables** — rejected for now: single-tenant, small-team product; a fixed enum covers the sellability gap and the matrix can be introduced later behind the same `require_role` seam.
- **External policy engine (OPA / Casbin / SpiceDB)** — rejected: heavy operational dependency for three roles and one resource ladder; nothing in the current model needs relationship-graph evaluation.
- **Folding the suite ladder into roles** (role decides everything) — rejected: loses per-resource control; a Member must not see unshared suites.
- **Per-connection ACLs now** — rejected: Admin-only mutation closes the actual hole with one rule; ownership semantics (who may grant, what happens on owner deletion) deserve their own decision when multi-team demand is real.
- **"Guest" naming** — rejected: collides with Entra B2B guest terminology already present in config (`AZURE_ALLOW_GUEST_USERS`).
- **Keep the env allowlist as the only admin source** — rejected: that *is* G-e; but it survives as bootstrap/break-glass rather than being removed.
