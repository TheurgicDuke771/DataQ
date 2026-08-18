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
and, for a **set-oriented expectation**'s full observed distinct-value list, in
**`results.observed_value`** (#1229/#1253) — and warehouse **schema/column names**. There
is no core people-database. HIPAA applies only if a customer points DataQ at PHI; GDPR
only to EU personal data.

---

## 1. What ships today (v1) — privacy-by-design controls

| Control | Implementation | Regulatory hook |
|---|---|---|
| **Logger-level PII redaction** (key-based: credentials, contact PII, **AAD object IDs tagged GDPR Art 4(1)**) | [`core/logging.py`](../backend/app/core/logging.py) `_PII_KEYS` / `_redact_pii` | GDPR Art 32, 25 |
| **Default-redact failing-row samples**, column-aware (suite `policy.pii_columns` + name heuristic; non-PII tested column may surface, everything else masked) | [`services/run_service.py`](../backend/app/services/run_service.py) `redact_sample_failures` | GDPR Art 25, 5(1)(c) |
| **Retention purge** of `sample_failures` AND list-shaped `observed_value` (#1253 — only the set-oriented-expectation shape; scalar aggregates are never purged) after `sample_failures_retention_days` (default 30), keeping only the non-PII `metric_value`; stamps an auditable `sample_failures_purged_at` | `purge_sample_failures` daily beat ([`worker/tasks.py`](../backend/app/worker/tasks.py)) | GDPR Art 5(1)(e) storage limitation |
| **Secret isolation** — `SecretStore` seam (Azure Key Vault impl via managed identity); secrets never in git-tracked files, never logged | [`core/secrets.py`](../backend/app/core/secrets.py); CLAUDE.md secret rules | GDPR Art 32 / HIPAA §164.312(a) |
| **Encryption in transit** — Postgres `sslmode=require`; HTTPS ingress | [`deploy/terraform/azure/postgres.tf`](../deploy/terraform/azure/postgres.tf) | GDPR Art 32 / HIPAA §164.312(e) |
| **Encryption at rest** — Azure platform-managed keys on Postgres / Key Vault / Storage (default) | Azure platform default (not asserted in IaC — see gap G5) | GDPR Art 32 / HIPAA §164.312(a)(2)(iv) |
| **Access control** — two axes: suite-scoped authz (owned-or-shared) **and** stored workspace roles (Admin / Member / Viewer), OIDC SSO (Azure AD + Cognito validated) | suite authz, `users.role` (ADR 0033), generic OIDC client + `fastapi-azure-auth` | GDPR Art 32 / HIPAA §164.312(a)(1) |
| **Config-change history** — Type-4 snapshot tables (`check_versions`, `connection_versions`); credentials never snapshotted | ADR 0020 | GDPR Art 5(2) accountability (partial) |
| **Cross-entity audit log** — append-only `audit_events`: every config mutation by a principal (actor, action, entity, before/after, `request_id`), written **inside the mutation's transaction** so an applied change and its record cannot diverge. Per-entity payload allow-list — no credential, no warehouse data. Workspace-admin read endpoint; own retention clock | ADR [0041](adr/0041-history-audit-strategy.md), #1318 | GDPR Art 5(2)/30 accountability; HIPAA §164.312(b) **partial** — config events only, data *reads* are #431 |
| **Data residency is deployable** — provider-agnostic seams (ADR 0010); a controller can deploy into their own jurisdiction's region | ADR 0010 / 0013 | GDPR Ch. V transfers |
| **(Post-v1) LLM transfer minimization** — schema-only, PII-redacted context; local-endpoint option; no key-proxy | [`docs/post-v1-dq-intelligence-notes.md`](post-v1-dq-intelligence-notes.md) | GDPR Ch. V / HIPAA minimum-necessary |

> **Access-control row — workspace roles are now stored, and manageable in-app (ADR
> [0033](adr/0033-workspace-roles-rbac.md), shipped #740–#742).**
> Authorization is two orthogonal axes. The **workspace role** (`users.role`:
> `admin | member | viewer`) says what kind of principal you are; the **per-suite grant**
> (`view` / `edit`) says what you may touch. Both are enforced server-side on REST and MCP
> identically, and both resolve **per request** — so a demotion takes effect on the target's
> next call, including calls made with tokens they already hold. There is no session or token
> to revoke, and therefore no window in which a revoked privilege is still honoured.
>
> Two consequences an auditor should know, stated plainly rather than buried:
>
> - **Least privilege tightened materially.** Connections — the objects that hold warehouse
>   credentials — can now be created, edited, deleted or re-credentialed **only by an Admin**.
>   Before #741 *any* authenticated user could delete or re-point the connection every suite in
>   the workspace ran on. **Viewer** is a genuine read-only tier: it cannot author, cannot be
>   granted `edit` (rejected at grant time *and* capped at the point of use, so a demotion
>   immediately downgrades grants the user already held), and cannot open an outbound
>   connection test.
> - **The `WORKSPACE_ADMIN_EMAILS` allowlist remains an admin-minting path, by design.**
>   It is now a *bootstrap seed and lockout break-glass* rather than the admin mechanism: it
>   only ever grants, never demotes, and the last-admin guard deliberately does **not** count
>   allowlist-resolved admins toward its invariant (an env entry can disappear on the next
>   deploy). The residual risk is explicit and unchanged from earlier releases: **anyone who
>   can set environment variables on the API container can mint themselves a workspace admin.**
>   Treat env write-access to the API as equivalent to workspace-admin in your access review,
>   and keep the allowlist empty in steady state once an in-app Admin exists.
>
> **Role changes are now audit-*tabled*, not only audit-logged (updated 2026-08-17).** Every
> change writes an `audit_events` row (`user.role_change`, with both the old and the new role)
> **inside the same transaction as the change**, so a refused or no-op change leaves nothing
> behind and an applied one cannot go unrecorded. The structured log line is kept alongside it,
> `request_id`-correlated. Queryable at `GET /api/v1/admin/audit-events`.
>
> **Tamper-evidence remains open**, and the distinction matters for an auditor: the table is
> append-only in the app and carries a `REVOKE UPDATE, DELETE` from the application role, which
> stops accidental in-app mutation and **nothing stronger** — that role owns the table and can
> grant the privileges back. Real tamper-evidence needs an external cryptographic anchor and is
> tracked with **#431**. This entry is distinct from the G1 *read*-access audit below, which is
> still open.

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

### G1 — 🟢 Data-*access* audit trail (the HIPAA gate) — #431 — **read events shipped; tamper-evidence open**
**Requirement:** HIPAA §164.312(b) **audit controls** require a durable record of *who
accessed which PHI*. GDPR accountability (Art 5(2) / 30) wants processing records too.
**Current state (updated 2026-08-17):** the *"revisit ADR 0020"* instruction has been
discharged — ADR [0041](adr/0041-history-audit-strategy.md) **accepts** the cross-entity
audit log, and **phase 1 has shipped** (#1318): an append-only `audit_events` table, an
`audit_service` seam with a per-entity payload allow-list, every one of the 35 mutating
`/api/v1` routes either audited or explicitly exempted behind a route-table coverage
guard, a workspace-admin read endpoint (`GET /api/v1/admin/audit-events`) and a daily
retention sweep on its own `AUDIT_RETENTION_DAYS` clock (default 365, deliberately
decoupled from `SAMPLE_FAILURES_RETENTION_DAYS` — the two protect opposite things).

Three acts that previously left **no trace of any kind** are now recorded: **share
grants/revokes** (the finest-grained permission in the product), **credential rotations**
(`connection.reauth`, a hole ADR 0020 shipped knowingly), and **workspace-role changes**
(which emitted a log line and nothing durable, while ADR 0033 §7 requires a durable
record).

**Phase 2 — data-*read* events — has now shipped too (#431).** Reading a run's results
records an `action_class='access'` event naming **who**, **which run**, **when**, and —
the field that makes the log answer the question it exists for — **whether regulated data
was actually surfaced**. A read whose sample came back fully redacted exposed nothing, and
recording it identically to one that surfaced real failing rows would bury the handful of
events an investigator wants among the many they do not. Covered on **REST and MCP**, the
latter tagged `surface: "mcp"`, because an LLM client may carry a value onward in ways a
browser session does not.

The event records **which** result was read, never **what** it contained (ADR 0041 §2.6.3)
— copying a sample into an append-only table with a longer retention would quietly turn
the audit log into a second, unpurged copy of the personal data it audits, defeating both
the #1253 purge and the G2 erasure path.

**Two contracts differ from phase 1, deliberately.** A read event is **not** fail-closed:
a failed audit write logs loudly (`audit_access_write_failed` at ERROR) and the read still
succeeds, because failing a legitimate read over a bookkeeping problem trades a real
outage for a smaller one. And the write is **one event per read, not per result**, so its
cost does not scale with the data — measured at roughly one Postgres commit, on the
request path. Moving it off the request path was considered and rejected: a deferred write
can be lost when a process dies, and "we usually record accesses" is the property an audit
control exists to rule out.

**What remains open for #431 is tamper-evidence**, which ADR 0041 §2.7 is explicit needs
cryptographic chaining anchored *outside* the database. The `REVOKE UPDATE, DELETE` is a
guard against **accidental** in-app mutation and is **not** tamper-resistance: the table's
owner (`dataq_app`) can grant the privileges straight back — the retention sweep does
exactly that, by necessity — and splitting the database role to prevent it is rejected on
a stronger security constraint. An operator whose regime requires a provably unaltered log
needs that anchor; one that requires a durable, queryable, admin-gated record of who read
which PHI now has it.

⚠️ **Not yet verified in production.** All of the above is proven against local Postgres,
including in the production role/ownership shape. It has not been observed on the deployed
Azure or AWS stacks.
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

### G3 — 🟢 Authoritative PII/PHI classification (not just a heuristic) — #433 — **warehouse tags consumed; verification pending**
**Requirement:** GDPR special-category data (Art 9) / HIPAA PHI must not leak via the
**surfacing** path. Today `redact_sample_failures` surfaces the *tested* column when it's
not flagged PII — but flagging is a **name-token heuristic + optional suite policy**, so a
mis-named column (`field_7` holding SSNs) can surface unredacted (false negative).
**Current state:** default-redact everywhere *else* limits blast radius; the gap is the
surfacing exception trusting best-effort classification.
**Shipped (2026-08-18): the fail-closed mode**, `column_policy.require_classification`,
per suite and off by default. With it on, nothing row-level is surfaced unless a column is
**explicitly** cleared — the operator's `identifier_column`, or a datasource governance tag
saying non-sensitive. The name/value classifier is not consulted, because consulting it is
the risk being removed.

Off by default deliberately: a fully-masked failing row is *unactionable* — you cannot see
what was wrong or which row — so this is a trade an operator makes for a regulated dataset,
not one made for them.

**Measuring the gap corrected our description of it.** The `field_7`-holding-SSNs example
does **not** leak through an *incidental* column: those already default-mask (#415). It
leaks through the **tested** column, which is shown by design (seeing the failing value is
the point) and is gated by the *affirmative* sensitivity test — and a meaningless name is
not affirmatively anything. So the exception is narrower than this gap originally described
and lands squarely on the one column the operator asked about. Fail-closed closes exactly
that path, and the precedence matrix is now pinned by tests (tag > explicit mask > explicit
show > classifier), which it previously was not: two isolated cases were covered, so an
inversion making the suite policy override a governance tag would have passed.

**Shipped (2026-08-18): the authoritative source.** `services/column_tags.py` reads column
classifications from **Snowflake** (`dataq_classification` plus Snowflake's own
`PRIVACY_CATEGORY`) and **Unity Catalog** (`dataq_classification`), caches them on the
asset on each run, and feeds them to the governance floor of the redaction ladder — the
rung a suite policy cannot lift, on REST *and* MCP. The convention is documented for
customers in [docs/security.md](security.md).

**A fixed convention rather than a per-connection mapping**, deliberately: a mapping would
make itself an unreviewed security control, where one typo silently un-masks a column.

**Cached on the ASSET, not on each result**, so a tag applied today masks samples captured
yesterday — a classification is a statement about the data, not about the moment it was
read.

**Every failure is silence.** No permission on the tag, a missing `information_schema`, a
dead warehouse: `{}` and a log line, never an exception into a run and never an inferred
clearance. That direction matters because fail-closed mode treats a non-sensitive tag as a
*clearance*, so a fetcher that guessed on failure could un-mask data.

**Limits, stated rather than left to be discovered.** Only Snowflake and Unity Catalog have
a column-tag concept at all; for ADLS, S3, Iceberg and flat files the classification remains
the suite policy, the classifier and fail-closed mode. That is a platform limit, not an
implementation gap.

**Live-verified 2026-08-18 against the real warehouses**, which is the only evidence that
counts across a driver boundary (the #953 rule). Both unknowns are settled and one gap
remains:

* **Unity Catalog — fully verified end to end.** A `dataq_classification = 'pii'` tag was
  applied to a real column, read back through the shipped `fetch_column_tags` as
  `{'customer_id': 'sensitive'}`, and removed; the removal was confirmed by re-reading
  `information_schema.column_tags`, not by trusting the cleanup's own log line.
* **Snowflake — the read path verified.**
  `INFORMATION_SCHEMA.TAG_REFERENCES_ALL_COLUMNS` **executes under DataQ's own
  `DATAQ_READER` role** and returns cleanly. That settles the larger unknown: no
  `ACCOUNT_USAGE` grant is needed, so classifications are read fresh rather than with that
  view's up-to-two-hour lag.
* **Snowflake — the apply/read-back half is NOT verified, and cannot be with the
  credentials that exist.** Both stored Snowflake credentials are **role-scoped
  programmatic access tokens** (one `DATAQ_READER`, one `DATAQ_LOADER`); neither role holds
  `CREATE TAG` on the schema, and a PAT cannot assume `ACCOUNTADMIN` even though the
  underlying user is granted it. Verified by inspecting the grants rather than inferred from
  an error message.

  So what remains unproven is narrow and specific: the `LEVEL = 'COLUMN'` filter and the
  upper-to-lower column-name folding, **against real Snowflake rows**. Closing it needs
  either a `CREATE TAG` grant on the target schema or an admin credential — a deliberate
  privilege decision, not something to grant in passing.

  Worth noting that the failure is itself a small piece of evidence: least-privileged roles
  genuinely cannot apply governance, which is the separation this feature assumes.

### G4 — 🟢 Region / residency assertion & enforcement — #434 — **asserted, surfaced, and its one exception accepted on the record**
**Requirement:** GDPR Ch. V — EU personal data must stay in-region; cross-border transfer
needs a lawful basis. The post-v1 LLM call is a new transfer vector.
**Current state (as filed):** the seam *allowed* an EU deploy but nothing documented or
enforced jurisdiction, and the LLM-transfer mitigation was design-only. The filing text also
described the deploy as "region-pinned to US (westus3)", which turned out to be half right
and is corrected below: the **database** is in West US 3, the **app** is in West US 2, and
nothing had noticed.
**Shipped (2026-08-18).** A documented **residency matrix** per resource for both
reference deployments ([docs/security.md](security.md)), a `DEPLOYMENT_REGION` declaration
surfaced at `GET /api/v1/admin/deployment` (workspace-admin only) so the posture is
readable without shell access, and — the part that is a control rather than a document —
an IaC **postcondition** that fails the plan when the shared Azure Container Apps
environment's region disagrees with `azure_location`.

That last one closes a real hole rather than a theoretical one: the environment is shared
and declared as a `data` source, so **every Container App and Job inherits its region**.
Moving or recreating it elsewhere would have relocated all of the app's compute with a
clean `apply` and no signal, and "we did not notice the jurisdiction changed" is precisely
the Ch. V failure.

The matrix states its three honest exceptions rather than omitting them — the shared Postgres
region split described below, CloudFront's global
edge (fingerprinted static assets only; no API response or failing-row sample is cached)
and the WAFv2 ACL, which exists only in `us-east-1` regardless of the stack's region and
holds rules, not data.

**External transfers are enumerated, not derived** — alert delivery, telemetry, and the
unbuilt LLM seam, which is listed while disabled on purpose so an auditor sees it was
considered rather than inferring its absence.

**Building the control immediately found a live discrepancy, which is the strongest
argument for it.** The Azure Postgres server — the only resource holding personal data —
is in **West US 3** while the app's other resources are in **West US 2**. Verified against
running Azure, not inferred, and an earlier draft of the matrix had asserted they agreed.

**Consciously accepted** as a recorded exception ([#1465](https://github.com/TheurgicDuke771/DataQ/issues/1465),
closed on that decision), on a stated basis rather than a shrug: both regions are in the
**same jurisdiction** (United States), which is the unit GDPR Ch. V keys on, so no personal
data crosses a border. Resolving it would mean a new server plus a data migration against
the same 1-server subscription cap that forced the shared server in the first place — the
cap that also blocks CMK in G5.

The acceptance is encoded as a `shared_pg_expected_location` variable rather than left in
prose, so the `check` block still **passes today and fires on a real move**. Checking the
server against `azure_location` would have warned on every plan forever, and a
permanently-firing check is noise people learn to skip — it would have masked exactly the
drift that matters. An accepted exception must not cost you the detector.

**The constraint this places on a future deployment is stated in
[docs/security.md](security.md):** an operator whose regime cares about sub-national
placement, or who deploys into the EU where a two-region split could straddle adequacy
boundaries, must consolidate rather than inherit this exception.

**What this does not do, stated plainly:** the app **declares** a jurisdiction, it does not
verify one. Software cannot confirm which datacentre its database sits in; the IaC pins it
and the deploying organization attests to it. `DEPLOYMENT_REGION` reads as `null` when
unset, so an undeclared deployment shows a gap rather than a default. The LLM posture
remains design-only until that feature exists.

### G5 — 🟢 Assert encryption-at-rest & offer CMK — #435 — **documented; CMK deferred with reasons**
**Requirement:** GDPR Art 32 / HIPAA §164.312(a)(2)(iv) addressable encryption.
**Current state:** satisfied by Azure platform-managed keys (default), but our OpenTofu
neither asserts it nor offers customer-managed keys, and it's undocumented (no evidence
for a customer security review). The 2026-08-16 audit also found the AWS stack's
**ElastiCache at-rest encryption off** (RDS beside it is `storage_encrypted = true`) —
[#1385](https://github.com/TheurgicDuke771/DataQ/issues/1385). It holds broker payloads and
rate-limit counters rather than customer data, but it is exactly the asymmetry a security
review would flag, and the fix is a one-line assert.
**Resolution (2026-08-17).** The original framing — *"satisfied … but our OpenTofu neither
asserts it nor offers CMK, **and it's undocumented (no evidence for a customer security
review)**"* — named documentation as the actual deliverable, and that is what shipped:
[docs/security.md](security.md) now carries a **per-resource at-rest table for both
reference deployments** (what is encrypted, with which key, with a first-party citation),
which is the artifact a security reviewer asks for.

Two corrections to this gap's premises, both recorded rather than quietly dropped:

- *"No cloud target"* is stale — Azure has been applied since 2026-06-28 and AWS since
  2026-08-15.
- **"Assert it in IaC" is mostly not expressible.** Azure Postgres, Key Vault, Log
  Analytics and App Insights encrypt at rest *unconditionally and by platform default*;
  there is no Terraform attribute to assert. The one place an assertion is both possible
  and meaningful is AWS RDS (`storage_encrypted = true`), where our stack owns the
  database — and it is already set. So the IaC half of this gap was largely a category
  error, and saying so is more honest than adding a no-op attribute that looks like a
  control.

**CMK is deferred, with three independent reasons** (full detail in
[docs/security.md](security.md)): it is **creation-time-only** on Azure Postgres and
therefore a data migration rather than a toggle; our IaC does not own the database server
(1-server subscription cap → shared server, declared as a `data` source); and our Key Vault
is deliberately purge-protection-off, which makes it the wrong custodian for a key whose
loss takes the database offline. Revisit if a customer requires key custody, or when a
stack owns its own database from creation.

**Still open:** [#1385](https://github.com/TheurgicDuke771/DataQ/issues/1385) — ElastiCache
at-rest encryption is off beside an encrypted RDS.

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
| **HIPAA** | Customer processes **PHI** | Encryption + access control + minimization present; **audit controls now present** — config changes *and* data reads are recorded and admin-queryable (G1) — with **tamper-evidence still open**; needs a BAA (G6) | §164.312 technical safeguards met once tamper-evidence lands; BAA still org-side |

## 4. The honest marketing line

- **v1 today:** "Privacy-by-design data handling — PII redaction, configurable retention,
  least-privilege secrets, suite-scoped access control."
- **v2.x (after G1–G5):** "Processor-grade technical controls for GDPR / CCPA / HIPAA
  workloads — access audit trail, data-subject-rights tooling, authoritative
  PII/PHI classification, region-pinned residency, customer-managed-key option."
- **Never:** "DataQ is GDPR/HIPAA *certified/compliant*" — compliance is a property of the
  *deployment + organization*, which DataQ enables but cannot unilaterally satisfy.
