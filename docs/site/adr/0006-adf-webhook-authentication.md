# ADR 0006 — ADF webhook authentication (shared secret + hard-cutover rotation)

- **Status:** Accepted
- **Date:** 2026-05-30
- **Deciders:** @TheurgicDuke771

## Context

Per [ADR 0004](0004-orchestration-abstraction.md), Azure Data Factory (ADF) pipeline run events reach DataQ via an Azure Monitor alert rule that POSTs to `POST /api/v1/orchestration/events/adf`. We must authenticate these inbound requests so a third party cannot forge pipeline events (which can trigger suite runs on successful-completion bindings).

The transport is constrained by Azure Monitor itself:

- A **plain action-group webhook cannot attach arbitrary custom headers**. So a signed `X-DataQ-Signature`-style header (as used for Airflow in [ADR 0007](0007-airflow-callback-model.md)) is not directly available without an intermediary.
- Azure Monitor's **Secure Webhook** action uses an Azure AD bearer token, which is stronger but requires an additional app registration + AAD wiring and shifts away from the shared-secret model framed in ADR 0004.

## Decision

**Authenticate the ADF webhook with a shared secret carried as a URL query parameter, validated constant-time against a single secret stored in Key Vault. Rotate by hard cutover. No request-freshness check in v1.**

### Authentication

- Azure Monitor posts to `https://<api-host>/api/v1/orchestration/events/adf?token=<secret>` over **HTTPS only**.
- The endpoint reads `token`, compares it **constant-time** (`hmac.compare_digest`) against the secret resolved from Key Vault.
- Missing / mismatched token → **401**. The token is never logged (PII/secret redaction at the logger level, per CLAUDE.md §10).
- After successful auth, the endpoint returns **200 for all well-formed events** — including non-success and ignored events — so Azure Monitor does not enter a retry storm. Malformed body → 422.

### Secret storage & config

- Secret lives in Key Vault, surfaced to the app as `ADF_WEBHOOK_SECRET` via the existing `SecretStore` abstraction (PR 3b) — never inlined in config or committed.
- The same value is configured on the Azure Monitor action group's webhook URL.

### Rotation — hard cutover

- **One active secret at a time.** Rotation = update the Key Vault secret **and** the Azure Monitor action-group webhook URL in lockstep.
- A brief mismatch window during rotation may drop a webhook delivery; this is **acceptable because the 10-minute ADF REST polling fallback** (ADR 0004) recovers any missed `succeeded` runs. No dual-secret overlap logic in v1.

### Replay / freshness — not enforced in v1

- v1 validates the shared secret only; it does **not** reject stale `firedDateTime` timestamps.
- Duplicate or replayed deliveries are neutralised downstream by the **idempotent upsert keyed on (`provider`, `provider_run_id`)** in `pipeline_runs` — a replayed event updates the same row and does not re-trigger a suite run.
- Freshness enforcement is a documented hardening candidate, deferred until replay is shown to be a real risk.

## Consequences

**Positive**
- Minimal infra — no extra Azure AD app registration, no Logic App hop. Ships inside the Week 2 webhook-receiver PR.
- Constant-time compare + HTTPS-only + idempotent upsert covers the realistic threat model for a single-tenant v1.
- Rotation is operationally simple; the polling fallback already exists as the safety net.

**Negative**
- The secret appears in the webhook **URL**, so it can surface in Azure Monitor action-group config and potentially some network/proxy logs. Mitigated by HTTPS, treating it as a rotatable credential, and never logging the query string on our side. A header- or AAD-based scheme would avoid this but at higher infra cost (see Alternatives).
- Hard cutover can drop deliveries during the rotation window; accepted because polling backfills.
- No replay window means a captured valid request could be replayed; impact is bounded to idempotent no-ops on existing `pipeline_runs` rows (no double-trigger).

## Alternatives considered

- **Secure Webhook (Azure AD bearer token)** — rejected for v1. Strongest option and avoids secret-in-URL, but costs an extra app registration + AAD validation path and revises ADR 0004's shared-secret framing. Revisit if the security review before Week 7 deploy flags secret-in-URL as unacceptable.
- **Logic App intermediary that injects a signed header** — rejected. Would let ADF and Airflow share an `X-DataQ-Signature` HMAC design (symmetric), but adds a Logic App resource + an operational hop to pay for and monitor.
- **Dual-secret overlap rotation** — rejected for v1. Zero-downtime rotation, but the polling fallback already covers the small cutover gap, so the extra validation branch isn't worth it yet.

## Related

- [ADR 0004](0004-orchestration-abstraction.md) — the unified `OrchestrationProvider` abstraction this webhook plugs into; `AdfProvider.parse_event` consumes the authenticated payload.
- [ADR 0007](0007-airflow-callback-model.md) — the Airflow sibling; uses HMAC-signed-header auth because Airflow callbacks (unlike Azure Monitor) can set custom headers. Rotation (hard cutover) and replay (idempotent-upsert only, no freshness check) decisions are shared between the two.
- Shared secret config (`ADF_WEBHOOK_SECRET`) and the receiver land in the Week 2 ADF webhook-receiver PR.
- A security review before the Week 7 deploy should re-examine secret-in-URL exposure.
