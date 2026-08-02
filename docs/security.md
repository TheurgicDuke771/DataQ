# Security & data handling

How DataQ authenticates users, stores secrets, handles the data it sees, and what it keeps.
This is a plain-language overview for people evaluating or operating DataQ. It is **not** a
legal compliance certification — much of GDPR/HIPAA is organizational (DPAs, BAAs, consent,
lawful basis) and is the deploying organization's responsibility.

## Authentication & access

- **Single sign-on (OIDC).** Users sign in through your identity provider; DataQ is
  provider-neutral (validated against Azure AD). The backend validates the token on every
  request — there is no local password store.
- **Personal access tokens (PATs).** For headless / AI-client use, users mint `dq_live_`
  tokens ([API keys](api-keys.md)). Tokens are **hashed (SHA-256) at rest** — the plaintext is
  shown once and never stored — and carry the **same authz as the user**, on REST and MCP.
  They can be scoped with an expiry and revoked.
- **Email one-time codes (OTP).** For deployments with **no identity provider**, a human can
  sign in by proving they own a mailbox: DataQ mails a 6-digit code, and verifying it sets an
  **HttpOnly, SameSite=Lax** session cookie (fixed lifetime, default 24 h, **no refresh
  token** — signing in again is the refresh). Logout revokes the session server-side, and both
  expiry and revocation are checked on every request. Sessions are hashed (SHA-256) at rest,
  like PATs. Sign-up is **allowlist-only** — there is no open registration. MCP does **not**
  accept a session: it is a browser credential, and PATs remain the headless credential
  (ADR [0032](adr/0032-email-otp-signin.md)).
  **`Secure` is inferred, not unconditional:** by default the backend reads the
  `X-Forwarded-Proto` the proxy forwards, and marks the cookie `Secure` only when that says
  `https`. That is only as trustworthy as the proxy. The reference frontend nginx forwards the
  **edge's** header and falls back to its own `$scheme` only on a direct connection
  ([#1138](https://github.com/TheurgicDuke771/DataQ/issues/1138) — it used to overwrite the
  header with its own plaintext upstream scheme, which dropped `Secure` on live HTTPS), so
  inference is correct on that stack. If you front DataQ with **your own** proxy, confirm it
  forwards the real client-facing scheme — or simply set `AUTH_SESSION_COOKIE_SECURE=true`,
  which any HTTPS deployment can do and which stops consulting the header at all. Leave it
  unset only for genuinely plain-HTTP local dev: a hard-coded `Secure` there fails *silently*
  (the browser accepts the `Set-Cookie` and then never sends it back).

### Email as the root of trust (read this before enabling OTP)

Under email OTP, **the mailbox is the credential**. The consequences are not subtle, and they
are the reason this mode is opt-in rather than the default:

- **Mailbox compromise is account compromise.** Anyone who can read the address's inbox can
  sign in as that person and see every suite they own or are shared on. There is no second
  factor to fall back on.
- **Mailbox compromise of an admin is workspace compromise.** If the address is on
  `WORKSPACE_ADMIN_EMAILS`, that access is workspace-wide — including every suite's failing-row
  samples, the one place PII can appear.
- **One user row per normalized email**, deliberately. An OTP sign-in whose address matches an
  existing SSO-provisioned user resolves to **that** user, so grants, shares and PATs never
  fragment across authenticators. The flip side: in a deployment running **both** SSO and OTP,
  an emailed code is an alternative route into an SSO identity. If your IdP enforces MFA and
  your mail does not, OTP is the weaker of the two doors — decide that deliberately, and
  consider keeping the allowlist to addresses that have no SSO identity.
- **Mitigations DataQ does apply:** codes expire in 10 minutes, are single-use, allow at most
  5 verification attempts, and a new request invalidates the previous code; requests are capped
  **per mailbox** as well as per IP; the sign-in endpoints return an identical response *body and
  status* whether or not an address is known, so the response content cannot be used to enumerate
  who has an account. **Caveat — response *latency* is not currently equalized:** an eligible
  address incurs the code-mint and synchronous mail-send round trip that an ineligible one skips,
  so timing can still distinguish members. Treat the content-level uniformity as the guarantee and
  the timing channel as a known gap; a constant-time floor is tracked in
  [#1137](https://github.com/TheurgicDuke771/DataQ/issues/1137).
- **Mitigations that are yours:** MFA on the mailbox, a mail domain with SPF/DKIM/DMARC, and a
  minimal allowlist. If you have an IdP, prefer SSO — OTP exists for the case where you do not.

- **Per-suite authorization.** Access is granted per suite (**view / edit**); a caller only
  ever sees suites they own or are shared on. There are no ambient "see everything" reads
  except the workspace-admin role below.
- **Workspace admins.** An allowlisted role (`WORKSPACE_ADMIN_EMAILS`) with workspace-wide
  visibility over every suite, its results, and schedules (ADR 0027). Because that includes
  failing-row samples (the one place PII can appear), **keep the allowlist minimal** and treat
  a data-access audit trail as a prerequisite before granting it in a regulated deployment.

## Network exposure

- The **frontend is the only public surface**; the API runs on **internal ingress** and is
  reached only through the frontend's same-origin `/api`, `/healthz`, and `/mcp` proxy
  (ADR 0028 §5). All traffic is over **HTTPS/TLS**.
- The **MCP** AI-assistant endpoint is **fail-closed** — unauthenticated requests are rejected
  (ADR 0008). It authenticates from the `Authorization` header only and **never reads a
  cookie**, so a signed-in browser lured to a hostile page cannot be used to drive it.
- **CSRF.** The session cookie is `SameSite=Lax`, which blocks cross-site POSTs; that only
  holds while every state-changing endpoint is a POST/PATCH/PUT/DELETE, so the test suite
  audits the whole route table for a GET that mutates. Sign-in and sign-out are both POST-only,
  and the SPA and API share an origin through the proxy, so a cross-site request can neither
  carry nor read the cookie.
- **Two coordinated auth-mode selectors.** The frontend's runtime `DATAQ_AUTH_MODE` and the
  backend's inferred mode (SSO variables, or the OTP mailer + allowlist block) are separate
  contracts — neither can derive the other. Set them together; the full table is in
  [`deploy/README.md`](https://github.com/TheurgicDuke771/DataQ/blob/main/deploy/README.md) and
  [`.env.app.example`](https://github.com/TheurgicDuke771/DataQ/blob/main/.env.app.example). The
  backend refuses to start on a half-configured OTP block rather than come up unable to log
  anybody in.

## Secrets

- Datasource credentials, webhook signing keys, and channel secrets are held in a **secret
  store behind a seam** — Azure Key Vault in the reference deployment — never in the database
  or in git. The app reads them via a managed identity.
- Secret **references** (names), not secret values, are stored alongside connections. Deleting
  a connection removes its secret (soft-delete on Key Vault).
- Inbound webhooks are authenticated: ADF by a shared secret, Airflow and dbt by an
  **HMAC-SHA256** signature keyed on a stored signing key.

## The data DataQ sees, stores, and redacts

DataQ runs checks *against* your data; it is **not** a copy of your data. What it persists:

- **Metadata** — suites, checks, connection config (no secrets), schedules, trigger bindings.
- **Results** — per-check pass/fail + a numeric `metric_value`, and for failing checks a
  small **failing-row sample**.
- **Failing-row samples are the one place results can carry PII/PHI.** They are **redacted at
  the boundary, column-aware**: a suite's **column policy** (auto-derived by a classifier or
  set by hand) keeps non-sensitive breach values debuggable while masking PII columns to
  `<redacted>`. The numeric counts and row/column shape are kept.
- **Logs & traces** are PII-redacted at the logger level, and secret values never enter them.

## Retention

- Failing-row **samples are purged** after a retention window (PII-minimisation), while the
  aggregatable **`metric_value` history is kept** for trends and baselines — so you lose the
  raw rows but keep the signal.

## Encryption

- **In transit:** HTTPS/TLS everywhere (public ingress and the internal proxy hop).
- **At rest:** provided by the managed data services — PostgreSQL, the object stores, and Key
  Vault all encrypt at rest in the reference (Azure) deployment.

## Reporting a vulnerability

Please report suspected security issues privately to the maintainers rather than opening a
public issue.

---

*For the detailed technical-controls-vs-regulation gap analysis (an internal engineering
document, not a certification), maintainers keep a separate compliance-posture register.*
