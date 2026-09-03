# ADR 0007 — Airflow callback model (HMAC-signed webhook + polling fallback)

- **Status:** Accepted
- **Date:** 2026-05-30
- **Deciders:** @TheurgicDuke771

## Context

Per [ADR 0004](0004-orchestration-abstraction.md), Apache Airflow DAG run events reach DataQ via DAG-level `on_success_callback` / `on_failure_callback` hooks that POST to `POST /api/v1/orchestration/events/airflow`. We must authenticate these requests so a third party cannot forge DAG events (successful-completion events can trigger suite runs via `trigger_bindings`).

Unlike Azure Monitor (see [ADR 0006](0006-adf-webhook-authentication.md)), the Airflow callback is **code we author** as a copy-paste snippet for the user's DAGs, so it **can set custom headers and compute a signature**. This permits a stronger header-based HMAC scheme rather than a secret-in-URL.

Two constraints shape the design:

- **We cannot mutate users' DAGs.** Adoption of the callback snippet is voluntary; some users will not add it. A polling fallback is therefore mandatory, not optional.
- The signing key is a shared symmetric secret that must live in Key Vault on our side and in the user's Airflow environment.

## Decision

**Authenticate the Airflow webhook with an HMAC-SHA256 signature over the raw request body, carried in an `X-DataQ-Signature` header. Provide a copy-paste callback snippet; document the Airflow REST polling fallback for non-adopters. Rotate by hard cutover. No request-freshness check in v1.**

### Authentication

- The callback computes `HMAC-SHA256(signing_key, raw_body)`, hex-encoded, and sends it as **`X-DataQ-Signature: sha256=<hex>`** (GitHub-style prefix for forward compatibility with other digests).
- The endpoint recomputes the HMAC over the **raw, unparsed body** and compares **constant-time** (`hmac.compare_digest`). Mismatch / missing signature → **401**.
- Because the signature covers the raw body, every field — including a `timestamp` the snippet includes — is **authenticated** even though v1 does not act on freshness (leaves the door open to add a replay window later with no protocol change).
- After successful auth, the endpoint returns **200 for all well-formed events**; malformed body → 422.

### Callback snippet + polling fallback

- Ship a copy-paste **`dataq_airflow_callback`** helper for users to wire into `on_success_callback` / `on_failure_callback`. It reads the signing key from the user's Airflow secret backend / env, never hardcoded.
- For DAGs that don't adopt the snippet, the **Airflow REST API `dagRuns` polling fallback every 10 minutes** (ADR 0004) is the documented path to capture `success` runs. Webhook is the fast path; polling is the floor.
- Per ADR 0004, **only success events trigger suite runs**; failure events alert the user but do not trigger.

### Signing-key storage & config

- Key lives in Key Vault, surfaced as `AIRFLOW_WEBHOOK_SIGNING_KEY` via the `SecretStore` abstraction (PR 3b). The same value is configured in the user's Airflow environment.

### Rotation — hard cutover

- **One active signing key at a time.** Rotation = update the Key Vault key **and** the user's Airflow-side key in lockstep.
- A brief mismatch window may drop a callback delivery; **acceptable because the 10-minute `dagRuns` polling fallback** recovers missed `success` runs. No dual-key overlap logic in v1.

### Replay / freshness — not enforced in v1

- v1 validates the HMAC only; it does not reject stale `timestamp` values.
- Duplicate / replayed deliveries are neutralised by the **idempotent upsert keyed on (`provider`, `provider_run_id`)** in `pipeline_runs` — no double-trigger.
- Freshness enforcement is a documented hardening candidate (the timestamp is already signed), deferred until replay is shown to be a real risk.

## Consequences

**Positive**
- Header-based HMAC over the raw body is a standard, well-understood scheme — no secret in the URL (improves on the ADF constraint).
- Voluntary adoption is de-risked by the mandatory polling fallback; webhook is a latency optimisation, not a correctness dependency.
- Symmetric with ADR 0006 on the operational decisions (hard-cutover rotation, idempotent-upsert dedup) — one mental model for both providers.

**Negative**
- Symmetric shared key means the user's Airflow environment holds signing material; a compromised Airflow could forge events. Bounded to that user's own DAGs in a single-tenant deployment; rotation is the mitigation.
- Users who never adopt the snippet get **10-minute** worst-case detection latency instead of near-real-time. Documented, and acceptable for the DQ-trigger use case.
- Hard cutover can drop deliveries during rotation; accepted because polling backfills.
- No replay window; impact bounded to idempotent no-ops (no double-trigger).

## Alternatives considered

- **Secret in URL query param (as ADF uses)** — rejected for Airflow. Since we author the callback, we can do proper header-based HMAC; no reason to accept the weaker secret-in-URL exposure here.
- **Webhook-only, no polling fallback** — rejected. We can't guarantee users add the callback to every DAG; without polling, those runs would be invisible. ADR 0004 mandates the fallback.
- **Dual-key overlap rotation** — rejected for v1, same rationale as ADR 0006: the polling fallback covers the cutover gap.
- **Asymmetric signing (user holds a public key)** — rejected as over-engineered for single-tenant v1; symmetric HMAC + Key Vault is sufficient.

## Related

- [ADR 0004](0004-orchestration-abstraction.md) — the `OrchestrationProvider` abstraction; `AirflowProvider.parse_event` consumes the authenticated payload, `list_recent_runs` drives the polling fallback.
- [ADR 0006](0006-adf-webhook-authentication.md) — the ADF sibling; uses secret-in-URL because Azure Monitor webhooks can't set custom headers. Hard-cutover rotation and idempotent-upsert dedup (no freshness check) are shared decisions.
- Signing-key config (`AIRFLOW_WEBHOOK_SIGNING_KEY`), the receiver, the `dataq_airflow_callback` snippet, and the Airflow connection type land in the Week 2 Airflow orchestration PR.
