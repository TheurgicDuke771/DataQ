# ADR 0043 — In-app workspace membership (`workspace_members`)

- **Status:** Proposed
- **Date:** 2026-09-04
- **Deciders:** @TheurgicDuke771
- **Amends:** ADR [0032](0032-email-otp-signin.md) — its "mandatory signup allowlist" becomes a *bootstrap seed + break-glass*, one input to a union rather than the whole member list. ADR [0033](0033-workspace-roles-rbac.md) — the in-app admin surface gains a second write axis (*who is in*) beside the role axis (*what they can do*); no capability in its normative matrix changes.
- **Related:** ADR [0026](0026-auth-api-keys-and-principal-seam.md) (a PAT authenticates as its owner — the reason membership must be re-checked at PAT resolution, not at mint), [0027](0027-suite-permission-model-workspace-admin.md) (per-suite ladder, untouched), [0041](0041-history-audit-strategy.md) (every mutation here is an audited deliberate act), [0010](0010-provider-agnostic-infrastructure-seams.md) (no IdP-specific claim or API is read).

## Context

**An admin cannot add a workspace member.** A `users` row is minted lazily on first successful sign-in, and *who may sign in* is decided entirely outside the application: `OIDC_ALLOWED_EMAILS`/`_DOMAINS` for generic OIDC, `AUTH_OTP_ALLOWED_EMAILS`/`_DOMAINS` for email OTP — where the allowlist **is** the member list — and bare tenant membership for Azure AD, which has no app-side gate at all. "Add Priya" is therefore: edit env vars, restart or redeploy, then wait for her first sign-in before a role can even be assigned to her. ADR 0033 gave the Admin page authority over the people already inside; it has none over the door.

**Revocation is inconsistent per door, and the inconsistency is invisible.** Verified against the code, not the tickets:

| Credential | Resolver | Membership re-checked? |
|---|---|---|
| Generic-OIDC bearer | `core.auth._resolve_generic_oidc_user` → `_oidc_access_allowed` | **Yes, per request** — a removed address 403s on the next call |
| Azure AD bearer | `core.auth._get_current_user_real` → `_upsert_user` | **No app-side check exists** — the tenant is the only gate |
| OTP session cookie | `session_service.resolve_token` | **No** — only `token_hash` / `revoked_at` / `expires_at`; a live `dq_sess_` survives to TTL |
| PAT (`dq_live_`, REST **and** `/mcp`) | `api_key_service.resolve_token` | **No** — only `key_hash` / `revoked_at` / `expires_at`; membership is never consulted after the mint |

So removing an address from `AUTH_OTP_ALLOWED_EMAILS` stops new codes (`otp_service.is_signup_eligible`, at both the request and the verify call site) and stops nothing else. The departed user keeps a working browser session until its TTL, and keeps every PAT they hold **indefinitely** — a PAT that reaches all 50 MCP tools, including the state-changing ones. That is the actual security shape of the current door, and it is why this ADR is P1 rather than a convenience feature.

**Scope, per user direction (2026-08-29): DataQ governs its own door and nothing else.** IdP account creation stays a prerequisite. We do not call Entra, Cognito, or any provisioning API; we do not implement SCIM; we do not create accounts anybody can sign in *with*. Adding a member here says "this human is admitted to this workspace once their IdP lets them authenticate" — the UI must state that plainly in OIDC and Azure modes, or admins will read "Add member" as "create account" and file a bug when nothing arrives.

## Decision

1. **One new table, `workspace_members`** — `id`, `email` (normalized, with a unique index over `lower(email)` mirroring `uq_users_email_lower`; there is one normalization rule in this codebase and it is `otp_service.normalize_email`), `initial_role`, `source` (`admin | auto_import` — CHECK-constrained, the provisional flag decision 8 turns on), `invited_by` (FK → `users.id`, null for an auto-imported row), `created_at`. No FK to `users`: a member is admitted **before** any user row exists, and the row must outlive an offboarded user. Additive migration, no change to `users`.

2. **Admin CRUD at `/admin/members`**, behind the existing `require_role(ADMIN_ROLE)` gate (ADR 0033), audited per ADR 0041 — `workspace_member.add` / `.remove`, actor + before/after from a per-entity allow-list. The membership list is not a secret but it *is* a member list: the log-line discipline from `_log_otp_mode_ready` and `_denied_identity` (counts, domains, digests — never a bare address in a log) carries over.

3. **The enforcement switch is the table's own emptiness, not a flag.** **Table EMPTY ⇒ exactly today's behaviour, per auth mode, byte for byte.** Every deployment upgrades into the identical posture it had; nobody is locked out by a migration. **Table NON-EMPTY ⇒ membership is `union(env allowlist, workspace_members rows)`**, enforced per request. A feature flag was rejected for the same reason ADR 0038 rejected an ENUM: the flag and the data can disagree, and the disagreement is silent.

4. **Four choke points, named, each a real single point of resolution:**
   - **`core.auth._upsert_user`** — Azure AD (REST + MCP) and generic OIDC (REST + MCP). Every identity-bearing sign-in funnels here, including the `_claim_unlinked_user` link branch, which is reachable only from inside it.
   - **`session_service.resolve_token`** — OTP browser sessions.
   - **`api_key_service.resolve_token`** — PATs, REST **and** `/mcp`. MCP's `_PatOrJwtVerifier.verify_token` calls this on its own session and `mcp.auth.resolve_current_user` then does a bare `session.get(User, …)`; that load is **not** a second gate and must not be mistaken for one.
   - **`otp_service.is_signup_eligible`** — both call sites (code request and code verify), so the check that stops delivery is the same check that stops redemption.

   **Denial status is door-specific, and stating it as one rule would be false.** On REST it is **403 with a membership reason** — the rule the generic-OIDC signup gate already follows: the credential *is* valid, so a 401 loops the SPA through sign-in forever. At `/mcp` it is **401 by construction**: `_PatOrJwtVerifier.verify_token` catches the resolver's `DataQError` and returns `None`, which FastMCP renders as an authentication failure, and the SPA-loop argument does not apply to an MCP client anyway. Changing that verifier's contract is **out of scope here** — recorded as a known asymmetry rather than left to be discovered. In both doors the *reason* is recorded in the audit log, which is where an operator answers "why did their PAT stop working?"; the HTTP status is not asked to carry it.

5. **Dev-bypass is exempt, and the exemption lives inside the check.** `_upsert_user` is also how the dev-bypass identity is minted, in both REST (`_get_current_user_dev_bypass`) and MCP (`resolve_current_user`'s fallback). An unconditional gate at that function would make the local and eval stacks unbootable. The exemption predicate is `_dev_bypass_allowed(settings)` — the same one the mode ladder binds on — not a comparison against `DEV_BYPASS_EMAIL`, which an attacker on a real deployment could supply.

6. **The check normalizes its own input.** `_resolve_generic_oidc_user` normalizes before calling `_upsert_user`; the Azure REST and MCP paths hand it `preferred_username` / `upn` / `email` **raw**. A membership lookup against a `lower(email)` unique index that trusts its caller would admit or deny by claim casing. Normalize at the check.

7. **Env allowlists demote to bootstrap seed + break-glass, grant-only** — the `WORKSPACE_ADMIN_EMAILS` pattern of ADR 0033 decision 6, exactly. They may only ever *admit*; they can never remove someone the table admits. This is what makes the switch safe: an operator who fills the table and then discovers a gap can restore access with an env entry, and an operator who empties the table falls back to today.

   **ADR 0032 decision 2's fail-closed boot check stands unchanged, and that is a decision, not an oversight.** `Settings._validate_otp_auth` refuses to start OTP mode with an empty `AUTH_OTP_ALLOWED_*`, and OTP mode selection is itself keyed on that block — so in OTP mode a non-empty env allowlist stays **boot-mandatory even once the table is populated**. It is not relaxed to "non-empty allowlist OR non-empty `workspace_members`", because the first admin has to come from somewhere: a workspace whose table is empty and whose allowlist is empty has no way for anybody to sign in and write the first row. The demotion is about *authority*, not presence — those addresses become grant-only, and the table is what admits everyone after them. The accepted cost is the familiar one: **removing an env-listed address still requires an env edit and a restart**, exactly as it does for `WORKSPACE_ADMIN_EMAILS`, so an operator should seed the minimum and add the rest in-app.

8. **Three lockout guards, because the failure mode here is locking the workspace out of itself.**
   - **The first row write auto-imports every existing `users` row as a member, in the same transaction — provisionally.** Turning enforcement on can never evict a current user. Not a migration and not a background job: it is part of the first insert, or it is a race. But **the import is a lockout guard, not evidence of intended membership**: nothing deletes `users` rows today, so it re-admits every departed person who ever signed in — including one still holding a live PAT. So imported rows carry `source = 'auto_import'` (against `'admin'` for a deliberate add), the Members page surfaces them under a **"review imported members"** banner until an admin confirms or removes each one, and the count is stated at switch-on rather than left to be discovered. Filtering the import by `last_seen_at` was rejected: a quiet-but-current member is indistinguishable from a departed one by that signal, and a lockout guard that guesses is not a guard. **Importing everything and flagging it is safe in the direction that matters** — an over-broad *provisional* set is visible and one click from correct, while an under-broad one silently locks a real member out.
   - **Removing the membership of the last admin-role user is refused**, mirroring the last-admin guard on role changes — decide from `SELECT … FOR UPDATE`-locked state, count **stored-role** admins only (an allowlist-resolved admin can vanish with the next deploy, so it cannot satisfy the invariant it is the recovery path for).
   - **Self-removal requires explicit confirmation** — permitted, since an admin may legitimately be offboarding themselves after a handover, but never as an unremarked click.

9. **`initial_role` seeds the user row on the new-row branch only** — the conflict branch stays promote-only per ADR 0033 decision 7, and the Members role editor is authoritative thereafter. **This cannot be delivered through `_upsert_user`'s existing `role=` parameter:** that parameter is written into *both* the `values()` and the `on_conflict_do_update` `set_`, so passing `initial_role` through it would silently overwrite an in-app role change on every subsequent request. `initial_role` needs its own new-row-only path.

10. **Out of scope, recorded so it is not re-litigated:** OTP invite emails (the code-request mail already reaches the invitee); IdP user provisioning and SCIM; deactivation and offboarding-transfer (gated on this ADR and on the admin share/ownership write pass); multi-workspace.

## Consequences

**Positive** — the largest live gap closes: removal now bites on the next request *regardless of credential kind*, so a departed member's PAT and browser session die with their membership instead of outliving it. Adding a member stops requiring a deploy. Azure AD gains an app-side gate it has never had. Pre-provisioned `initial_role` removes the "wait for their first login, then set their role" two-step. The union rule means no deployment changes behaviour until an admin deliberately writes the first row.

**Negative / accepted** — four choke points is four gates, and a fifth credential kind added later must be added to this list or it silently bypasses membership; the mitigation is the ADR-0033 pattern of declaring the gates as **data** and sweeping them, as `tests/support/mcp_gates.py` does for the tool surface. Every authenticated request gains one indexed lookup on the hot path (the emptiness check is cacheable per process with a bounded TTL; a cache is a *revocation delay*, so the TTL is a decision, not an implementation detail — start with none). `workspace_members` is a second place an email lives beside `users`, and the two can drift for a user whose address changes at the IdP. Membership is workspace-global: this ADR does not introduce groups, teams, or multi-workspace, and deliberately leaves room for them.

## Alternatives considered

- **Provision users in the IdP (SCIM / Graph / Cognito admin APIs)** — rejected, and it is the user's explicit direction. It is the deepest possible lock-in (a bespoke integration per provider, contradicting ADR 0010), it requires DataQ to hold IdP-write credentials — a far larger blast radius than anything it holds today — and it answers a question nobody asked us to answer. The IdP owns identity; DataQ owns admission.
- **Invite emails with tokens** — rejected for v1.2: OTP already mails the invitee at code request, and the OIDC/Azure modes have no credential to hand over, so the invite would carry nothing actionable. Revisit if a self-service flow is ever wanted.
- **Keep enforcing per door** (fix OTP session revocation, fix PAT membership, separately) — rejected. That is the shape the current bug *is*: three doors that each decided independently, two of which quietly decided nothing. One membership predicate applied at four resolvers is one rule; three fixes is three rules that will drift.
- **A migration that populates the table (non-empty by default)** — rejected, and note what is and isn't being rejected. The objection is to a **silent** backfill: it converts a docs-only, zero-risk deploy into one that turns enforcement on everywhere at once, with nobody present to look at the result. Decision 8's auto-import reads the same rows, but it fires at the moment an admin has *decided* to switch, it is transactionally tied to that decision, and — because a `users` row is evidence somebody once signed in and not evidence they are still meant to be here — every row it writes is marked `auto_import` and shown for review. Same data, opposite epistemics: the migration would assert membership, the import proposes it.
- **A `MEMBERSHIP_ENFORCED` env flag** — rejected: it is the state the table already encodes, stored somewhere that can disagree with it, and re-introduces the env-edit-and-restart the ADR exists to remove.
- **Replace the env allowlists entirely** — rejected: an env-only path is the break-glass that recovers a workspace whose table locked everyone out, exactly as `WORKSPACE_ADMIN_EMAILS` recovers one with no admins.

## Verification bar

Non-negotiable for the build PRs, drawn from what has actually bitten this repo:

- **Every choke point is mutation-verified.** Remove the gate; the sweep must go red. A gate whose deletion leaves tests green is not a gate — the trap hit four times on the MCP tool surface, always because the probe failed earlier for an accepted-looking reason (a fabricated id 404ing before authz). Probes here must use a **real** admitted-then-removed identity for each credential kind.
- **Revocation is proven per credential, not per door**: a live `dq_sess_` cookie and a live `dq_live_` PAT, each minted while admitted and then exercised after removal, against REST *and* `/mcp`.
- **The auto-import's transactionality is proven on real Postgres** — first-row-write and the import commit together or not at all. The test suite reads through the fixture's own transaction and so cannot see whether anything committed; this needs the dedicated real-DB harness, as the ADR-0041 access-event work did.
- **The last-admin guard is proven under concurrency**, driven with genuinely interleaved sessions. Three regression tests in the role-guard work passed against the *unfixed* code because they ran sequentially and the interleaving never occurred; mutation-check this one before believing it.
- **The `initial_role` new-row-only claim is tested from the other side**: sign in, change the role in-app, sign in again, assert the in-app value survived.
- Superuser blindness applies as ever: tests run as an owner that bypasses `REVOKE`, so anything resting on a privilege needs the real-privilege harness; the append-only audit table already produced one production incident that way.

## Migration & rollout

One **additive** migration: `CREATE TABLE workspace_members` + the `lower(email)` unique index. Nothing is dropped, nothing on `users` changes, and no existing column changes meaning — so the migration is deployable on its own and rolls back cleanly.

Two-step per CLAUDE.md §6: **step 1** ships the migration alone; **step 2** ships the resolver checks, the `/admin/members` endpoints, and the Members panel. Because the table ships empty, step 1 changes no behaviour whatsoever and step 2 changes none either until an admin writes the first row — the switch is an admin's deliberate act, not a deploy event. Backend and the Members panel of the Admin IA restructure are independently landable; the backend does not wait on that restructure.
