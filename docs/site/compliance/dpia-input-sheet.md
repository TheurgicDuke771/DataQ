# DPIA input sheet — what personal data DataQ can hold

> **Who this is for:** a controller running a Data Protection Impact Assessment
> (GDPR Art 35) or a HIPAA risk analysis over a deployment of DataQ. This sheet is
> the processor-side input **only DataQ can supply**: the complete inventory of
> personal data the software can hold, where each item lives, how long it is kept,
> and which controls apply. Everything here describes the software's mechanisms;
> your deployment's concrete regions and endpoints are readable from
> `GET /api/v1/admin/deployment`, per the [residency posture](../security.md#data-residency).

## The framing that bounds the assessment

DataQ is a data-quality tool, not a people database. Personal data appears in two
distinct classes:

1. **Incidental warehouse data** — values copied out of *your* monitored tables
   when a check fails. DataQ never ingests tables wholesale; it stores small
   evidence samples of failing rows. Whether those contain personal data depends
   entirely on what you point it at.
2. **Workspace account data** — the identities of the people who use DataQ
   itself: emails, display names, roles, sign-in state.

The controller's warehouse remains the controller's responsibility — DataQ reads
it but is not its system of record.

## Class 1 — incidental warehouse data

| Where | What | Retention | Controls |
|---|---|---|---|
| `results.sample_failures` | Up to a bounded number of failing rows per check result, as column→value maps | `SAMPLE_FAILURES_RETENTION_DAYS` (default **30**); daily purge sets the column NULL and stamps `sample_failures_purged_at` — the row and its `metric_value` trend survive, the personal data does not | Column-aware **redaction ladder** on every read surface (REST, MCP, alert delivery), driven by the per-suite column policy, the governance floor from warehouse-native PII tags (G3, `services/column_tags.py`), and fail-closed mode (`require_classification`) |
| `results.observed_value` (list-shaped) | A set-oriented expectation's full observed distinct-value list can reproduce column values | Same purge as `sample_failures` | Same redaction ladder |
| `results.observed_value` (scalar `unparsed_value` cell) | A single unparseable cell value captured for diagnosis | `SAMPLE_FAILURES_RETENTION_DAYS`, same purge as the two rows above | Redaction ladder applies on read |
| `incidents.evidence` (`failing_result.observed_value`) | A stored snapshot of the same kind of literal cell value as the `results.observed_value` rows above, captured once when an incident opens or gets a fresh occurrence | **Not covered by the retention purge** — the snapshot persists for the life of the incident row, independent of the `SAMPLE_FAILURES_RETENTION_DAYS` clock | Redacted through the same column-policy/warehouse-tag floor, but **at write time only** (once, when the card is built) — a policy change afterward does not retroactively re-mask an already-stored card, unlike the read-time redaction the rows above get on every read |
| Dry-run / live-probe responses | Real values shown to the check author; **nothing persisted** | Not stored (structurally cannot persist) | Fail-closed suites mask even here; REST dry-run values are shown unredacted by design, a recorded decision |

**Erasure status — state it honestly in your DPIA:** deletion happens on the
retention clock and via entity cascade (deleting a suite/check destroys its
results) **and, on demand, via the [data-subject-rights runbook](data-subject-rights-runbook.md)** — an
Admin-only capability that identifies a subject by a `(column, value)` pair (the
same key the controller's own warehouse row uses; DataQ has no people-table) and
surgically removes only the matching row/cell from `sample_failures` /
`observed_value`, leaving the rest of a result's captured sample — other rows,
other subjects — intact. **This on-demand erasure does not reach `incidents.evidence`**
(the row above) — an incident's stored snapshot survives an on-demand erasure of
the same value from `results`, and only entity cascade (deleting the covering
suite/check/asset) removes it.

## Class 2 — workspace account data

| Where | What | Retention | Controls |
|---|---|---|---|
| `users` | Email (unique, lower-cased), display name, OIDC subject id + issuer, workspace role | Life of the account | Role-gated admin surface; ADR 0033 two-axis authz |
| `sessions` | OTP-mode sign-in sessions — **token hash only** (never the token) | Server-side revocation; logout deletes | HttpOnly cookie; SHA-256 at rest |
| `otp_codes` | One-time codes — **hashed**, attempt-capped | Expired codes purged daily (`purge_otp_codes` beat) | Rate limits + enumeration-resistant responses |
| `api_keys` | PATs — **SHA-256 hash only**, name, last-used | Until revoked | `dq_live_` prefix supports secret scanning |
| `audit_events` | Actor email/id, action, target — the append-only audit trail (ADR 0041) | `AUDIT_RETENTION_DAYS` (default **365**), independent of the sample purge — one clock keeps a record, the other destroys one | Append-only (`REVOKE UPDATE/DELETE`); **no warehouse values ever copied in** (ADR 0041 §2.6) — read events name *which* result was read, never what it contained. Hash-chained for tamper-evidence, unanchored to an external log sink by default |
| Logs / telemetry | Request ids, structured events | Sink-controlled | **PII redacted at the logger level** — the redactor sits in `core/logging.py`, so a dependency's log line is scrubbed too |

## The rows a DPIA form usually asks for

| DPIA question | DataQ answer |
|---|---|
| Lawful-basis notes | DataQ is a **processor** (BYOL, ADR 0013); the deploying controller holds lawful basis for the warehouse data it monitors. Workspace accounts: legitimate interest / contract (employee tooling). |
| Data subjects | Whoever appears in monitored tables (controller-determined); workspace users (employees/contractors of the controller). |
| Special categories | Only if the controller points checks at such columns. Mitigations: column policy + warehouse-native tag floor (G3) + fail-closed mode for suites that must never show values. |
| Cross-border transfers | Enumerated, not derived — see the [sub-processor disclosure](sub-processors.md) and the residency posture. The outbound-LLM vector is **built, off by default** — live only once an admin configures a provider and credential. SQL-generation and check-suggestion prompts use masked aggregate profiler statistics; RCA narratives additionally send the triggering check's own observed value, masked through that same column-policy/warehouse-tag floor, and its expected value (a check-authored threshold, not warehouse data). Never raw sample rows on either path. MCP clients are the token-holder's choice. |
| Access ("who saw it") | G1 read events: data reads on REST **and** MCP are recorded and admin-queryable. |
| Erasure ("how is it removed") | Retention purge (30-day default) + entity cascade, **and** on-demand targeted erasure by `(column, value)` — the [data-subject-rights runbook](data-subject-rights-runbook.md). |
| Security of processing | See [Security & data handling](../security.md): secrets in a dedicated store, TLS, rate limiting, security headers, single public surface, non-root containers, least-privilege DB role. |

Last reviewed: 2026-09-01 (originally 2026-08-21).
This sheet shares its inventory with the [data-subject-rights runbook](data-subject-rights-runbook.md) — update both together (one artifact, not two).
