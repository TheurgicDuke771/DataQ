# ADR 0043 — In-app workspace membership (`workspace_members`)

- **Status:** Proposed
- **Date:** 2026-09-04
- **Deciders:** @TheurgicDuke771
- **Amends:** ADR [0032](0032-email-otp-signin.md) — the env sign-up allowlist stops being the whole member list and becomes a starting seed plus an emergency way back in. ADR [0033](0033-workspace-roles-rbac.md) — the Admin page can now also decide *who is in*, not only what each person can do; no capability in its matrix changes.
- **Related:** ADR [0026](0026-auth-api-keys-and-principal-seam.md) (a PAT acts as its owner, so membership must be checked when the PAT is used, not only when it is created), [0027](0027-suite-permission-model-workspace-admin.md) (per-suite sharing, unchanged), [0041](0041-history-audit-strategy.md) (every change here is audited), [0010](0010-provider-agnostic-infrastructure-seams.md) (we never read anything provider-specific).

## Context

**An admin cannot add a member today.** A `users` row appears only when someone signs in for the first time, and who is allowed to sign in is set outside the app: `OIDC_ALLOWED_EMAILS`/`_DOMAINS` for generic OIDC, `AUTH_OTP_ALLOWED_EMAILS`/`_DOMAINS` for email OTP (there, the allowlist *is* the member list), and plain tenant membership for Azure AD, which has no app-side check at all. "Add Priya" means: edit env vars, restart, then wait until she signs in before you can even give her a role. ADR 0033 let admins manage the people already inside; nobody controls the door.

**Removing someone works differently at each door, and you cannot see that.** Checked against the code:

| Credential | Where it is resolved | Is membership re-checked? |
|---|---|---|
| Generic-OIDC bearer | `core.auth._resolve_generic_oidc_user` → `_oidc_access_allowed` | **Yes, every request** — a removed address gets 403 on its next call |
| Azure AD bearer | `core.auth._get_current_user_real` → `_upsert_user` | **No app-side check at all** — the tenant is the only gate |
| OTP session cookie | `session_service.resolve_token` | **No** — only hash, revoked, expiry; a live `dq_sess_` cookie works until it expires |
| PAT (`dq_live_`, REST **and** `/mcp`) | `api_key_service.resolve_token` | **No** — only hash, revoked, expiry; membership is never looked at after the key is created |

So removing an address from `AUTH_OTP_ALLOWED_EMAILS` stops new sign-in codes (`otp_service.is_signup_eligible`, at both the request and the verify step) and stops nothing else. The person keeps their browser session until it expires and keeps every PAT they hold **for ever** — and a PAT reaches all 50 MCP tools, including the ones that change things. That is why this is a security fix, not a convenience.

**Scope (user direction, 2026-08-29): DataQ controls its own door, nothing more.** The account at the identity provider must already exist. We do not call Entra, Cognito or any provisioning API, we do not do SCIM, and we do not create accounts anyone can sign in *with*. Adding a member means "this person may enter this workspace once their IdP lets them sign in". The UI must say that plainly in OIDC and Azure modes, or admins will read "Add member" as "create account".

## Decision

1. **One new table, `workspace_members`**: `id`, `email` (normalized, unique on `lower(email)` like `uq_users_email_lower`; the one normalization rule is `otp_service.normalize_email`), `initial_role`, `source` (`admin` or `auto_import`, CHECK-constrained — see decision 8), `invited_by` (FK to `users.id`, null for imported rows), `created_at`. No FK to `users`: a member is admitted **before** a user row exists, and the row must survive the user leaving. Additive migration; `users` is untouched.

2. **Admin add/remove at `/admin/members`**, behind the existing admin role gate, audited (`workspace_member.add` / `.remove` with actor and before/after). Logs carry counts, domains and digests, never a bare email address — the same rule the OTP code already follows.

3. **The switch is whether the table is empty. There is no flag.** **Empty table ⇒ exactly today's behaviour** for every auth mode, so an upgrade changes nothing and locks nobody out. **Non-empty table ⇒ a person is a member if they are in the env allowlist OR in the table**, checked on every request. A separate flag was rejected because a flag and the data can disagree, and nobody notices when they do.

4. **Four places to enforce it, one per credential kind:**
   - **`core.auth._upsert_user`** — Azure AD and generic OIDC, REST and MCP. Every identity sign-in passes through here, including the `_claim_unlinked_user` branch, which is only reachable from inside it.
   - **`session_service.resolve_token`** — OTP browser sessions.
   - **`api_key_service.resolve_token`** — PATs, REST **and** `/mcp`. MCP's `_PatOrJwtVerifier.verify_token` calls this, and `mcp.auth.resolve_current_user` then just loads the user row; that load is **not** a second check and must not be mistaken for one.
   - **`otp_service.is_signup_eligible`** — both callers (code request and code verify), so the check that stops the email is the check that stops the code being used.

   **The HTTP status differs by door, and pretending otherwise would be wrong.** On REST it is **403 with a membership reason**, as the OIDC sign-up gate already does: the credential itself is valid, and a 401 would send the SPA round the sign-in loop for ever. At `/mcp` it is **401**, because `_PatOrJwtVerifier.verify_token` swallows the resolver's error and returns nothing, which FastMCP reports as "not authenticated". Changing that verifier is **out of scope**; this is recorded so it is not a surprise. On both doors the *reason* goes in the audit log, which is where an operator answers "why did their PAT stop working?".

5. **Dev-bypass is exempt, and the exemption lives inside the check.** `_upsert_user` is also how the dev-bypass user is created, on REST and on MCP. A blanket gate there would make the local and eval stacks unbootable. The exemption keys on `_dev_bypass_allowed(settings)` — the same test the auth-mode ladder uses — not on comparing against `DEV_BYPASS_EMAIL`, which an attacker on a real deployment could send.

6. **The check normalizes the email itself.** The generic-OIDC path normalizes before calling `_upsert_user`; the Azure REST and MCP paths pass `preferred_username` / `upn` / `email` through as-is. A lookup on a `lower(email)` index that trusts its caller would admit or refuse people based on letter case. Normalize at the check.

7. **The env allowlists become a starting seed plus an emergency way in, and can only ever grant.** This is exactly how `WORKSPACE_ADMIN_EMAILS` already works (ADR 0033 decision 6). An env entry can let someone in; it can never remove someone the table admits. That is what makes the switch safe: fill the table and find a gap, and an env entry restores access; empty the table and you are back to today.

   **ADR 0032's boot check stays as it is, on purpose.** `Settings._validate_otp_auth` refuses to start OTP mode with an empty `AUTH_OTP_ALLOWED_*`, and OTP mode is selected by that block. So in OTP mode the env allowlist must stay non-empty even after the table is filled. We do not relax it to "allowlist OR table", because the first admin has to come from somewhere: with both empty, nobody can sign in to write the first row. The change is about *authority* (env entries can only grant), not presence. The known cost: **removing an env-listed address still means an env edit and a restart**, same as for `WORKSPACE_ADMIN_EMAILS`. Seed the minimum, add the rest in-app.

8. **Three guards against locking the workspace out of itself.**
   - **The first row written imports every existing `users` row as a member, in the same transaction, marked as provisional.** Turning enforcement on can never throw out a current user. It is part of the first insert, not a migration or a background job, otherwise it is a race. But **the import is a safety net, not proof that those people belong**: nothing deletes `users` rows today, so it re-admits everyone who ever signed in, including someone who left and still holds a PAT. So imported rows get `source = 'auto_import'` (a deliberate add gets `'admin'`), the Members page shows them under a **"review imported members"** banner until an admin confirms or removes each one, and the count is shown at switch-on. Filtering the import by `last_seen_at` was rejected: a quiet current member and a departed one look the same by that signal, and a safety net that guesses is not a safety net. **Importing everyone and flagging them is safe in the direction that matters** — too many *provisional* members is visible and one click from correct; too few silently locks a real member out.
   - **Removing the last admin-role member is refused**, like the existing last-admin guard on role changes: decide from a `SELECT … FOR UPDATE` lock, count **stored-role** admins only (an allowlist admin can disappear on the next deploy, so it cannot be what keeps the workspace recoverable).
   - **Removing yourself needs an explicit confirmation** — allowed, since an admin may be leaving after a handover, but never as a quiet click.

9. **`initial_role` sets the role only when the user row is first created.** After that the Members role editor is the source of truth, and an existing row is never downgraded on sign-in (ADR 0033 decision 7). **This cannot reuse `_upsert_user`'s `role=` parameter:** that parameter is written into both the insert values and the on-conflict update, so passing `initial_role` through it would silently overwrite an in-app role change on every sign-in. It needs its own insert-only path.

10. **Out of scope, so it is not re-argued:** OTP invite emails (the code email already reaches them); creating users at the IdP or SCIM; deactivating people and handing over their suites (that is the offboarding work, which builds on this); more than one workspace.

## Consequences

**Good** — the biggest live gap closes: removal takes effect on the next request *whatever credential is used*, so a departed member's PAT and browser session die with their membership. Adding a member no longer needs a deploy. Azure AD gets an app-side gate for the first time. `initial_role` removes the "wait for their first login, then set the role" two-step. Nothing changes for any deployment until an admin deliberately writes the first row.

**Costs we accept** — four enforcement points is four gates, and a fifth credential kind added later must join the list or it silently bypasses membership; the mitigation is to declare the gates as **data** and sweep them in tests, as `tests/support/mcp_gates.py` does for the MCP tools. Every authenticated request gains one indexed lookup (the "is the table empty" answer can be cached per process, but a cache is a *delay in revocation*, so the TTL is a decision — start with none). An email now lives in two tables, `users` and `workspace_members`, and the two can drift if someone's address changes at the IdP. Membership is per workspace with no groups or teams; this leaves room for them without adding them.

## Alternatives considered

- **Create users at the IdP (SCIM / Graph / Cognito admin APIs)** — rejected, and it is the user's explicit direction. It is the deepest lock-in possible (one integration per provider, against ADR 0010), it would need DataQ to hold write credentials for the IdP — far more dangerous than anything it holds now — and nobody asked for it. The IdP owns identity; DataQ owns admission.
- **Invite emails with a token** — rejected for now: OTP already emails the person when they request a code, and in OIDC/Azure modes there is no credential to hand over, so the invite would carry nothing useful. Revisit if self-service sign-up is ever wanted.
- **Fix each door separately** (OTP session revocation, PAT membership, and so on) — rejected. That is the shape of the current bug: three doors that each decided on their own, two of which decided nothing. One membership rule at four resolvers is one rule; three fixes are three rules that will drift apart.
- **A migration that fills the table (non-empty by default)** — rejected, and be precise about why. The problem is a **silent** backfill: it turns a zero-risk deploy into one that switches enforcement on everywhere at once with nobody watching. Decision 8's import reads the same rows, but it runs when an admin has *chosen* to switch, in the same transaction as that choice, and every row it writes is flagged and shown for review — because a `users` row proves someone once signed in, not that they still belong. Same data, opposite meaning: the migration would assert membership, the import proposes it.
- **A `MEMBERSHIP_ENFORCED` env flag** — rejected: it duplicates what the table already says, somewhere that can disagree with it, and brings back the env-edit-and-restart this ADR exists to remove.
- **Drop the env allowlists entirely** — rejected: an env-only way in is how you recover a workspace whose table has locked everyone out, exactly as `WORKSPACE_ADMIN_EMAILS` recovers one with no admins.

## Verification bar

Required for the build PRs, based on what has actually bitten this repo:

- **Every enforcement point is mutation-verified.** Delete the gate; the sweep must go red. A gate whose removal leaves tests green is not a gate — that trap hit four times on the MCP tool surface, each time because the probe failed earlier for an acceptable-looking reason. Probes must use a **real** identity that was admitted and then removed, for each credential kind.
- **Revocation is proven per credential, not per door**: a live `dq_sess_` cookie and a live `dq_live_` PAT, each created while admitted and then used after removal, on REST *and* `/mcp`.
- **The import's transaction is proven on real Postgres** — the first row and the import commit together or not at all. The test suite reads through its own transaction and cannot tell whether anything committed; use the real-DB harness the audit work introduced.
- **The last-admin guard is proven under real concurrency**, with genuinely interleaved sessions. Three earlier regression tests passed against unfixed code because they ran one after the other; mutation-check this one before trusting it.
- **The `initial_role` insert-only claim is tested from the other side**: sign in, change the role in-app, sign in again, assert the in-app value survived.
- Superuser blindness still applies: tests run as an owner that ignores `REVOKE`, so anything that rests on a privilege needs the real-privilege harness.

## Migration & rollout

One **additive** migration: `CREATE TABLE workspace_members` plus the `lower(email)` unique index. Nothing is dropped, nothing on `users` changes, so it deploys on its own and rolls back cleanly.

Two steps per CLAUDE.md §6: **step 1** ships the migration alone; **step 2** ships the resolver checks, the `/admin/members` endpoints and the Members panel. Because the table ships empty, neither step changes behaviour until an admin writes the first row — the switch is an admin's deliberate act, not a deploy event. The backend and the Members panel can land independently.
