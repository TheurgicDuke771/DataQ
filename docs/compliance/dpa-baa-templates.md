# DPA & BAA templates

> ⚠️ **COUNSEL REVIEW REQUIRED — these are engineering drafts, not legal
> instruments.** They exist so the legal half of compliance gap G6 is *drafted
> with accurate technical annexes* instead of assumed; nothing here has legal
> sign-off, and executing either document without counsel review is explicitly
> out of contract for the DataQ project (an engineering team cannot sign off a
> DPA/BAA — [#1452](https://github.com/TheurgicDuke771/DataQ/issues/1452)).

**When you even need these.** DataQ ships customer-deployed BYOL (ADR 0013): the
deploying organization runs the software on its own infrastructure, and DataQ (the
project/vendor) never touches the data. In that shape there is usually **no
DataQ-side DPA/BAA to execute at all** — your DPAs run between you and your cloud
provider, IdP, and alert-channel vendors (see the
[sub-processor disclosure](sub-processors.md)). These templates matter for two
real cases:

1. A **service provider deploys and operates DataQ for a controller** (an MSP, a
   consultancy, an internal shared-services org with its own legal entity) — that
   operator is a processor / business associate and needs paper.
2. A future **DataQ-hosted offering** — not currently offered; if it ever is,
   these templates are the starting point, not the finish line.

The **technical annexes** below are the part only this project can supply and the
part counsel cannot invent — they are accurate against the shipped software as of
the review date and cross-referenced to the living docs so they don't rot.

---

## Template A — Data Processing Agreement (GDPR Art 28)

*Parties:* the **Controller** (the organization whose data is monitored) and the
**Processor** (the organization operating the DataQ deployment).

1. **Subject matter & duration.** Processing of personal data incidental to data
   quality monitoring of the Controller's data stores, for the term of the
   underlying services agreement.
2. **Nature & purpose.** Execution of data-quality checks against
   Controller-designated tables; storage of bounded failing-row evidence samples;
   alerting; workspace administration. No profiling, no automated decision-making
   about data subjects, no sale of data.
3. **Categories of data & subjects.** As determined by the Controller's choice of
   monitored tables (Class 1) plus workspace user accounts (Class 2) — the
   authoritative inventory is the [DPIA input sheet](dpia-input-sheet.md), which
   is incorporated by reference.
4. **Controller instructions.** The Processor processes only per documented
   instructions — in DataQ terms: the suites, checks, column policies, retention
   settings, and notification targets the Controller configures ARE the
   instructions, and the audit trail (G1) is the record of them.
5. **Confidentiality.** Persons authorized to process are bound by
   confidentiality; workspace access is role-gated (ADR 0033) and per-suite
   grants (ADR 0027).
6. **Security of processing (Art 32).** The Processor maintains the controls in
   [Security & data handling](../security.md), including: secrets in a dedicated
   store, PII redaction at the logging layer, column-aware sample redaction with
   a warehouse-tag governance floor and a fail-closed mode, retention-bounded
   sample storage, append-only audit of config changes and data reads, TLS on
   public surfaces, rate limiting, least-privilege database role, non-root
   containers.
7. **Sub-processors.** Per the [sub-processor disclosure](sub-processors.md);
   general authorization with prior notice of changes, per its documented update
   process. *(Counsel: choose general vs specific authorization.)*
8. **Data-subject rights assistance (Art 15/17/20).** The Processor assists via
   the platform's levers: retention purge, entity cascade deletion, and — once
   [#432](https://github.com/TheurgicDuke771/DataQ/issues/432) ships — targeted
   subject erasure/export. **Until #432 ships, the DPA must not promise on-demand
   subject-level erasure**; it may promise erasure within the configured
   retention window (default 30 days) or by deletion of covering results.
9. **Breach notification (Art 33).** The Processor notifies the Controller
   without undue delay after becoming aware, with the content of the
   [breach-notification runbook](breach-notification-runbook.md) §2–§3; the
   Controller owns supervisory-authority notification.
10. **Transfers.** Only the enumerated vectors of the sub-processor disclosure;
    residency is asserted per the deployed region posture and readable at
    `GET /api/v1/admin/deployment`. *(Counsel: SCCs/adequacy per destination.)*
11. **Deletion/return at end of provision.** Deletion of the workspace database
    deletes all Class 1 and Class 2 data; suite export (JSON) provides
    configuration return. Warehouse data is untouched by termination — DataQ
    holds no copy beyond §3's samples.
12. **Audit & information.** The Controller may audit via the admin surfaces
    (audit trail, deployment endpoint) and, for the software itself, the public
    repository; on-site audit terms are for counsel.

---

## Template B — HIPAA Business Associate Agreement

*Parties:* the **Covered Entity** and the **Business Associate** (the DataQ
operator). Applies **only** when the Covered Entity points DataQ at PHI.

1. **Permitted uses.** The BA uses PHI solely to provide data-quality monitoring
   services: check execution, bounded failing-sample evidence, alerting. No other
   use or disclosure.
2. **Minimum necessary.** DataQ's design enforces minimization structurally:
   samples are bounded and retention-limited; the redaction ladder masks
   classified columns on every read surface; fail-closed mode
   (`require_classification`) makes unclassified columns unreadable for suites
   the Covered Entity so designates. The BA will configure column policies /
   warehouse tags over PHI columns (G3).
3. **Safeguards (§164.312 technical).** Access control (unique user identity,
   role + suite grants, per-request revocation), audit controls (append-only
   config **and** read events — with the tamper-evidence limitation of
   [#1460](https://github.com/TheurgicDuke771/DataQ/issues/1460) disclosed rather
   than overclaimed), integrity and transmission security per
   [Security & data handling](../security.md).
4. **Reporting.** Security incidents and breaches of unsecured PHI reported per
   the [breach-notification runbook](breach-notification-runbook.md); the Covered
   Entity owns individual/HHS notification and its clocks.
5. **Subcontractors.** Only the enumerated vectors of the
   [sub-processor disclosure](sub-processors.md), each under equivalent
   restrictions. Note for MCP: PATs held by the Covered Entity's own users expose
   redacted results to the AI client of **the token holder's choice** — the BAA
   should either bind that choice or the Covered Entity should not issue PATs on
   PHI-scoped workspaces.
6. **Access/amendment (§164.524/.526).** PHI in DataQ is transient evidence, not
   a designated record set; requests route to the source systems. *(Counsel:
   confirm the designated-record-set analysis.)*
7. **Termination.** Destruction of the workspace database destroys all retained
   samples; certification terms for counsel.

---

*Both templates last reviewed 2026-08-21 against the shipped software. Any PR
that changes a referenced mechanism (retention, redaction, audit, transfers)
should re-check the annex claims — same neighbour-aging rule as the tool
docstrings.*
