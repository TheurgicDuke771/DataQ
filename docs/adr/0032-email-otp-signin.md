# ADR 0032 — Email OTP sign-in: a passwordless third authenticator behind the `get_current_user` seam

- **Status:** Proposed
- **Date:** 2026-07-09
- **Deciders:** @TheurgicDuke771
- **Related:** ADR [0026](0026-auth-api-keys-and-principal-seam.md) (PATs — the verifier-secret and seam pattern this copies; Basic auth rejected there stays rejected), [0028](0028-cloud-neutral-image-runtime-config-generic-oidc.md) (frontend runtime auth config), [0010](0010-provider-agnostic-infrastructure-seams.md)/[0013](0013-marketplace-distribution-and-anti-lock-in.md) (portability guardrails)
- **Issues:** umbrella [#738](https://github.com/TheurgicDuke771/DataQ/issues/738) → slices #734 (backend) · #735 (identity) · #736 (frontend) · #737 (SMTP pre-flight); hard prerequisite #725 (rate limiting, auth slice)

## Context

Human sign-in today has exactly one real path: Azure AD (`fastapi-azure-auth` on the backend, generic OIDC against Azure on the frontend). PATs (ADR 0026) are headless-only and need an existing user to mint them; dev-bypass is single-user local eval. So a BYOL customer on a non-Azure cloud, and the post-wind-down local-first posture (#591), have **no way to log a human in**. ADR 0026 rejected HTTP Basic because it would make DataQ a password system (storage/hashing policy, lockout, reset flows). Email OTP is passwordless — proof of mailbox ownership is the credential — so it closes the gap without reopening that rejection. It **complements, not replaces**, the generic OIDC/JWKS backend validator (ADR 0013 Phase 2, tracked in #732): generic OIDC serves customers with an IdP; OTP serves small teams without one.

The trade this makes explicit: OTP swaps "bring an IdP" for **"bring a mailbox"** — an org SMTP relay is an install prerequisite of this mode. A deployment with neither uses `bypass` (solo/eval, unchanged). And unlike Basic auth, OTP still makes DataQ an identity *issuer* for its users: enumeration, mail-flooding, and code-guessing become our attack surface to own — hence the hard caps and the #725 dependency below.

## Decision

1. **Third authenticator behind the existing seam.** `get_current_user` resolves, in order: `dq_live_` bearer (PAT) → **`dq_sess_` session cookie** → Azure JWT. Same uniform-401 discipline. `/mcp` is a **non-goal**: sessions are a browser credential; PATs remain the headless/MCP credential.
2. **Auth-mode ladder, fail-closed.** `DATAQ_AUTH_MODE` gains `otp`: `bypass` (solo/eval, nothing required) · `otp` (small team — bring SMTP) · `oidc` (org IdP). Extending the `init_auth` contract, `otp` mode with incomplete `AUTH_EMAIL_*` **or an empty signup allowlist refuses to boot**, naming the missing vars — never a deployment that looks up but can't log anyone in.
3. **Sessions copy the PAT mechanism, not the PAT table.** Opaque `dq_sess_` token, SHA-256 at rest in a new `sessions` table (verifier secret — never in the SecretStore), fixed expiry (default 24 h), **no refresh pair** — re-running OTP is the "refresh". Delivered as an **HttpOnly, Secure, SameSite=Lax cookie** riding the same-origin nginx proxy (ADR 0028 §5), so the SPA never holds the token (no JS-readable storage; Lax blocks cross-site POST CSRF). DataQ does not self-issue JWTs — no signing-key lifecycle to own.
4. **OTP mechanics sized to its entropy.** A 6-digit code is ~20 bits, so the protection is caps, not KDF: 10-min TTL, single-use, max 5 verify attempts, re-request invalidates prior codes, constant-time compare, SHA-256 at rest. The request endpoint returns a **uniform response whether or not the email is eligible** (anti-enumeration) and sends nothing for ineligible addresses.
5. **Signup gating is mandatory — no open registration.** `otp` mode requires `AUTH_OTP_ALLOWED_EMAILS` and/or `AUTH_OTP_ALLOWED_DOMAINS`. First-admin bootstrap: the operator puts their own address in the signup allowlist and `WORKSPACE_ADMIN_EMAILS`, then signs in to their own mailbox — no seeded password to rotate.
6. **One user row per normalized email (identity linking).** `users.aad_object_id` becomes nullable with a unique index on `lower(email)` (two-step migration + duplicate-email audit first — #735). An OTP sign-in whose email matches an existing AAD-provisioned row resolves to **that row**: mailbox proof is the credential, and in a single-tenant AAD the email claim is tenant-controlled, so the join is trustworthy. Grants, shares, and PATs never fragment across authenticators. Consequence to state plainly: **email is the root of trust** — mailbox compromise is account compromise (and admin compromise if that address is on `WORKSPACE_ADMIN_EMAILS`).
7. **The OTP mailer is its own config block, on the request path.** `AUTH_EMAIL_*` (host/port/username/from/password-secret-name — password via `SecretStore`, same pattern as alerting) is separate from alerting's `EMAIL_*`, reusing the SMTP+STARTTLS code shape but **not** the publisher: the alert mailer is a best-effort quiet no-op by design; OTP send is synchronous (~5 s timeout), surfaces real errors, and never silently drops. A misconfigured alert channel can never block sign-in, and vice versa. An admin-gated **SMTP pre-flight** ("send me a test mail", #737) surfaces misconfiguration at install time.
8. **Hard prerequisite: #725's auth-endpoint slice.** `otp/request` is the app's first unauthenticated mail-sending endpoint; per-email and per-IP rate limits land with or before it.

## Consequences

**Positive** — a real human sign-in for non-Azure and fully-local deployments (the local-first posture's missing auth mode); no password store, ever; bootstrap without seeded credentials; all four moving parts copy proven in-repo patterns (PAT verifier storage, `init_auth` fail-closed, SecretStore-by-name, runtime frontend config), so the implementation risk concentrates in the one genuinely new thing — the identity migration (#735).

**Negative / accepted** — a mailbox becomes an install prerequisite of `otp` mode (mitigated: `bypass` remains for the mailbox-less solo case, and the BYOL audience has SMTP by definition); DataQ takes on identity-issuer abuse surface (mitigated by caps + gating + #725, but it is new surface); email-as-root-of-trust is a real trust-model shift that the security docs must state; the `users` migration touches every authz path and needs the two-step discipline; sessions add a second browser credential model (cookie) beside the OIDC bearer flow.

## Alternatives considered

- **HTTP Basic / passwords** — rejected in ADR 0026; unchanged.
- **Magic links instead of codes** — rejected: corporate mail scanners prefetch URLs (consuming single-use links), links leak into logs/referrers, and codes copy across devices. Same transport, worse failure modes.
- **TOTP authenticator apps** — rejected for this gap: enrollment needs a retrievable shared secret (password-adjacent storage) plus a recovery story — heavier than the problem. Fine as a *future* second factor on top.
- **Self-issued JWT sessions** — rejected: buys statelessness we don't need at this scale, costs a signing-key rotation story; opaque hashed tokens match the PAT precedent and are trivially revocable.
- **Bundle the generic OIDC validator instead** — rejected as *instead* (different audience: IdP-owning orgs vs IdP-less teams); it remains the marketplace prerequisite in #732 and arrives on its own track.
- **Open signup (no allowlist)** — rejected: DataQ holds failing-row samples (PII); self-provisioning by anyone who can receive email is unacceptable as a default.
