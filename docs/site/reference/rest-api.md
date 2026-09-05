# REST API

DataQ's REST API is the same surface the web UI uses — versioned under `/api/v1`, with the
same per-suite authorization. Use it for scripting, CI integration, or your own tooling.

## Base URL & auth

The API has no public ingress of its own; reach it **through the frontend host**, which
proxies `/api` same-origin (ADR 0028 §5):

```
https://<your-frontend-host>/api/v1/...
```

Authenticate with a **personal access token** (mint one in the UI → Profile → API keys, see
[API keys](../guides/api-keys.md)) as a Bearer token:

```bash
BASE=https://<your-frontend-host>/api/v1
TOKEN=dq_live_xxxxxxxx   # keep it out of shell history / source

curl -s -H "Authorization: Bearer $TOKEN" $BASE/me
```

A PAT acts **as its owning user** — every call is scoped by the same two axes as the UI: your
**workspace role** (`admin | member | viewer`, ADR 0033) and your per-suite `view`/`edit`
grants. Both resolve per request, so a role change or a revoked share applies to keys you
already hold, on their very next call. Unauthenticated requests get `401`; an authenticated
call your role doesn't permit gets `403`. (The interactive Swagger/OpenAPI docs are disabled
in production — this page is the reference.)

## Conventions

- **Versioning:** all endpoints are under `/api/v1`.
- **Errors:** a JSON envelope — `{"error": {"code": "...", "message": "...", "detail": {...}}}` —
  with a conventional HTTP status (`401` auth, `403` forbidden, `404` not found / hidden,
  `422` validation, `409` conflict, `429` rate limited, `502` datasource unreachable).
  Named codes worth knowing: `rate_limited` (see below), and `credential_redirect` on
  `PATCH /connections/{id}` — see [Connections](#connections).
- **Rate limiting (ADR 0035):** every surface is throttled per minute — authenticated
  requests per API key, unauthenticated per client-IP **prefix** (IPv4 /24, IPv6 /64 by default — machines on one allocation share a budget). Over the limit returns `429` with
  `code: "rate_limited"`, `detail.retry_after_seconds` (1–60), and a matching `Retry-After`
  header (plus `X-RateLimit-Limit` / `X-RateLimit-Remaining`). Back off for that many seconds.
- **IDs** are UUIDs. Timestamps are ISO-8601 UTC.

## Endpoints

### Identity & sign-in

| Method | Path | What |
|---|---|---|
| GET / PATCH | `/me` | The current user + `role` (`admin` / `member` / `viewer`) + `is_workspace_admin`; PATCH updates `display_name`. |
| POST | `/me/api-keys` | Mint a PAT (plaintext returned **once**). |
| GET | `/me/api-keys` | List your keys (metadata only, never the token). |
| DELETE | `/me/api-keys/{id}` | Revoke a key. |
| POST | `/auth/otp/request` · `/auth/otp/verify` | Email-OTP sign-in (ADR 0032): request a code, redeem it for a `dq_sess_` cookie session. Uniform responses — never confirms whether an address is enrolled. |
| POST | `/auth/logout` | Revoke the OTP session server-side. |
| GET | `/users/search` | Find a user by name/email (for sharing). Viewer results are edit-clamped. |

### Connections

| Method | Path | What |
|---|---|---|
| GET / POST | `/connections` | List / create a connection. |
| GET / PATCH / DELETE | `/connections/{id}` | Read / update / delete. |
| POST | `/connections/test` | Test an **unsaved draft** connection — nothing is persisted. |
| POST | `/connections/{id}/test` | Test live connectivity. |
| POST | `/connections/{id}/reauth` | Rotate the credential and verify. |
| GET | `/connections/{id}/versions` | Config-change history (ADR 0020 snapshots — never credentials). |

**Roles (ADR 0033).** Connections are shared infrastructure holding credentials, so the gates
here are the sharpest in the API: **create, update, delete, re-auth and the unsaved-draft
`POST /connections/test` are Admin-only**; the saved-connection `POST /connections/{id}/test`
is Member+; list and read are open to any authenticated user (responses carry `has_secret`,
never secret material). A Member's PAT hitting `POST /connections` gets `403` regardless of
any suite grant — the two axes are independent.

**Credential health.** Each datasource connection in `GET /connections` carries a
`credential_health` object — `status` (`healthy` / `failing` / `unknown`),
`consecutive_auth_failures`, `last_auth_success_at`, `last_auth_failure_at` and a classified
`last_error`. It is derived from real use (runs, dry-runs, profiles and
`POST /connections/{id}/test`), never a periodic probe, and only a credential **rejection**
moves it: a missing grant, an unreachable host and a bad table name leave it untouched.
`unknown` means the credential has not been used yet and is never reported as `healthy`.
Orchestration connections carry `null` here — theirs is the poll health above.
`GET /admin/health` returns the same shape for every datasource connection at once, worst
status first.

**Moving a connection to a new host.** A `PATCH` that changes a field deciding *where* the
credential is sent — `account` (Snowflake), `account_url` (ADLS), `endpoint_url` (S3, dbt),
`workspace_url` (Unity Catalog), `catalog_uri` / `warehouse` / `properties` /
`secret_property` (Iceberg),
`base_url` (Airflow), `artifacts_uri` (dbt) — must re-supply that credential in the same
request. Otherwise it returns `422` with `code: "credential_redirect"` and
`detail.required` naming what to send. A stored credential is never forwarded to a
destination the caller changed.

### Notification channels

A reusable Teams/Slack/email/generic-webhook destination, defined once and referenced from any
number of suites — the destination only; per-suite `alert_on`/enabled stays on
`/suites/{id}/notifications` above.

A `webhook` channel posts an HMAC-SHA256-signed JSON body (header `X-DataQ-Signature`) to an
admin-supplied `webhook_url` — the vendor-neutral way to reach PagerDuty, Opsgenie, ServiceNow,
Jira, or a self-hosted receiver with no per-vendor code. The destination URL must be `https` and
must not resolve to a private, loopback, or otherwise internal address (an SSRF guard, checked
both when the channel is saved and again before every send). DataQ generates the signing key —
it is returned in the response body **exactly once**, at creation (or at rotation via
`regenerate_hmac_secret: true` on `PATCH`), and is never retrievable again after that.

A `webhook` channel may also carry an optional `payload_template` — a JSON object reshaping the
generic alert body for a specific receiver (a PagerDuty Events-API shape, an Opsgenie/ServiceNow/
Jira payload) — and an optional `auth_header_name` + `auth_header_value` pair, an extra header
some receivers want beside the signature. A template's `{{field.path}}` placeholders resolve by
key lookup only against the same fields the generic payload already carries; there is no
expression language, so a template can rename or select what's there but never reach a field
that isn't. `auth_header_value` is write-only, same as every other channel credential. Leaving
both unset keeps the channel sending the plain generic payload with no extra header — the
pre-template behavior, unchanged.

`payload_template` is stored as plain JSON, not a SecretStore-backed credential — but a template
commonly has nowhere else to put a receiver's static routing/integration key than as a literal
value in the JSON, so `GET`/`LIST` only ever include it for a workspace **Admin** caller; every
other authenticated user gets `has_payload_template` (a presence-only boolean) instead, the same
shape already used for genuine secrets. Put any real credential in `auth_header_value` instead —
it's encrypted at rest and never echoed back to anyone. Changing `webhook_url` on a channel that
already has a stored auth header requires re-supplying `auth_header_value` in the same request
(`422 channel_credential_redirect` otherwise) — silently repointing the destination must never
carry a stored credential to a URL the caller didn't just prove they still control.

| Method | Path | What |
|---|---|---|
| GET / POST | `/notification-channels` | List / create a channel. |
| GET / PATCH / DELETE | `/notification-channels/{id}` | Read / update / delete (refused with `409 channel_in_use` while any suite still references it). |
| GET | `/suites/{id}/notification-channels` | List a suite's linked channels. |
| PUT / DELETE | `/suites/{id}/notification-channels/{channel_id}` | Link / unlink a channel (linking is idempotent — relinking is a no-op, not a conflict). |

**Roles.** Same split as connections: create/update/delete are **Admin-only** (a webhook URL is
a credential); list/read are open to any authenticated user (`has_webhook` only, never the
URL); linking/unlinking a suite follows that suite's own `view`/`edit` grant.

### Suites & checks

| Method | Path | What |
|---|---|---|
| GET / POST | `/suites` | List / create a suite. |
| GET / PATCH / DELETE | `/suites/{id}` | Read / update / delete. |
| GET / POST | `/suites/{id}/checks` | List / add checks. |
| GET / PATCH / DELETE | `/suites/{id}/checks/{cid}` | Read / update / delete a check. |
| POST | `/suites/{id}/checks/dryrun` | Preview a check against live data (no persistence). |
| POST | `/suites/{id}/checks/{cid}/snooze` · DELETE to clear | Snooze a check's alerts for N hours. |
| GET | `/suites/{id}/checks/{cid}/versions` · POST `…/versions/{n}/restore` | Version history + restore (restore mints a new version). |
| GET | `/suites/{id}/checks/{cid}/history` | Result history for the trend view (`metric_value` over time). |
| GET | `/suites/{id}/checks/{cid}/baseline` · POST `…/rebaseline` | Read / recapture a monitor baseline (schema-drift, anomaly). |
| GET | `/suites/{id}/export` · POST `/suites/import` | Portable suite document (env promotion). |
| GET / PUT | `/suites/{id}/column-policy` | Read / set the failing-sample redaction policy. |
| POST | `/suites/{id}/column-policy/suggest` | Heuristic PII-column suggestions from a profile. |
| POST | `/suites/{id}/profile` | Column profiler (no persistence; audited as a data access). |
| GET | `/suites/{id}/columns` | Column names/types of the resolved target (cheap authoring aid). |
| GET | `/suites/{id}/batch-preview` | Which files a flat-file batch pattern currently matches. |
| GET / POST | `/suites/{id}/shares` | List / grant per-suite access (`view` / `edit`). |
| PATCH / DELETE | `/suites/{id}/shares/{user_id}` | Change / revoke a grant (Viewers cap at `view`). |
| GET / PUT / DELETE | `/suites/{id}/notifications` | Per-suite alert config (channels, `alert_on`, auto-resolve). |

### Running & results

| Method | Path | What |
|---|---|---|
| POST | `/suites/{id}/run` | Trigger a run (returns a run id to poll). |
| GET | `/runs` · `/runs/{id}` | List runs / get a run with its results (reads are access-audited — ADR 0041). |
| GET | `/runs/{id}/progress` | Live per-check progress. |
| POST | `/runs/{id}/cancel` | Cancel a queued/running run. |
| GET | `/runs/{id}/results/{rid}/comparison_report` | CSV/XLSX diff report of a comparison result (derived on demand, never stored). |
| GET | `/dashboard/summary` | KPIs + run trend + per-suite performance. |

### Assets & incidents

| Method | Path | What |
|---|---|---|
| GET | `/assets` · `/assets/{id}` | The monitored tables/files (ADR 0034/0037): health rollup, composing suites (grant-filtered), lineage. Paged, `X-Total-Count`. |
| PATCH | `/assets/{id}` | Set owner / description (workspace-Admin-only). |
| GET | `/incidents` · `/incidents/{id}` | Open/acknowledged/resolved incidents with the evidence card (suite-granted; 404-no-leak). |
| POST | `/incidents/{id}/ack` · `/incidents/{id}/resolve` | Lifecycle transitions (requires `edit` on the suite). |

### Scheduling & orchestration

| Method | Path | What |
|---|---|---|
| GET / POST | `/schedules` | List / create cron schedules. |
| GET / PATCH / DELETE | `/schedules/{id}` | Read / update / delete. |
| GET / POST | `/trigger-bindings` | List / create pipeline→suite trigger bindings. |
| GET / PATCH / DELETE | `/trigger-bindings/{id}` | Read / update (incl. enable/disable) / delete. |
| GET | `/pipeline_runs` · `/orchestration/pipelines` | Monitored orchestrator runs. |
| GET | `/orchestration/near-misses` | Succeeded pipeline runs that matched **no** enabled binding (why a trigger never fired). |
| POST | `/orchestration/events/{provider}` | Inbound webhook (adf / airflow / dbt) — authenticated by shared-secret / HMAC, not a PAT. |

### LLM-assisted authoring (ADR 0042)

Each POST queues an async invocation (`202`, `LlmInvocationQueued`) run by the worker
against the configured `LLMProvider`; poll `GET /llm/invocations/{id}` for the result.
Rate-limited under a dedicated `llm` class (per-principal and per-IP), since each call is
an outbound model request.

| Method | Path | What |
|---|---|---|
| POST | `/llm/sql_generation` | Draft a custom-SQL check from a natural-language rule (suite edit). |
| POST | `/llm/check_suggestions` | Suggest checks for a suite from its column profile (suite edit). |
| POST | `/llm/rca_narrative` | Root-cause narrative for a failed check, from an incident's evidence card. |
| GET | `/llm/invocations/{id}` | Poll one invocation — requester or workspace admin only. |

### Admin (workspace-admin only)

| Method | Path | What |
|---|---|---|
| GET | `/admin/suites` · `/admin/users` · `/admin/access` | Unscoped workspace-wide views. |
| PATCH | `/admin/users/{id}/role` | Change a workspace role (last-admin guarded; audit-tabled). |
| GET | `/admin/members` | Workspace membership, with whether enforcement is on and how many users a first add would import. |
| POST | `/admin/members` | Admit an address, with an optional initial role. The first add turns enforcement on and imports existing users for review (audited). |
| DELETE | `/admin/members/{id}` | Withdraw a membership — bites on the next request for every credential kind. Last-admin guarded; `confirm_self=true` to remove your own (audited). |
| POST | `/admin/members/{id}/confirm` | Clear the provisional flag on an imported member. Grants nothing new (audited). |
| GET | `/admin/audit-events` | The append-only audit log (config + data-access events, ADR 0041). |
| GET | `/admin/deployment` | Declared residency / deployment posture (`DEPLOYMENT_REGION`). |
| GET | `/admin/orchestration/webhooks` | Webhook receiver URLs + auth mode per provider. |
| GET | `/admin/secret-sweep` | Last orphan-secret sweep report — `never_run` if the sweep hasn't recorded one. |
| POST | `/admin/secret-sweep/run` | Run the orphan-secret sweep now, always report-only (audited). |
