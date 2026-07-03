# ADR 0026 — DataQ-issued API keys / service tokens behind the auth seam (REST + MCP)

- **Status:** Proposed — **build deferred to post-v1** (timing decision recorded 2026-07-03; see Decision record below)
- **Date:** 2026-06-29 (timing decided 2026-07-03)
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0010](0010-provider-agnostic-infrastructure-seams.md) (the `get_current_user` identity seam — Azure is one impl, not the architecture), [0013](0013-marketplace-distribution-and-anti-lock-in.md) (BYOL / anti-lock-in), [0008](0008-mcp-server.md) (MCP auth via `JWTVerifier` — bring-your-own-token today), [0020](0020-history-and-audit-strategy.md) (audit), compliance posture (#436)
- **Issue:** [#461](https://github.com/TheurgicDuke771/DataQ/issues/461)

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
- Never logged (prefix only) — same discipline as the compliance posture (#436).

## Decision record (2026-07-03 — the go-live "now vs post-v1" call)

**Deferred to post-v1 (Theme 3).** Rationale: this is a credential surface with an explicit
do-not-half-build bar, and v1's actual programmatic needs are covered by interim mechanisms —
the Azure CLI is pre-authorized on the API scope (#565: non-interactive
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

## Consequences (anticipated)

- Discharges the identity-seam lock-in (ADR 0010/0013): Azure AD becomes *one* authenticator behind the seam, not the only one — unblocking BYOL on non-Azure clouds.
- Closes the programmatic-access gap for REST automation and headless MCP clients without weakening the per-user authz model (PAT phase).
- Adds a credential-management surface (issue/list/revoke UI + API) and an audit obligation.

## Open questions (for the full ADR)

- Token format + verification cost (hash lookup on every request — caching?).
- Scope granularity: read-only vs read-write vs per-suite.
- Where service-account principals sit relative to the workspace-admin allowlist.
- Migration path for `users.aad_object_id` → generic principal without breaking existing ownership/shares.
