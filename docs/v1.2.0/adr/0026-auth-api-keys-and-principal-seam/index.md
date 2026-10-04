# ADR 0026 — DataQ-issued API keys / service tokens behind the auth seam (REST + MCP)

- **Status:** **Accepted — phase 1 (user-scoped PATs) built 2026-07-04** (v1.1 W1); phase 2 (service-account principals) remains deferred (see phase-1 record below)
- **Date:** 2026-06-29 (timing decided 2026-07-03; phase 1 built 2026-07-04)
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) (the `get_current_user` identity seam — Azure is one impl, not the architecture), [0013](0013-marketplace-distribution-and-anti-lock-in.md) (BYOL / anti-lock-in), [0008](0008-mcp-server.md) (MCP auth via `JWTVerifier` — bring-your-own-token today), [0020](0020-history-and-audit-strategy.md) (audit), the maintainers' compliance posture register

> **Amendment (2026-07-09, [ADR 0032](0032-email-otp-signin.md)):** the phase-2 open
> question "migration path for `users.aad_object_id` → generic principal without
> breaking existing ownership/shares" is **answered for the email slice**:
> `aad_object_id` becomes nullable with a unique `lower(email)` key — one user row
> per normalized email across authenticators (ADR 0032 Decision 6).
> Service-account principals remain deferred phase-2 scope.

> **Amendment (2026-08-15, provider-neutral OIDC):** the same open question is now
> **answered for a second real-IdP slice — generic OIDC** (closes the "Lock-in"
> paragraph in Context below for the auth-validation half; service-account
> principals are still deferred). ADR 0028 had already made the frontend
> provider-neutral (`oidc-client-ts`, the `DATAQ_AUTH_*` runtime contract); the
> backend validator (`fastapi_azure_auth`, hardcoded to Microsoft's discovery/JWKS
> shape and Entra-specific claim names — `oid`/`preferred_username`/`upn`) was the
> coupling left. Built rather than deferred again: a customer on AWS (Cognito) or
> GCP is the concrete non-Azure BYOL case this ADR names, and the AWS deployment
> work that surfaced the gap made it not-hypothetical.
>
> **Shape, not a "generic principal" rewrite.** `users.aad_object_id` is
> **unchanged** — not renamed, not re-typed — its bare `UNIQUE` constraint already
> gives cross-issuer uniqueness (real IdPs mint opaque/random subject ids; a
> collision between two different issuers' values is not a realistic risk). A new
> nullable `users.oidc_issuer` column disambiguates *which* issuer authenticated a
> row; both the Azure and the new generic-OIDC login paths write it on every
> login (self-healing — no backfill migration). A second, standards-based
> validator (`core.auth.OidcBearerScheme`) does OIDC discovery
> (`/.well-known/openid-configuration`) + JWKS signature verification via `PyJWT`
> against **any** configured issuer, keyed on the RFC 7519 `sub` claim (not
> Azure's non-standard `oid`) — mutually exclusive with Azure by config validation
> (one real-IdP mode per deployment; OTP still layers on either). The MCP layer
> needed less: `fastmcp.JWTVerifier` was already a generic JWKS verifier, only
> ever *configured* with Azure's endpoints — it gained a `generic_oidc` branch,
> not new verification code.
>
> **One provider-specific accommodation, documented rather than hidden:**
> Cognito's OAuth *access* token (what the SPA sends as the bearer, matching how
> the Azure path also validates an access token) carries the client id as
> `client_id`, not the standard `aud` — `OidcBearerScheme` accepts a match on
> either claim. Everything else in the validator is provider-agnostic; this line
> is the one exception, and it needs confirming against a real Cognito token once
> the AWS deployment's Cognito user pool exists — the project's standing rule that
> anything crossing a third-party token-shape boundary needs a live check, not
> just a spec read.
>
> Service-account principals remain deferred phase-2 scope, unaffected by this.

> **Stub — Proposed, not yet designed in full.** Captures the direction while it's fresh; to be fleshed out when Theme 3 (access/identity) is picked up post-v1.

## Context

Authentication today is **exclusively Azure AD bearer tokens** (delegated/SSO) for both the REST API and `/mcp`. There is no DataQ-native credential. Two problems follow:

1. **Lock-in.** This is the deepest remaining Azure coupling. The `get_current_user` seam (ADR 0010) exists and service code never reads Entra claims (CLAUDE.md §11), but the seam has exactly *one* real implementation, and the persistence layer is still Azure-shaped (`users.aad_object_id`). A BYOL customer on AWS/GCP (ADR 0013) has no Azure AD and cannot authenticate at all.
2. **No programmatic access.** Headless agents, CI, and always-on MCP clients want a long-lived, scoped, revocable secret — not a ~60-min Azure token they must refresh (ADR 0008 uses a token-*validating* `JWTVerifier`, not a client-driven OAuth provider). There is no API-key / PAT / service-token mechanism.

## Decision (direction — to be detailed)

Introduce a **DataQ-issued credential as a second authenticator behind the existing `get_current_user` seam**, so the **REST API and `/mcp` accept it identically** — explicitly *not* an MCP-only feature. On MCP, fastmcp `MultiAuth` lets `/mcp` accept either a DataQ key or an Azure token. Adding a second real auth impl is also what *exercises* (and thereby validates) the seam ADR 0010 describes.

**Phased:**

1. **User-scoped PATs first.** A key belongs to a user and inherits that user's per-suite grants → **no new authz model** (reuse the `view < edit < admin < owner` ladder; optionally down-scope a key to read-only — ideal for MCP read tools / dashboards, smallest blast radius). ~80% of the value for a fraction of the change.
2. **Service-account principals later.** These force the larger move and should be sequenced after PATs prove the seam: generalize `users.aad_object_id` into a generic **principal** with pluggable identity bindings (Azure AD oid, API key, …), and extend suite **sharing** to non-user principals.

**Credential bar (this is a new secret surface — not to be half-built):**

- Hash-at-rest (argon2/bcrypt), **show-once** on creation, identifying prefix (`dq_live_…`), expiry defaults, revocation, last-used + audit.
- Hashes live in a **new `api_keys` table** — *not* the credential SecretStore (that store is for *retrievable* connection secrets; a key hash is a verifier secret, never retrievable).
- **Lifecycle tied to the owner**: deactivating a user kills their keys, so a PAT can't become an SSO-policy backdoor that outlives the account.
- Never logged (prefix only) — same discipline as the maintainers' compliance posture.

## Decision record (2026-07-03 — the go-live "now vs post-v1" call)

**Deferred to post-v1 (Theme 3).** Rationale: this is a credential surface with an explicit
do-not-half-build bar, and v1's actual programmatic needs are covered by interim mechanisms —
the Azure CLI is pre-authorized on the API scope (non-interactive
`az account get-access-token` bearers for the live-smoke lane and local MCP clients), and
`/mcp` accepts the same Azure token as REST (ADR 0008). Rushing a PAT store into go-live week
buys little and risks the exact half-build this ADR warns against.

**Shape confirmed** (evaluated against alternatives when the timing was decided):

- **User-scoped PATs first** — as phased above. Inherits the per-suite ladder, zero new
  authz model, revocable per-integration.
- **Standalone service accounts later** — the real need is automation that outlives a person
  (owner deactivated → their PATs correctly die), but first-class service principals force the
  `users.aad_object_id` → generic-principal migration + non-user sharing; sequence after PATs.
  Interim escape hatch: a dedicated service user (e.g. `dataq-admin`) owns automation keys.
- **HTTP Basic auth — rejected.** DataQ has no password store (identity is delegated to OIDC);
  Basic auth would make DataQ a password system (storage/hashing policy, lockout, reset flows,
  credential-stuffing surface) for a *worse* credential: one secret per account (revoking one
  integration breaks all), no scoping, no expiry, replayed on every request, and routinely
  leaked via logs/proxies. A PAT has the same `Authorization`-header ergonomics without any
  of that.

## Phase-1 decision record (2026-07-04 — the build)

Pulled forward to v1.1 W1 at cycle planning (2026-07-04): the second authenticator must land
while Azure AD is still live as the reference validator (the Azure window closes ~2026-07-25),
otherwise the seam's one real impl can never be regression-checked against the new one.

**As built:**

- **Token format:** `dq_live_` + `secrets.token_urlsafe(32)` (~256 bits of entropy).
- **Hash-at-rest: SHA-256 (hex), not argon2/bcrypt — a deliberate deviation** from the
  stub's credential bar, for verifier-shaped reasons:
  - Slow KDFs exist to protect **low-entropy human passwords** from offline brute force. A
    PAT is machine-generated with ~256 bits of entropy — preimage-resistant under plain
    SHA-256; a KDF adds no security margin against guessing.
  - PATs are verified on **every request** (not once per session). An argon2/bcrypt
    verification costs ~50–300 ms by design — a per-request tax; SHA-256 is microseconds.
  - bcrypt-style salted hashes cannot be **looked up by index** — verification would be an
    O(n) scan over all keys. SHA-256 gives an O(1) unique-index lookup on `key_hash`.
  - Same trade-off GitHub and GitLab ship for their PATs.
- **Uniform 401:** unknown, revoked, and expired keys return byte-identical
  `invalid_api_key` errors — no oracle to probe key state. Bad keys never fall through to
  the Azure branch (a `dq_live_…` bearer is decided by the PAT branch alone), and in local
  dev-bypass mode a bad PAT 401s rather than degrading to the bypass identity.
- **Seam wiring, REST:** the Azure scheme moves to `auto_error=False`; `get_current_user`
  tries the PAT branch first (disjoint by prefix — a PAT is never a valid JWT), then Azure,
  else a standard 401. Service/route code is untouched — the seam held (ADR 0010 validated).
- **Seam wiring, MCP:** a composite `TokenVerifier` (PAT by prefix, else the Azure
  `JWTVerifier`) rather than fastmcp `MultiAuth` — both credentials are bearer strategies on
  the one `Authorization` header, so a prefix branch inside one verifier is the whole
  composition. The PAT-resolved user id rides a DataQ-internal claim into
  `resolve_current_user`.
- **Expiry:** default 90 days, max 365, **no non-expiring keys**. Revocation is a soft
  `revoked_at` mark (row kept for audit); user delete cascades keys (owner-lifecycle bar).
- **Surface:** `POST/GET /api/v1/me/api-keys`, `DELETE /api/v1/me/api-keys/{id}` —
  self-service, show-once, list is metadata-only. Prefix-only logging.
- **`last_used_at`** is throttled to one write per 60 s per key — audit signal without a
  hot-path write amplification.
- **Not in phase 1:** key down-scoping (read-only), the management UI (API-only for now),
  and service-account principals (phase 2, unchanged).
- **Accepted risk:** a PAT satisfies `get_current_user`, so a key can mint sibling keys —
  a leaked PAT's holder can persist beyond that key's revocation via keys they minted.
  Standard PAT trade-off (GitHub's PATs can mint PATs too); mitigations are the mandatory
  expiry, per-key `last_used_at`, and list-visibility of every key on `/me/api-keys`.
  Down-scoped (read-only) keys would remove this and stay on the phase-2 list.

Open questions carried: scope granularity, principal generalization, and where service
accounts sit relative to the workspace-admin allowlist. The token-verification-cost question
is answered by the indexed-SHA-256 design above (no caching layer needed).

## Consequences (anticipated)

- Discharges the identity-seam lock-in (ADR 0010/0013): Azure AD becomes *one* authenticator behind the seam, not the only one — unblocking BYOL on non-Azure clouds.
- Closes the programmatic-access gap for REST automation and headless MCP clients without weakening the per-user authz model (PAT phase).
- Adds a credential-management surface (issue/list/revoke UI + API) and an audit obligation.

## Open questions (for the full ADR)

- Token format + verification cost (hash lookup on every request — caching?).
- Scope granularity: read-only vs read-write vs per-suite.
- Where service-account principals sit relative to the workspace-admin allowlist.
- Migration path for `users.aad_object_id` → generic principal without breaking existing ownership/shares.
