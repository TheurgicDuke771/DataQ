# Compliance posture — GDPR / CCPA-CPRA / HIPAA (technical controls & gaps)

> **What this is:** a map of DataQ's **technical** data-handling controls against the
> **technical** requirements of the major data-protection regimes, plus an honest gap
> list. **What this is not:** a legal compliance certification. Much of GDPR/HIPAA is
> *organizational* (DPAs, BAAs, DPIAs, consent, lawful basis, breach process) and is
> the deploying organization's responsibility, not the codebase's. Treat the "v2.x
> target" column as **engineering work that would let us credibly claim alignment** —
> the legal claim still needs counsel/DPO sign-off.

## 0. The single most important framing: roles & deployment model

DataQ ships as **customer-deployed BYOL** (ADR 0013), not multi-tenant hosted SaaS.
That fixes the data-protection role split:

- **The deploying organization = the data _controller_** (GDPR) / _covered entity_ or
  _business_ (HIPAA / CCPA). They choose the region, own the warehouse data, hold the
  (post-v1) LLM credential, and carry consent / lawful-basis / DPA / BAA obligations.
- **DataQ (the software) = a _processor_ / _business associate_.** Its job is to provide
  the technical controls (security of processing, minimization, deletion levers,
  auditability) the controller needs to *be* compliant.

**Why this matters for marketing:** we can't market "DataQ is GDPR/HIPAA compliant" —
no software can. We *can* market "DataQ provides the processor-side technical controls
for GDPR / CCPA / HIPAA workloads" **once the v2.x gaps below are closed**. The honest
v1 claim is "privacy-by-design data handling"; the honest v2.x claim is "processor-grade
controls for regulated data."

Also note **scope of applicability**: DataQ is a generic data-quality tool, so personal
data / PHI appears only *incidentally* — in **failing-row samples** (`results.sample_failures`)
and warehouse **schema/column names**. There is no core people-database. HIPAA applies
only if a customer points DataQ at PHI; GDPR only to EU personal data.

---

## 1. What ships today (v1) — privacy-by-design controls

| Control | Implementation | Regulatory hook |
|---|---|---|
| **Logger-level PII redaction** (key-based: credentials, contact PII, **AAD object IDs tagged GDPR Art 4(1)**) | [`core/logging.py`](../backend/app/core/logging.py) `_PII_KEYS` / `_redact_pii` | GDPR Art 32, 25 |
| **Default-redact failing-row samples**, column-aware (suite `policy.pii_columns` + name heuristic; non-PII tested column may surface, everything else masked) | [`services/run_service.py`](../backend/app/services/run_service.py) `redact_sample_failures` | GDPR Art 25, 5(1)(c) |
| **Retention purge** of `sample_failures` after `sample_failures_retention_days` (default 30), keeping only the non-PII `metric_value`; stamps an auditable `sample_failures_purged_at` | `purge_sample_failures` daily beat ([`worker/tasks.py`](../backend/app/worker/tasks.py)) | GDPR Art 5(1)(e) storage limitation |
| **Secret isolation** — `SecretStore` seam (Azure Key Vault impl via managed identity); secrets never in git-tracked files, never logged | [`core/secrets.py`](../backend/app/core/secrets.py); CLAUDE.md secret rules | GDPR Art 32 / HIPAA §164.312(a) |
| **Encryption in transit** — Postgres `sslmode=require`; HTTPS ingress | [`deploy/terraform/azure/postgres.tf`](../deploy/terraform/azure/postgres.tf) | GDPR Art 32 / HIPAA §164.312(e) |
| **Encryption at rest** — Azure platform-managed keys on Postgres / Key Vault / Storage (default) | Azure platform default (not asserted in IaC — see gap G5) | GDPR Art 32 / HIPAA §164.312(a)(2)(iv) |
| **Access control** — suite-scoped authz (owned-or-shared), workspace-admin allowlist, Azure AD SSO | suite authz, `WORKSPACE_ADMIN_EMAILS`, MSAL | GDPR Art 32 / HIPAA §164.312(a)(1) |
| **Config-change history** — Type-4 snapshot tables (`check_versions`, `connection_versions`); credentials never snapshotted | ADR 0020 | GDPR Art 5(2) accountability (partial) |
| **Data residency is deployable** — provider-agnostic seams (ADR 0010); a controller can deploy into their own jurisdiction's region | ADR 0010 / 0013 | GDPR Ch. V transfers |
| **(Post-v1) LLM transfer minimization** — schema-only, PII-redacted context; local-endpoint option; no key-proxy | [`docs/post-v1-dq-intelligence-notes.md`](post-v1-dq-intelligence-notes.md) | GDPR Ch. V / HIPAA minimum-necessary |

> **Decided change to the Access-control row — ADR [0027](adr/0027-suite-permission-model-workspace-admin.md) / [#482](https://github.com/TheurgicDuke771/DataQ/issues/482) (build pending).**
> The suite-permission model is being revised so the **workspace-admin is an implicit
> admin on *every* suite** with **workspace-wide visibility** (Dashboard/Suites/Results),
> while normal users are capped at `edit`/`view` (grantable suite-`admin` is removed).
> Net effect on this control: least-privilege for normal users *tightens* (no peer can be
> granted manage-shares/delete), and the broad grant is concentrated in the explicit
> `WORKSPACE_ADMIN_EMAILS` allowlist. The trade-off is that a workspace-admin can then
> **read every suite's `sample_failures`** (the incidental PII/PHI store) — no new
> *unredacted* path (redaction/retention/secret-isolation are unchanged), but the **read
> surface widens**, so this read must be covered by the **G1 access-audit log (#431)**
> (see G1 below). Hold the allowlist tightly.

---

## 2. Gaps to close for a credible v2.x "processor-grade controls" claim

Ranked by severity. Tracked in the Backlog milestone: **G1 #431 · G2 #432 · G3 #433 ·
G4 #434 · G5 #435**.

### G1 — 🔴 Data-*access* audit trail (the HIPAA gate) — #431
**Requirement:** HIPAA §164.312(b) **audit controls** require a durable record of *who
accessed which PHI*. GDPR accountability (Art 5(2) / 30) wants processing records too.
**Current state:** we have config-*change* history (ADR 0020), but **no record of data
reads** — and we deliberately redact PII from logs, so logs can't serve as the audit
trail either. ADR 0020 explicitly deferred the cross-entity audit log.
**v2.x target:** an append-only access-audit log (actor, action, suite/run/result,
timestamp, request_id) for result/sample reads + share grants; tamper-evident; its own
retention policy. Revisit ADR 0020. **This is the one hard blocker for any PHI customer.**
**Scope widened by ADR 0027 / #482:** once the workspace-admin is an implicit admin on
every suite, the audit log must capture **workspace-admin cross-suite result/sample
reads** (not just owner/shared reads) — the read surface this gap must cover grows. A
PHI deployment should therefore treat G1 as a prerequisite **before** granting broad
workspace-admin.

### G2 — 🟠 Data-subject-rights machinery (erasure / access / portability) — #432
**Requirement:** GDPR Art 15 (access), 17 (erasure), 20 (portability); CCPA/CPRA right to
know / delete.
**Current state:** cascade-delete of entities + the retention purge exist, but there's no
**targeted "erase/export all personal data relating to subject X"** capability. Between
runs, `sample_failures` is a real (time-bounded) residual store of subject rows.
**v2.x target:** a subject-rights workflow — (a) erase: purge matching sample rows on
demand (not just on the retention clock), (b) export: structured dump of stored personal
data for a subject. Document that the controller's warehouse remains their responsibility.

### G3 — 🟠 Authoritative PII/PHI classification (not just a heuristic) — #433
**Requirement:** GDPR special-category data (Art 9) / HIPAA PHI must not leak via the
**surfacing** path. Today `redact_sample_failures` surfaces the *tested* column when it's
not flagged PII — but flagging is a **name-token heuristic + optional suite policy**, so a
mis-named column (`field_7` holding SSNs) can surface unredacted (false negative).
**Current state:** default-redact everywhere *else* limits blast radius; the gap is the
surfacing exception trusting best-effort classification.
**v2.x target:** make classification **authoritative** — consume warehouse-native data
classification / tags (Snowflake/UC column tags) as the source of truth, fall back to
policy, and treat the name heuristic as a last resort only. Optionally: fail-closed mode
(surface nothing unless a column is explicitly classified non-sensitive).

### G4 — 🟠 Region / residency assertion & enforcement — #434
**Requirement:** GDPR Ch. V — EU personal data must stay in-region; cross-border transfer
needs a lawful basis. The post-v1 LLM call is a new transfer vector.
**Current state:** deploy is region-pinned to **US (westus3)**; the seam *allows* an EU
deploy but nothing documents or enforces jurisdiction, and the LLM-transfer mitigation is
design-only so far.
**v2.x target:** a documented residency matrix + an IaC variable that pins all stateful
resources (Postgres, KV, Storage, ACA, LLM endpoint) to one region/jurisdiction, with a
validation that they agree; honor the schema-only/PII-redacted/local-endpoint LLM posture
in code when that feature lands.

### G5 — 🟡 Assert encryption-at-rest & offer CMK — #435
**Requirement:** GDPR Art 32 / HIPAA §164.312(a)(2)(iv) addressable encryption.
**Current state:** satisfied by Azure platform-managed keys (default), but our Terraform
neither asserts it nor offers customer-managed keys, and it's undocumented (no evidence
for a customer security review).
**v2.x target:** assert at-rest encryption in IaC, document it, and offer a CMK
(customer-managed key in Key Vault) toggle for customers who require key custody.

### G6 — ⚪ Organizational artifacts (out of code scope, tracked for completeness)
DPA / BAA templates, DPIA template, breach-notification runbook, a published
sub-processor list (incl. the LLM provider when enabled), consent/lawful-basis guidance.
These are **documentation/legal**, not engineering — listed so they aren't forgotten in a
"are we compliant?" review. Owner: the deploying organization + DataQ legal, not the
codebase.

---

## 3. Per-regulation summary

| Regulation | Applies when | v1 stance | After G1–G5 (v2.x) |
|---|---|---|---|
| **GDPR** | EU personal data in scope | Privacy-by-design handling; minimization + storage limitation strong; **missing** access audit (G1), subject rights (G2), residency enforcement (G4) | Processor-grade Art 25/32 controls + Art 15/17/20 levers + Ch. V residency |
| **CCPA / CPRA** | CA residents' data, "business" threshold | No sale of data; deletion via cascade + purge; **missing** targeted know/delete (G2) | Right-to-know / delete workflow (G2) |
| **HIPAA** | Customer processes **PHI** | Encryption + access control + minimization present; **blocked by missing audit controls (G1)** + needs a BAA (G6) | §164.312 technical safeguards met (G1 closes the gate); BAA still org-side |

## 4. The honest marketing line

- **v1 today:** "Privacy-by-design data handling — PII redaction, configurable retention,
  least-privilege secrets, suite-scoped access control."
- **v2.x (after G1–G5):** "Processor-grade technical controls for GDPR / CCPA / HIPAA
  workloads — access audit trail, data-subject-rights tooling, authoritative
  PII/PHI classification, region-pinned residency, customer-managed-key option."
- **Never:** "DataQ is GDPR/HIPAA *certified/compliant*" — compliance is a property of the
  *deployment + organization*, which DataQ enables but cannot unilaterally satisfy.
