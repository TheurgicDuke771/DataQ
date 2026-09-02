# Security & data handling

How DataQ authenticates users, stores secrets, handles the data it sees, and what it keeps.
This is a plain-language overview for people evaluating or operating DataQ. It is **not** a
legal compliance certification — much of GDPR/HIPAA is organizational (DPAs, BAAs, consent,
lawful basis) and is the deploying organization's responsibility.

## Authentication & access

- **Single sign-on (OIDC).** Users sign in through your identity provider; DataQ is
  provider-neutral (validated against Azure AD and AWS Cognito). The backend validates the
  token on every request — there is no local password store.
- **Who may hold an account is your decision, not your IdP's.** DataQ provisions a user on
  first successful sign-in, so without a gate the identity provider's *registration* policy
  silently becomes DataQ's *access* policy. That is fine for an invite-only tenant and wrong
  for a provider with self-service sign-up. Two independent controls:
  - Keep the identity provider invite-only (the AWS reference stack sets
    `allow_admin_create_user_only` on the Cognito pool; an Azure AD tenant is invite-only by
    nature).
  - Set `OIDC_ALLOWED_EMAILS` / `OIDC_ALLOWED_DOMAINS` as the app-side allowlist. It is
    checked on **every request**, not only at first sign-in, so removing an entry revokes an
    existing account rather than merely blocking a new one.

  Leaving the allowlist empty means "admit anyone the issuer vouches for" — deliberately, so
  that upgrading DataQ can never lock an existing workspace out — and the API logs
  `auth_oidc_no_signup_allowlist` at **WARNING** on every boot in that state. If you see that
  line, confirm your IdP is invite-only.
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
  (it used to overwrite the
  header with its own plaintext upstream scheme, which dropped `Secure` on live HTTPS), so
  inference is correct on that stack. If you front DataQ with **your own** proxy, confirm it
  forwards the real client-facing scheme — or simply set `AUTH_SESSION_COOKIE_SECURE=true`,
  which any HTTPS deployment can do and which stops consulting the header at all. Leave it
  unset only for genuinely plain-HTTP local dev: a hard-coded `Secure` there fails *silently*
  (the browser accepts the `Set-Cookie` and then never sends it back).
  **Before turning OTP on for real users**, run the admin-gated SMTP pre-flight test — `POST
  /api/v1/admin/auth-email/test` sends a real message to the caller's own address over the
  configured `AUTH_EMAIL_*` transport and, on failure, reports exactly which stage broke
  (`connect` / `tls` / `auth` / `send`) rather than a generic error. It never accepts a
  recipient argument — it can only mail the admin who called it, and that address is echoed
  back in the response **by design** (it's the point of the check). The SMTP password never
  appears in the response or in any log line; the recipient address never appears in a log
  line either, only in the response, to the same admin who supplied it by calling the endpoint.
  **Transport options:**
  `AUTH_EMAIL_TLS_MODE` picks `starttls` (default) / `implicit` (SMTPS on :465) / `none`
  (plaintext — test-only, loudly warned on every send, and there is no way to disable that
  warning), and `AUTH_EMAIL_CA_BUNDLE` names a PEM the mailer trusts for a relay on a private
  CA. The bundle is scoped to the mailer's own connection only — it never widens or replaces
  the trust store any other TLS client in the process relies on (Key Vault, Snowflake, ADLS,
  webhooks). There is deliberately **no** option to skip certificate verification: a private
  CA bundle is judged to cover the legitimate case, and disabling verification was rejected as
  a shortcut around it rather than shipped as a convenience.
  It is **capped per admin** (`ADMIN_EMAIL_PREFLIGHT_PER_10MIN`, default 3 per 10 minutes),
  because every call is a real
  connection to your mail relay: admin-gating bounds *who* can open one, not *how many*, and a
  scripted or compromised admin token could get your sending account throttled or blocked by
  the relay — a worse outage than the misconfiguration the pre-flight exists to catch. Over the
  cap is a plain `429`; the cap fails **open** if the counter store is unavailable, and `0`
  disables it.

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
  who has an account. The
  code-request endpoint also holds **every** such response to a common minimum latency
  (`AUTH_OTP_REQUEST_MIN_SECONDS`, default 1s), so the code-mint an eligible address incurs no
  longer stands out against an ineligible one's in-memory lookup. **The mail itself is not sent
  on the request path at all** (ADR 0032 §7 as amended): the api mints the code and hands
  delivery to the worker, which does the SMTP password lookup and the send. A floor can pad an
  ineligible address *up* to a slow relay but never an eligible one *down*, so a synchronous send
  slower than the floor used to re-open the channel — a relay's latency now cannot reach the
  response at all. A mail or broker failure is an operator log line (`otp_send_task_failed` on
  the worker, `otp_request_dispatch_failed` on the api) and the response stays `ok`, because
  the earlier 502/503 answered differently for members than for strangers while the relay was
  down. Use the admin SMTP pre-flight to find a broken relay; it is still synchronous by design.
  **What the floor does not cover, stated plainly:** a database round trip or a broker publish
  slower than the floor still overruns it, and setting the floor to `0` removes it. **Code
  *verification* carries the same floor on its own
  setting** (`AUTH_OTP_VERIFY_MIN_SECONDS`, default 0.5s):
  every rejected code answers an
  identical 401, but an address that has a live code outstanding does more database work than one
  that has none, so the rejection's *timing* used to reveal which — and cheaply, because a code is
  minted only for an *allow-listed* address, so a single request-a-code call against the address
  being probed (which answers identically either way, revealing nothing itself) is what sets that
  difference up. Every 401 is now held to the same minimum. A *successful* verification is deliberately **not** held (a caller who knows the
  code learns nothing from that), a database round trip slower than the floor still overruns it,
  and `0` removes it here too. Treat both floors as raising the attacker's cost from a handful of
  samples to a statistical exercise, not as a constant-time guarantee.
- **Mitigations that are yours:** MFA on the mailbox, a mail domain with SPF/DKIM/DMARC, and a
  minimal allowlist. If you have an IdP, prefer SSO — OTP exists for the case where you do not.

- **Per-suite authorization.** Access is granted per suite (**view / edit**); a caller only
  ever sees suites they own or are shared on. There are no ambient "see everything" reads
  except the Admin role below.
- **Workspace roles — Admin / Member / Viewer.** A stored `users.role` (ADR 0033), not just
  an env allowlist. **Admin** has workspace-wide visibility over every suite, its results,
  and schedules, and is the *only* role that can create, edit, delete or re-auth a
  **connection** — a Member can reference, test and run against an existing connection but
  cannot mutate or re-credential it (closes the earlier hole where any authenticated user
  could delete or re-point the Snowflake connection every suite ran on). Testing a **saved**
  connection is deliberately Member+ rather than Admin-only: authoring a suite against a connection you
  cannot verify is not a workable flow, and a test reveals nothing a Member could not
  already learn by running a suite. Testing an **unsaved draft** is Admin-only, because that
  request carries caller-supplied config rather than config an admin stored. **Viewer** is capped at `view` everywhere, including on
  any share it receives, and cannot test a connection — the probe opens an outbound
  connection using stored credentials, which a read-only tier has no reason to trigger.
  The Viewer cap is enforced at the point of use, not only when a share is granted, so
  demoting someone to Viewer immediately downgrades any `edit` share they already hold. Because Admin visibility includes
  failing-row samples (the one place PII can appear), **grant it sparingly** and treat a
  data-access audit trail as a prerequisite before granting it in a regulated deployment.
  `WORKSPACE_ADMIN_EMAILS` still resolves to Admin as a **bootstrap / break-glass** path for a
  fresh or locked-out workspace — keep that allowlist minimal, and prefer in-app role
  management (**Admin → Users**) once at least one Admin exists. It only ever *grants*:
  removing an address never demotes anyone, so demotion has exactly one route, where the
  guard runs.
- **An env-level actor can always mint an Admin.** This is the deliberate cost of keeping a
  break-glass path, and it is worth stating rather than leaving implicit: **anyone who can set
  environment variables on the API container can make themselves a workspace admin**, without
  a role change, an audit line, or anyone's approval. Treat write-access to the API's
  environment as equivalent to workspace-admin when you do access reviews. In steady state,
  leave the allowlist empty.
- **You cannot remove the last Admin.** A role change must leave at least one **stored-role**
  admin; allowlist-resolved admins deliberately do not count toward that (the env entry can
  vanish on the next deploy, which would leave the workspace with no admin and no in-app way
  to mint one). Promote a successor first. Role changes are **audited durably**: every change
  writes an append-only `audit_events` row (`user.role_change`, old and new role, actor,
  `request_id`) **inside the same transaction as the change**, queryable by a workspace
  admin at `GET /api/v1/admin/audit-events` (ADR 0041) — alongside the structured log line.
  Every row is hash-chained to the one before it, so a direct database edit or deletion
  outside the app is detectable (see [Compliance](#compliance-gdpr-ccpa-cpra-hipaa)
  below).

## Network exposure

- The **frontend is the only public surface**; the API runs on **internal ingress** and is
  reached only through the frontend's same-origin `/api`, `/healthz`, and `/mcp` proxy
  (ADR 0028 §5). All traffic is over **HTTPS/TLS**.
- The **MCP** AI-assistant endpoint is **fail-closed** — unauthenticated requests are rejected
  (ADR 0008), and it is not mounted at all unless the deployment has a working sign-in
  configuration. It authenticates from the `Authorization` header only and **never reads a
  cookie**, so a signed-in browser lured to a hostile page cannot be used to drive it.
  In an **OTP-only** deployment there is no identity provider, so a **PAT is the only
  credential MCP accepts**: a session token is rejected by prefix before any validation, and
  a bearer that is neither is rejected outright rather than handed to an unconfigured
  validator — the absence of a JWT verifier is a refusal, never a skipped check.
- **Browser security headers.** Every response from the frontend carries a
  **Content-Security-Policy** (`default-src 'self'`, `script-src 'self'`, `object-src 'none'`,
  `frame-ancestors 'none'`, `base-uri 'self'`), **HSTS**, **X-Frame-Options**,
  **X-Content-Type-Options: nosniff**, **Referrer-Policy** and **Permissions-Policy**. The CSP
  is the backstop that matters here specifically because the UI renders values that came from
  *your warehouse* — failing-row samples, error text, custom SQL. `connect-src` is configured
  per deployment (`DATAQ_CSP_CONNECT_SRC`) because the sign-in flow talks directly to your
  identity provider; the shipped default (`https:`) is permissive so that upgrading the image
  cannot break an existing sign-in, and the reference stacks narrow it to their exact IdP
  origins.
- **Edge rate limiting (AWS).** In addition to the in-app limiter below, the CloudFront
  distribution carries a WAF per-IP rate ceiling. The two are not redundant: the app limiter
  is per-token, understands DataQ's request classes, and **fails open** if its Redis store is
  unavailable — so it is deliberately biased toward availability. The WAF rule sheds a flood
  *before* it reaches the application at all.
- **CSRF.** The session cookie is `SameSite=Lax`, which blocks cross-site POSTs; that only
  holds while every state-changing endpoint is a POST/PATCH/PUT/DELETE, so the test suite
  audits the whole route table for a GET that mutates. Sign-in and sign-out are both POST-only,
  and the SPA and API share an origin through the proxy, so a cross-site request can neither
  carry nor read the cookie.
- **Two coordinated auth-mode selectors.** The frontend's runtime `DATAQ_AUTH_MODE` and the
  backend's inferred mode (SSO variables, or the OTP mailer + allowlist block) are separate
  contracts — neither can derive the other. Set them together; the full table is in
  the repository's `deploy/README.md` and
  [`.env.app.example`](https://github.com/TheurgicDuke771/DataQ/blob/main/.env.app.example). The
  backend refuses to start on a half-configured OTP block rather than come up unable to log
  anybody in.

## Secrets

- Datasource credentials, webhook signing keys, and channel secrets are held in a **secret
  store behind a seam** — Azure Key Vault in the primary reference deployment, AWS Secrets
  Manager on the AWS stack, OpenBao/Vault self-hosted — never in the database or in git. The
  app reads them via a managed identity (Azure) or the task IAM role (AWS).
- Secret **references** (names), not secret values, are stored alongside connections. Deleting
  a connection removes its secret (soft-delete on Key Vault).
- A reference is **server-owned**: a client may echo back the one already stored on a
  connection, but may never introduce or repoint one. Otherwise a caller could name someone
  else's secret and have the server resolve it on their behalf.
- **A stored credential is never sent to a destination the caller changed.** Editing a config
  field that decides where a credential goes — Snowflake `account`, ADLS `account_url`,
  S3/dbt `endpoint_url`, Unity Catalog `workspace_url`, Iceberg `catalog_uri`/`warehouse`/
  `properties`/`secret_property`, Airflow `base_url`, dbt `artifacts_uri` — requires re-supplying that
  credential in the same request, or the update is rejected (`422 credential_redirect`).
  Moving a connection to a new host is a supported operation; doing it with a credential you
  do not know is not. This is why an Admin, who may **rotate** a credential, still cannot
  **read** one.
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

- **In transit:** HTTPS/TLS on every public surface, and TLS to PostgreSQL (`sslmode=require`)
  and to the AWS cache (`rediss://`). Two internal hops are plaintext, both deliberately and
  both stated rather than glossed: the Azure frontend→api hop, which never leaves the
  Container Apps environment; and — on AWS only — the **CloudFront→ALB origin hop**, because
  that stack has no custom domain and therefore no certificate the load balancer could serve.
  The second one does cross the AWS network, so it is a real, tracked gap rather than a
  contained one.
- **At rest:** PostgreSQL, the object stores, and the secret store (Key Vault / AWS Secrets
  Manager) encrypt at rest in both reference deployments. The AWS cache (ElastiCache — the
  Celery broker and rate-limit counters, not a data store) currently does **not**.

### At-rest encryption per resource — the security-review evidence

What encrypts what, with which key, and where the vendor says so. This exists because
"encrypted at rest" is only useful to a reviewer with the mechanism attached; each row
below is checkable against a first-party citation rather than our assertion.

**Azure (primary reference deployment)**

| Resource | Encrypted at rest | Key | Citation |
|---|---|---|---|
| PostgreSQL Flexible Server — user + system databases, **server logs, WAL segments and backups** | Always, unconditionally | Service-managed (Azure-managed) by default | [Data encryption at rest](https://learn.microsoft.com/azure/postgresql/security/security-data-encryption) |
| Key Vault (the `SecretStore` — warehouse credentials, webhook URLs) | Yes | Microsoft-managed, FIPS 140-validated HSM-backed | [About keys](https://learn.microsoft.com/azure/key-vault/keys/about-keys#compliance) |
| Container Apps images / revisions (GHCR-sourced) | Registry-side | Provider-managed | — |
| Log Analytics + Application Insights (telemetry, PII-redacted at the logger) | Yes | Microsoft-managed by default | [Azure Monitor data security](https://learn.microsoft.com/azure/azure-monitor/logs/data-security) |

**AWS (second reference deployment)**

| Resource | Encrypted at rest | Key | Notes |
|---|---|---|---|
| RDS PostgreSQL | Yes — `storage_encrypted = true`, asserted in our own IaC | AWS-managed KMS | The one row our Terraform/OpenTofu actually asserts, because that stack **owns** its database |
| Secrets Manager (`dataq/conn-*`) | Yes | AWS-managed KMS | |
| S3 (landing bucket) | Yes | SSE-S3 default | |
| ElastiCache (Celery broker + rate-limit counters) | **No** | — | Not a data store, but the asymmetry beside an encrypted RDS is exactly what a review flags |

### Customer-managed keys (CMK)

Data at rest is encrypted with **service-managed keys** by default on both clouds.
CMK — where you hold the key — is **not wired into the IaC in this repo**, and the
reason splits into one product constraint and a set of deployment prerequisites.

**The product constraint: on Azure, CMK is creation-time-only.** Microsoft is
explicit — *"You can select the mode only at server creation time. You can't change
the mode from one to another for the lifetime of the server."* The only route to
CMK on an existing server is restoring a backup onto a **new** one, so it is a data
migration rather than a Terraform toggle, and it is one-way: reverting to
service-managed keys requires another restore.
([Limitations of CMK](https://learn.microsoft.com/azure/postgresql/security/security-data-encryption#limitations-of-customer-managed-keys-cmk))

**The prerequisites, which a deployment either meets or does not:**

- **Your IaC must own the database server from creation.** A stack that attaches to
  a pre-existing server has no server resource to hang a key on.
- **The key vault must be non-destroyable.** If the vault holding a CMK is deleted,
  the server goes **Inaccessible** and denies every connection — so purge protection
  must be on, which is irreversible once enabled. Azure additionally requires
  90-day deleted-vault retention, a value that *cannot be changed after the vault is
  created*: a vault set lower needs a new vault, not a setting change.
- **You must commit to key rotation operationally.** A key that expires, is
  disabled, or becomes unreachable takes the database offline within about an hour.

Meeting all three is a deployment topology, not a feature flag — which is why CMK is
documented here rather than shipped as an option that could be enabled unsafely. A
stack that provisions its own server and a purge-protected vault can adopt it; the
maintainers' reference deployment does neither, so it is out of scope there.

## Column classification from your warehouse

DataQ masks failing-row samples by default and surfaces only what it can justify
showing. The strongest justification available is **your own governance**:
if a column is classified in the warehouse, DataQ reads that classification and
treats it as the floor — a suite-level setting cannot lift it.

### The convention

A **fixed, documented convention**, not a per-connection mapping. A mapping would
be more flexible and worse: every deployment would express the same idea
differently, the mapping itself would become an unreviewed security control, and a
typo in it would silently un-mask a column.

Tag your **columns** with the key **`dataq_classification`** (matched
case-insensitively):

| Value | Effect |
|---|---|
| `sensitive` · `pii` · `confidential` · `restricted` · `secret` | **Always masked.** A suite policy cannot show it |
| `public` · `non_sensitive` · `nonsensitive` | Cleared — may be shown, unless the value itself is affirmatively personal data |
| anything else | **Ignored**, exactly as if untagged — see below |

**Snowflake additionally honours its own `PRIVACY_CATEGORY`**, set by Snowflake's
built-in classification. Its values (`IDENTIFIER`, `QUASI_IDENTIFIER`,
`SENSITIVE`) all mean personal data, so all three mask. It has no clearing side:
Snowflake omits the tag for data it does not consider personal rather than marking
it public, so an unexpected value means *unknown*, not *safe*.

**An unrecognised value is ignored rather than guessed at.** Guessing has only two
directions, and one of them un-masks data. `internal` is the worked example: it
sounds like a classification and is commonly the default stamp on everything, so
reading it as a clearance would clear whole tables in exactly the organisations
careful enough to tag them.

**Inherited tags are not read.** A tag applied to a table or schema is reported by
the warehouse against every column beneath it; DataQ accepts only tags applied to
the **column itself**. An inherited tag is a statement about the container, and
reading one as a per-column clearance would clear a whole schema from a single
misplaced `public`.

**The tag name is matched without its namespace**, and that is a constraint on
you rather than a feature: DataQ honours a tag *named* `dataq_classification`
wherever it lives, because knowing which database or schema should be
authoritative would require per-deployment configuration, and this convention is
deliberately fixed. **Do not create tags with this name for any other purpose** —
a same-named tag elsewhere carrying a `public`-family value would be honoured as
a clearance.

### Who can apply the tags, and what DataQ needs to read them

Two different privileges, and conflating them is the usual stumbling block.

**Applying** a tag is a data-steward action, and on Snowflake it needs more than
the `CREATE TAG` grant that creates the tag object. Either:

* `APPLY TAG` **on the account** — the global route; or
* `APPLY` on the **tag** *and* `OWNERSHIP` of the **object** being tagged.

A role that can create the tag but does not own the table is refused. Verified
against a live warehouse, not inferred — though our run held both halves of the
second route at once (the steward created the tag and owned the table), so it
confirms the combination rather than isolating which half was doing the work.

Related, if your connection uses one: a **role-scoped programmatic access token
cannot switch to another role**, even one the underlying user holds. A PAT issued
for a read-only role stays read-only no matter what the user is granted.

**Reading** them needs nothing extra. DataQ queries
`INFORMATION_SCHEMA.TAG_REFERENCES_ALL_COLUMNS` (Snowflake) and
`information_schema.column_tags` (Unity Catalog) as the connection's own role —
no `ACCOUNT_USAGE` grant, no elevated identity. That is deliberate: `ACCOUNT_USAGE`
lags by up to two hours, and a *stale* classification is the failure this feature
exists to prevent.

**That holds for `PRIVACY_CATEGORY` too**, which is worth stating because the tag
lives in the shared `SNOWFLAKE` database and a reasonable reader would expect it to
need `IMPORTED PRIVILEGES`. It does not: verified live against a connection role
holding no such grant, which nonetheless saw the tag and all three of its values.
The check mattered because failure here would be **silent** — a role that cannot
see a tag gets zero rows, not an error, so the column would look untagged and fall
back to the name heuristic. If you restrict tag visibility in your own account,
confirm your DataQ connection role can still read both tags.

### Where it applies

Only **Snowflake** and **Unity Catalog** have a column-tag concept. ADLS, S3,
Iceberg and flat files have no authoritative source to read, so for those the
classification remains the suite's own policy, the name/value classifier, and
fail-closed mode. This is a limit of the platforms, not a gap in the
implementation, and it is stated here so nobody plans around a guarantee that
cannot exist.

### How fresh it is

Tags are read **on each run** of a suite against that table and cached on the
asset. So a newly-classified column takes effect from the suite's next run —
including on samples captured *before* it was tagged, because a classification is
a statement about the data rather than about the moment it was read.

If the tags cannot be read — no permission on the tag, a warehouse that is down —
DataQ logs it and behaves exactly as it did before the tag existed. It never
infers a clearance from a failed lookup, because a wrong guess in that direction
un-masks data.

### Fail-closed mode

For a dataset where "unclassified" should mean "do not show", set
`require_classification` on the suite's column policy. Nothing row-level is then
surfaced unless a column is explicitly cleared — by a `public`-family tag or by
your own `identifier_column`. The classifier is not consulted at all, which is the
point: a column called `field_7` full of national ID numbers looks harmless to a
name heuristic.

It is off by default, and deliberately: a fully-masked failing row is
unactionable, so this is a trade you make for a regulated dataset rather than one
made for you.

## Data residency

Where data lives, and what can take it elsewhere. GDPR Ch. V asks a controller to
answer both; this section describes **how DataQ answers them**, and
`GET /api/v1/admin/deployment` gives the answer for *your* instance, read live from
the running system (workspace-admin only) so it can be checked rather than
trusted.

**One declared jurisdiction per deployment.** The unit that matters is the
*jurisdiction* — the country whose law applies — because that is what GDPR Ch. V
keys on; a cloud *region* is how a provider spells a location within one.

The primary region is set by a single IaC variable (`azure_location` /
`aws_region`) and declared to the app as `DEPLOYMENT_REGION`. The app's value is a
**declaration, not a verification** — software cannot confirm which datacentre its
database sits in — and it is reported as `null` when unset, so an auditor sees a
gap rather than a guess.

**This page deliberately does not state where any particular deployment sits.**
DataQ is customer-deployed: your regions are yours, they are set at deploy time,
and a region printed in a document is a snapshot that silently stops being true.
Read the live answer from the running instance instead —
`GET /api/v1/admin/deployment` (workspace-admin only) reports the declared region
and the enumerated transfer vectors from the system itself. A control you can
query beats a paragraph you have to trust.

### Where each resource sits

By default **every resource is in the declared region**, so the interesting rows
are the ones that cannot be:

| Resource | Region | Holds customer data? |
|---|---|---|
| PostgreSQL, secret store, compute, object storage | the declared region | **Postgres holds the data** — suites, checks, results, and the incidental personal data in failing-row samples. The secret store holds warehouse credentials; compute holds data in transit only |
| Telemetry (App Insights / CloudWatch / OTLP) | the sink's region — **operator-chosen**, and may differ | Operational metadata; PII redacted at the logger |
| CloudFront (AWS only) | **global edge** | **No.** Only fingerprinted static assets are cached (`/assets/*.<ext>`); every other path is pass-through, so no API response and no failing-row sample is stored at an edge location |
| WAFv2 Web ACL (AWS only) | **`us-east-1`, unavoidably** | **No** — the ACL is rule configuration. CloudFront-scoped ACLs exist only in `us-east-1` regardless of where the stack lives |

The CloudFront and WAF rows are stated rather than omitted so that a reviewer who
finds a `us-east-1` provider alias in an `eu-west` stack finds it explained here
rather than having to work out whether it matters. Both are properties of the
product, not of any one deployment.

### When a resource legitimately sits elsewhere

A deployment may attach the app to a **pre-existing** database server or Container
Apps environment — a subscription quota, or an organisation's shared-platform
policy, are the usual reasons. Its region was then fixed by whoever created it and
is not something this stack chooses.

**The two are treated differently, on purpose.** A shared **database** in another
region is an **accepted exception rather than drift**: declare it with
`shared_pg_expected_location`, which turns "this server is somewhere else" from an
unexplained mismatch into a recorded decision.

A shared **Container Apps environment** has **no such escape hatch, deliberately** —
it is compared against `azure_location` and a mismatch fails the plan outright. The
asymmetry follows from blast radius: one out-of-region database is a single
resource you can reason about, whereas the environment's region is inherited by
*every* Container App and Job in the stack, so accepting a mismatch there silently
relocates the entire deployment. Moving the app's compute to another jurisdiction
should require changing the declaration, not setting an override.

The basis on which such an exception is acceptable is the part worth checking, and
it is **jurisdiction, not region**. Two regions inside one country engage no
Ch. V transfer. Two regions straddling an adequacy boundary do, whatever the
provider's console calls them — so an operator in a regime that cares about
sub-national placement, or one deploying across an EU boundary, must consolidate
rather than inherit somebody else's reasoning.

### The assertions that catch drift

The Azure stack can share a Container Apps environment, declared as a `data`
source — so its region is set by whoever created it, and **every Container App and
Job in the stack inherits it** (a Job must sit in its environment's region). Left
unchecked, moving or recreating that shared environment elsewhere would relocate
all of the app's compute with a clean `apply` and no signal.

`aca.tf` therefore carries a `postcondition` comparing the shared environment's
actual location against `var.azure_location`, so a mismatch **fails the plan**
with a message naming both. For a Ch. V control, "we did not notice the
jurisdiction changed" is the whole failure mode.

A shared **database** gets the same comparison as a `check` block, which **warns**
rather than blocking. A `postcondition` there would fail every apply until someone
migrated a database, and the right response to "the DB moved" is a decision, not a
rollback.

It compares against `shared_pg_expected_location`, which **defaults to
`azure_location`** — so for an ordinary single-region deployment the two are the
same thing and the check is silent. Setting the variable is how a deployment
records an accepted exception. That indirection is the difference between a check
that still works and one that warns on every plan forever: an accepted exception
must not cost you the detector, and it must not hand the noise to every other
deployment either.

**One drafting lesson, kept because it argues for the paragraph above.** An
earlier version of this page tabulated a specific deployment's regions. One row
asserted the database sat in the declared region; checking the running deployment
showed it did not, and had not for some time. Nobody had lied — the document had
simply been written once and the infrastructure had moved. That is the failure
mode of publishing per-deployment facts, and it is why this page now describes the
mechanism and points you at the live endpoint for the values.

### What can move data out

Enumerated at `GET /api/v1/admin/deployment` — enumerated rather than derived, so
a vector that is switched **off** still appears and an auditor can see it was
considered:

- **Alert delivery** — webhooks and email carry check names, statuses and, when a
  failing sample is included, *redacted* sample values. The destination is
  operator-configured and its location is outside DataQ's knowledge.
- **Telemetry** — traces and logs to an operator-chosen sink; PII is redacted at
  the logger, so this is operational metadata rather than warehouse values.
- **MCP AI clients** — **live today.** `/mcp` serves run results, redacted failing
  samples and check configuration to whatever AI client holds a valid PAT. The
  model provider behind that client, and its jurisdiction, are chosen by the token
  holder and are outside DataQ's knowledge. This is the more consequential of the
  two LLM entries, and an earlier draft listed only the other one.
- **LLM intelligence** — the *outbound* direction, DataQ calling a model on its
  own behalf: **built, off by default** until an admin configures a provider and
  credential, sent to that admin-chosen endpoint. SQL-generation and
  check-suggestion prompts are schema plus masked aggregate profiler statistics;
  root-cause-analysis narratives additionally send the triggering check's own
  observed value — routed through the same column-policy/warehouse-tag floor
  every other results surface applies — and its expected value (a
  check-authored threshold, not warehouse data, so never masked). **Never raw
  sample rows**, on either path. Every call — feature invocation or admin test
  probe alike — is recorded with requester and token counts.
- **Sign-in email** — email-OTP codes to user addresses via the configured SMTP
  relay: account identifiers rather than warehouse content, relay operator-chosen.
- **Secret store** — warehouse credentials in Key Vault / Secrets Manager /
  OpenBao. Not customer data, but a remote store is a location, and the
  credentials it holds unlock the systems the customer data lives in.

Deploying into another jurisdiction is a variable change, not a fork: the same
images and the same IaC take a different region. What DataQ does **not** do is
verify the result — that remains the deploying organization's attestation, which
is the correct split for a customer-deployed product.

## Compliance — GDPR / CCPA-CPRA / HIPAA

**What this is:** a summary of how the technical controls above map onto the major
data-protection regimes. **What this is not:** a legal compliance certification. Much of
GDPR/HIPAA is *organizational* (DPAs, BAAs, DPIAs, consent, lawful basis, breach process)
and is the deploying organization's responsibility, not the codebase's.

### Roles & deployment model

DataQ ships as **customer-deployed, bring-your-own-license** software, not multi-tenant
hosted SaaS. That fixes the data-protection role split:

- **The deploying organization is the data *controller*** (GDPR) / *covered entity* or
  *business* (HIPAA / CCPA). It chooses the region, owns the warehouse data, holds any LLM
  credential, and carries consent / lawful-basis / DPA / BAA obligations.
- **DataQ (the software) is a *processor* / *business associate*.** Its job is to provide
  the technical controls — security of processing, minimization, deletion levers,
  auditability — the controller needs to *be* compliant.

We do not market "DataQ is GDPR/HIPAA compliant" — no software can be. The honest claim is
**processor-grade technical controls for GDPR / CCPA / HIPAA workloads**: an access audit
trail, data-subject-rights tooling, authoritative PII/PHI classification, a declared and
enforced deployment region, and encryption at rest — each covered in its own section above.

Personal data / PHI appears only *incidentally* in DataQ — in failing-row samples and, for
a set-oriented expectation's full observed-value list, in a result's `observed_value`. There
is no core people-database; HIPAA applies only when a customer points DataQ at PHI, GDPR
only to EU personal data.

### Per-regulation summary

| Regulation | Applies when | Controls in place |
|---|---|---|
| **GDPR** | EU personal data in scope | Privacy-by-design handling (logger-level redaction, retention limits); access audit trail ([Authentication & access](#authentication-access)); data-subject-rights export/erasure (below); declared + enforced residency ([Data residency](#data-residency)) |
| **CCPA / CPRA** | CA residents' data, "business" threshold | No sale of data; deletion via retention purge + the data-subject-rights erasure lever below; right-to-know via the export lever |
| **HIPAA** | Customer processes **PHI** | Encryption in transit/at rest ([Encryption](#encryption)); access control (roles + suite grants); audit controls — config changes *and* data reads recorded, admin-queryable, and hash-chained for tamper-evidence (unanchored to an external log sink by default); a BAA remains the deploying organization's obligation |

### Data-subject rights (export / erasure)

DataQ has no people-table of its own, so a subject is identified the same way the
controller's own warehouse identifies them: an Admin-only `(column, value)` workflow.
`POST /api/v1/admin/data-subject-requests/export` returns every matching failing-row-sample
cell **unredacted** (the access/portability right) across every suite in the workspace, and
records an access-audit event. The erasure counterpart removes only the matching row/cell —
the rest of a result's captured sample survives. Both also reach the stored incident evidence
snapshot, a third persisted copy of the same value that sits outside the retention clock, and
report result and incident hits separately. Both run synchronously and are recorded in
the audit trail. Full mechanism and a verification walkthrough are in the
[data-subject-rights runbook](compliance/data-subject-rights-runbook.md). The controller's
own warehouse remains their own responsibility — this covers only what DataQ itself has
captured.

### Audit-log tamper-evidence

The `REVOKE UPDATE, DELETE` on `audit_events` ([Authentication & access](#authentication-access))
guards against **accidental** in-app mutation only — the table's owner can grant the
privileges back, and the retention sweep does exactly that, by necessity. Every event is
additionally **hash-chained** to the one before it: `GET /admin/audit-events/verify`
(workspace-admin gated) recomputes the chain and reports the first break, and a daily
scheduled task runs the same check unattended. Retention purges checkpoint the boundary
they create before deleting, so a documented purge reads as clean rather than as tampering.

**The chain alone is not the tamper-evidence claim — an external anchor is.** An actor with
write access to the *whole* table could recompute the chain forward from wherever they
edited, proving nothing to that specific adversary. An optional `TamperAnchor` publishes the
chain head, HMAC-signed, to an operator-controlled endpoint outside the database (a webhook
to a separate log sink or append-only object store) — DataQ never reads it back, which is
the whole point. It is **off by default**; an operator whose regime requires a provably
unaltered log must configure it.

### Organizational artifacts

Templates and reference documents for the parts of compliance that are genuinely
organizational, published alongside this page:

- [Sub-processor / third-party disclosure](compliance/sub-processors.md) — every external
  service the software can send data to.
- [DPIA input sheet](compliance/dpia-input-sheet.md) — the personal-data inventory only the
  software can supply: data classes, per-location retention, and controls.
- [Breach-notification runbook](compliance/breach-notification-runbook.md) — a
  reference-deployment procedure plus a template for customer deployments.
- [DPA / BAA templates](compliance/dpa-baa-templates.md) — drafted with accurate technical
  annexes and marked **counsel-review-required**; not an engineering sign-off.
  Consent/lawful-basis guidance stays with the deploying organization.

## Reporting a vulnerability

Please report suspected security issues privately to the maintainers rather than opening a
public issue.
