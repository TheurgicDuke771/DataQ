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
  (ADR 0008).

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
