# Post-v1 roadmap — the single home for deferred work

> **What this is:** the canonical index of everything deliberately deferred past DataQ v1 —
> design themes (with their detailed design docs) **and** the concrete issue backlog. Every
> issue on the GitHub **`v1.1 Backlog`** milestone (renamed 2026-07-04 from
> `Backlog (post-v1 / testing)`) is, by definition, post-v1
> and is mirrored here under the theme it belongs to.
>
> **What this is not:** a commitment or a schedule. v1 is the 8-week product (see
> [DataQ_platform_roadmap.md](DataQ_platform_roadmap.md) +
> [progress-v1.md](../docs/progress-v1.md), the archived v1 ledger). These are the things we consciously chose *not* to build for v1,
> captured so the intent isn't lost.
>
> **Source of truth for status:** the GitHub milestone. This doc is the human-readable map;
> when an issue closes, it closes on GitHub — don't hand-maintain `[x]` here.
>
> **Why it lives in `context/`:** alongside [DataQ_platform_roadmap.md](DataQ_platform_roadmap.md),
> this is the **input for a post-v1 week-wise task generator** — once v1 ships, this themed backlog
> + the design docs feed the planner that produces the v1.x / v2.x weekly task breakdown. Keep it
> generator-friendly: themes with intent, issue refs, and pointers to the detailed design.

**Detailed design docs** (the "why" + the shape):
- [post-v1-admin-ui-notes.md](../docs/post-v1-admin-ui-notes.md) — admin / access model / UI & IA
- [post-v1-dq-intelligence-notes.md](../docs/post-v1-dq-intelligence-notes.md) — expectation expansion, LLM-assisted authoring, marketplace
- [compliance-posture.md](../docs/compliance-posture.md) — GDPR / CCPA-CPRA / HIPAA technical controls + gap list

**The big picture:** v1 ships the DQ loop (checks → results → trends → freshness/volume monitors
→ alerts → MCP) end-to-end. Post-v1 layers *intelligence* (more expectations, LLM authoring,
the remaining monitor kinds), *governance* (admin console, compliance controls), and *scale*
(performance hardening, second-impl seams) on top of it.

---

## v1 maturity assessment — where the product honestly stands (2026-07-03)

> A calibrated self-assessment recorded at Week-7 close so post-v1 prioritisation starts from
> reality, not from the changelog. Verdict: **strong engineering artifact, ~4/10 as a
> competitive DQ product today.** Confidence it does what it's configured to do: ~8/10
> (live-verified). Confidence a data team should pick it over Soda/Elementary/GX Cloud today:
> ~3/10. Confidence the architecture can close the gap: ~7/10 — the seams are the right ones.
> One-liner: v1 is a well-engineered, security-conscious **"GX-as-a-service with orchestration
> awareness"** — credible as a single-team internal tool; not yet in the conversation with
> commercial DQ platforms.

**What's genuinely strong (defend without hesitation):**
- End-to-end **verified**, not claimed: 4 datasource run paths green against live infra;
  orchestration-triggered checks proven (ADF failure → visible in 4m14s; trigger-on-success
  firing off real pipeline runs). Trigger-on-pipeline-success is a real differentiator over
  cron-only checkers.
- The expensive-to-retrofit architecture is already right: monitor-kind seam (ADR 0012),
  adapter/runner registries (ADR 0011), `ResultPublisher`, provider-agnostic infra (ADR 0010).
- Security/PII posture well above v1 norm: logger-level + column-aware redaction, tested authz
  matrix, fail-closed MCP, incident-driven hardening. Alerting has dedup/snooze/severity routing.
- MCP integration is ahead of the market curve and tested end-to-end.

**The honest gaps (each mapped to where it's tracked):**

| # | Gap | Why it matters | Where tracked |
|---|---|---|---|
| G-a | **Monitors only what you remember to check** — hand-authored expectations; no anomaly baselines, no schema-drift, no auto-suggested checks. Category leaders' core pitch (Monte Carlo/Anomalo) is the inverse: automatic coverage. An unknown-unknown incident sails past DataQ. | Existential for "product"; the single biggest gap | **Theme 1** (remaining monitor kinds: `schema_drift`, `anomaly`) + **Theme 2** (auto-suggestion/LLM authoring) |
| G-b | **Scale unproven; structurally weak on 2 of 4 paths** — flat-file + UC runners load the whole file/table into worker pandas; largest table ever validated is a few thousand synthetic rows. No sampling/partition/incremental strategy for checks. (Snowflake pushes down via SQL — fine.) | 100M-row table = worker OOM; blocks any serious deployment | **Theme 7** (perf & scale — add: sampling/partition-aware/incremental check execution as a named workstream) |
| G-c | **Every "verified" is self-referential** — one operator, ~1 week live, against a harness built by the same author. Zero real-world data-pathology exposure at scale (schema churn, half-written files, encoding chaos in volume). | Unknown failure modes in the first real deployment | **Theme 10** (test-hardening) + first-real-workload milestone, post-v1 |
| G-d | **No incident workflow, no lineage, no ownership routing** — runs + alerts exist; "what broke downstream / who owns it / when was it resolved" doesn't. No data-access audit trail (the HIPAA gate). | This is what DQ products are bought for | **Theme 9** (results/reporting depth) + **Theme 4** / [#431](https://github.com/TheurgicDuke771/DataQ/issues/431) (audit trail); lineage/incident objects = new design doc needed; the incident-*narrative* half is design-captured as **Theme 2's agentic root-cause analysis** (2026-07-04); lineage may be *pulled/emitted* rather than built — **Theme 14** governance-catalog + OpenLineage capture (2026-07-04); incident objects anchor to the **asset entity** (Theme 3's Asset-first IA capture, phase 1 = the shared prerequisite) |
| G-e | **Single-tenant, config-allowlist admin, one validated IdP** — fine internally, not sellable. | Blocks multi-team/commercial use | **Theme 3** (admin/access) + ADR 0026 / [#461](https://github.com/TheurgicDuke771/DataQ/issues/461) (API keys) |
| G-f | **Ecosystem: 4 datasources** vs the 30–50 a category product ships; no dbt integration (seam reserved); can't check the Postgres it runs on. | Adoption ceiling | **Theme 8** (datasource depth; generic-RDBMS adapter is the cheap first win) |
| G-g | **Engine risk: GX Core pin** — the product's core capability rides a fast-moving third party with documented API drift; DQX swap-in shape exists for UC only. | Strategic dependency | **Theme 2**'s engine-abstraction watch item (below) — no issue until the churn trigger fires |
| G-h | **Harness Databricks = Free Edition, non-commercial-only** — the UC demo leg cannot legally back a commercial demo, while ADR 0013's ambition is commercial BYOL. **Decision recorded 2026-07-03 (go-live):** acceptable while the deployment is demo/eval; trigger stands — before any commercial demo/use → paid workspace. | Licence landmine on the demo path | ADR 0021/0013 context — recorded in the go-live checklist ([progress-v1.md](../docs/progress-v1.md)); re-trigger **before any commercial demo** |
| G-i | **Pre-marketplace teardown: the deployed app still carries the demo harness** — Flows A/B/C, the 5 harness connections (Snowflake/UC/ADLS/ADF/Airflow), demo suites/users, and the seeded-breach check are all ADR-0021 *test* fixtures, not product. Before any marketplace listing / distributable image / customer-facing deployment (ADR 0013): remove the harness flows + datasource connections + demo users from the reference deployment, and verify nothing in the shipped artifact references harness endpoints. (Noted 2026-07-03 with the G-h decision.) | A demo fixture shipping as if it were product surface — licence (G-h) + credibility + stale-credential risk | ADR 0013 pre-listing checklist item — this row is the note; fold into the marketplace workstream when Theme 2's marketplace work is picked up |

**Prioritisation signal for the post-v1 planner:** G-a and G-b are the two that change what the
product *is* (coverage-without-authoring + scale-safe execution); G-d is what makes it *usable in
anger* (incidents/lineage); the rest are adoption/positioning. Recommended post-v1 opening
sequence: Theme 1 `schema_drift` + `anomaly` (rides the ADR 0012 seam, no schema rewrite) →
scale-aware execution (G-b, Theme 7) → incident/lineage design doc (G-d).

---

## Theme 1 — Monitor kinds (the remaining reserved kinds)

ADR [0012](../docs/adr/0012-monitor-kind-seam.md) reserved five non-expectation monitor kinds behind the
`check.kind` discriminator + numeric `metric_value`. **`freshness` + `volume` shipped end-to-end
in Week 7** (out of roadmap — run engine #426 + authoring UI #437). The rest stay reserved:

| Kind | Status | Home |
|---|---|---|
| `freshness`, `volume` | ✅ **shipped** (W7, #426/#437) | ADR 0012 |
| `schema_drift` | 🔵 reserved (422 today) | ADR 0012 |
| `anomaly` | 🔵 reserved (422 today); needs a baseline/seasonality model | ADR 0012 |
| `comparison` (cross-dataset reconciliation) | 🔵 reserved; reuse the FastAPI_DataComparison engine; needs the two-connection model | ADR [0014](../docs/adr/0014-reconciliation-comparison-check-kind.md) → ADR 0015 (pending) |

**Monitor-engine follow-ups** (from the #426/#437 landings):
| # | Title |
|---|---|
| [#427](https://github.com/TheurgicDuke771/DataQ/issues/427) | Reuse one warehouse connection per monitor run (avoid double-connect + per-call engine) |
| [#428](https://github.com/TheurgicDuke771/DataQ/issues/428) | Consolidate SQL-identifier validation + dedup `run_monitors` engine boilerplate across SQL runners |
| [#429](https://github.com/TheurgicDuke771/DataQ/issues/429) | `MonitorRunner` gate uses `isinstance` on a `runtime_checkable` Protocol (name-only match) |

---

## Theme 2 — DQ-intelligence (expectation expansion, LLM authoring, marketplace)

Full design: **[post-v1-dq-intelligence-notes.md](../docs/post-v1-dq-intelligence-notes.md)**. The enabler is
already true in v1 — no server-side expectation allowlist, so "add an expectation" is mostly a
frontend-catalog + config-validation problem. Four themes: (1) the 5 high-ROI GX built-ins,
(2) LLM custom-SQL generator, (3) LLM curated check-suggestions, (4a) curated server-served
catalog + allowlist. LLM integration = admin-configured, default-off, BYO-credential `LLMProvider`
seam (schema-only, PII-redacted context).

| # | Title |
|---|---|
| [#124](https://github.com/TheurgicDuke771/DataQ/issues/124) | Add DQ-dimension classification to checks (Completeness / Uniqueness / Validity / …) |
| [#286](https://github.com/TheurgicDuke771/DataQ/issues/286) | Apache Iceberg v2 / v3 table-format support |

*(The built-ins / LLM / marketplace work is design-captured but not yet filed as discrete issues —
file from the detail doc when picked up.)*

**Engine-abstraction watch item (from maturity-assessment G-g):** the product's core capability
rides a pinned GX Core with documented API drift (CLAUDE.md §11); the DQX swap-in shape exists
for the UC runner only. If GX churn continues (or DQX/v1.1 lands), generalise that runner-level
engine seam beyond UC so the check engine is a pluggable impl, not a hard dependency. No issue
filed yet — file one when the trigger fires.

### Agentic root-cause analysis (design-captured 2026-07-04 — the category-leading bet)

**When a check fails, DataQ investigates — it doesn't just alert.** Every DQ product today stops
at detection ("row count dropped 40% 🔴") and a human spends the next two hours on why. DataQ's
moat for closing that loop already exists in the v1 schema: the `triggered_by`
`pipeline_runs` ↔ `runs` correlation (cron-only checkers structurally lack it), `metric_value` as
a SQL-aggregatable trend scalar (ADR 0012), the column profiler, and redacted failing samples
(#417). Two-layer, LLM-degradable design:

1. **Deterministic evidence card (no LLM anywhere).** On a `fail`/`critical` result, a Celery
   task assembles the dossier from existing data: the upstream pipeline run (status + duration/
   delay vs. its own history), the check's `metric_value` trend ("sudden vs. slow drift"), a
   profile-diff of the failing batch vs. the last passing one (which segment broke), sibling
   checks on the same table, and downstream suites (blast radius). Ships as a structured card on
   the alert via the existing `ResultPublisher` seam. Most of the triage value lives here, with
   zero new dependencies — **build this first**.
2. **LLM narrative + ranked causal hypothesis (optional).** Turns the dossier into a
   three-sentence diagnosis. Rides the **same admin-configured, default-off, BYO-credential
   `LLMProvider` seam** as the authoring work above (one seam, two features; endpoint/key in the
   SecretStore like any connection; any OpenAI-compatible/Anthropic/Azure-OpenAI/local endpoint).
   Context is schema-only + column-aware-redacted samples — the model sees what a non-admin user
   would. **Fail-open:** no LLM connection configured → layer 1 still ships in full.

**Zero-config interactive path:** the MCP server (Theme 13) exposes the same dossier as tools, so
a user's *own* Claude/Copilot is the reasoning engine and DataQ never holds an LLM key — the
push-narrative-into-the-alert version is the premium ergonomics, not a gate.

This is the **incident-narrative half of gap G-d** done LLM-natively, and it compounds with
Theme 1 (anomaly baselines sharpen the dossier) and Theme 5 (the card enriches the alert
payload). No issues filed yet — file the two layers as separate issues when picked up (layer 1
has no LLM dependency and can ship alone).

---

## Theme 3 — Admin, access model & UI/IA

Full design: **[post-v1-admin-ui-notes.md](../docs/post-v1-admin-ui-notes.md)**. v1 ships suite-level sharing
+ a read-only workspace-admin view (#289). A full RBAC console is gold-plating for a single-tenant
trusted-team tool; the market leaders lead with checks→results→trends→alerts, not user-lifecycle.

| # | Title |
|---|---|
| [#411](https://github.com/TheurgicDuke771/DataQ/issues/411) | Workspace-admin: workspace-wide view on Dashboard + Results (both are owned-or-shared scoped today) |
| [#412](https://github.com/TheurgicDuke771/DataQ/issues/412) | Admin page is read-only — allow workspace-admin write actions (manage shares / suites) |
| [#461](https://github.com/TheurgicDuke771/DataQ/issues/461) | **DataQ-issued API keys / service tokens** (REST + MCP) — see below |
| _(no issue yet)_ | **Data-asset-centric view** — the biggest IA gap (added 2026-07-04): the UI is suite-centric but users think table-centric ("is `orders` healthy?"). A dataset page aggregating every check, result, freshness state, and trend across all suites targeting that table/file pattern. The natural UI anchor for Theme 14's lineage/governance pull and the Theme-2 RCA blast radius; category leaders are asset-first for this reason. **Phase 2 of the Asset-first IA capture below** — build the asset entity first. |
| _(no issue yet)_ | **Global search / command palette (⌘K)** — jump to any suite/check/connection/run by name. The list APIs exist; disproportionate daily-use payoff for a small build. |
| _(no issue yet)_ | **First-run onboarding + empty-state pass** — guided connect → suite → check → run path and designed empty states. Low value for the current solo deployment, first-touch-critical for any marketplace/BYOL evaluator (ADR 0013); cheap to retrofit now vs. embarrassing at listing time. |
| _(no issue yet)_ | **Bulk operations on checks** — multi-select enable/disable/severity/snooze; one-at-a-time editing doesn't survive 50-check suites. |

**DataQ-issued API keys / service tokens (#461, ADR [0026](../docs/adr/0026-auth-api-keys-and-principal-seam.md) proposed).** Auth today is Azure-AD-only (delegated/SSO) for both REST and `/mcp` — the deepest remaining vendor lock-in (the `get_current_user` seam has one real impl; `users.aad_object_id` is Azure-shaped) and it blocks BYOL-on-AWS/GCP (ADR 0013) and headless/programmatic access (a long-lived scoped key beats a ~60-min refreshing token for CI / always-on MCP clients). The fix is a **second authenticator behind the same `get_current_user` seam** so the **REST API and MCP accept it identically** — never MCP-only — which also finally *exercises* the seam (ADR 0010). Phase it: **user-scoped PATs first** (inherit the owner's per-suite grants → zero new authz; optional read-only down-scope), defer standalone **service-account principals** (they force generalizing `aad_object_id` → a generic principal with pluggable identity bindings + non-user suite sharing). Credential bar: hashed-at-rest + show-once + prefix + expiry + revocation + audit, in a new `api_keys` table (not the retrievable-secret SecretStore), with key lifecycle tied to the owner so it can't outlive a deactivated account.

### Accessibility & inclusive UI (design-captured 2026-07-04 — a genuine blind spot)

Nothing in the repo mentions accessibility today — flagged in the 2026-07-04 UI review as the
gap most likely to convert from "nice" to **procurement checkbox** the moment a commercial buyer
with compliance requirements appears (it belongs next to Theme 4 in the "sellability" bucket).
Target: **WCAG 2.1 AA**, phased so it's cheap early instead of a retrofit crisis:

1. **Automated floor first** — wire `axe-core` into the existing Playwright E2E lane (25 specs
   already run in CI) + `vitest-axe` for component tests; fail CI on new serious/critical
   violations. This is the ratchet that stops regression before any manual work starts.
2. **Non-color severity cues** — severity is communicated almost entirely by color today
   (red/orange/green tags, chart series). Add icons/shapes/text alongside color and adopt a
   colorblind-safe palette (also fixes the recharts dashboards, where color is the *only*
   encoding). Highest user-impact single item — ~8% of male users can't reliably read the
   current severity language.
3. **Keyboard & focus audit** — full keyboard traversal of the 13-screen set; focus-trap +
   restore on the surviving drawers/modals (Share, version history, run progress, import);
   visible focus states; skip-to-content. antd gives a decent baseline; the custom
   drawer/table/editor compositions are where it breaks.
4. **Screen-reader semantics** — landmarks, table headers/captions on the results/runs tables,
   aria-live for async run-progress updates, and **text alternatives for every chart**
   (the dashboards are pure-visual today; a data-table fallback per chart covers both a11y and
   the PDF-export path, #345).
5. **Related, decide-later:** reduced-motion support (cheap, ride item 2), dark mode (already
   Theme 12), i18n/locale externalization (defer until a non-English prospect exists — but stop
   hard-coding user-facing strings in new code *now*, which costs nothing).

No issues filed yet — file items 1–4 as separate issues when picked up; item 1 can land
independently as a Theme-10-style CI ratchet.

### Asset-first IA (design-captured 2026-07-04 — the lineage-era navigation model)

**When lineage lands (Theme 14), invert the navigation from suite-first to asset-first — but
split the noun from the verb rather than replacing one with the other.** Assets become what
users *browse and reason about* (health, lineage, incidents, blast radius); suites remain how
checks *execute* (batching, scheduling, trigger bindings, notifications, sharing). Making assets
primary for *everything* would fight three structural facts: authz is suite-scoped (ADR 0027)
and re-keying permissions to assets is a painful migration for little gain; scheduling /
triggers / notifications are genuinely batch-level operational concepts; and GX itself is
suite-shaped, so the engine grain stays suites regardless. The products that do this well (dbt
models-vs-jobs, Monte Carlo assets-vs-monitors) all run this two-axis model.

**The enabling primitive — build once, four features consume it:** a first-class **asset
entity**. Today "the table" exists only implicitly inside `Suite.target` and check configs.
Lineage needs asset *nodes*; the asset page needs asset *identity*; G-d incidents need something
to *attach to*; governance-catalog sync (Theme 14) needs an entity to *map to*. One migration
serves all four — schedule it before any of them.

**Asset identity = the OpenLineage dataset naming spec (namespace + name), adopted as the
canonical key from day one.** It answers the hardest resolution problems (the same logical table
via DEV vs QA connections, flat-file patterns as assets, UC three-level names) with a
vendor-neutral convention, and makes the Theme-14 OpenLineage emission/pull interop automatic
instead of a mapping layer.

**Phases (each shippable alone):**
1. **Asset entity + resolution** — `assets` table keyed by OpenLineage naming + the mapping of
   checks/runs to asset IDs. The migration everything else rides.
2. **Asset view** (the row above) as a read-only aggregation. Authz subtlety: an asset page
   aggregates *across* suites, so it must filter to the caller's suite grants and never leak
   the existence of unshared checks — the same 404-no-leak discipline verified live at go-live.
3. **Lineage attach** — asset pages grow upstream/downstream + blast radius; the Theme-2 RCA
   dossier and G-d incident objects anchor to assets.
4. **Navigation inversion** — sidebar leads with Assets, dashboard health aggregates per asset
   (more meaningful to data consumers than per-suite), suites demote to an "execution groups"
   surface; suite CRUD and deep links keep working untouched. This is the step where DataQ
   stops feeling like "GX-as-a-service" and starts feeling like a data-observability product —
   the G-a/G-d repositioning the maturity assessment calls for.

No issues filed yet — phase 1 files first when picked up (it's the dependency of the other
three and of Theme 14's governance sync).

---

## Theme 4 — Compliance (GDPR / CCPA-CPRA / HIPAA)

Full design: **[compliance-posture.md](../docs/compliance-posture.md)**. v1 is privacy-by-design (logger PII
redaction, default-redact samples, retention purge, SecretStore, suite-scoped authz, BYOL
controller/processor split). These close the gaps for a credible v2.x "processor-grade controls" claim.

| # | Gap | Regime hook |
|---|---|---|
| [#431](https://github.com/TheurgicDuke771/DataQ/issues/431) | 🔴 **G1** data-access audit trail (who read which result/sample) | HIPAA §164.312(b) — the HIPAA gate |
| [#432](https://github.com/TheurgicDuke771/DataQ/issues/432) | 🟠 **G2** data-subject-rights machinery (erasure / access / portability) | GDPR Art 15/17/20, CCPA |
| [#433](https://github.com/TheurgicDuke771/DataQ/issues/433) | 🟠 **G3** authoritative PII/PHI classification (warehouse tags over name heuristic) | — |
| [#434](https://github.com/TheurgicDuke771/DataQ/issues/434) | 🟠 **G4** region/residency assertion + enforcement | GDPR Ch. V; LLM transfer vector |
| [#435](https://github.com/TheurgicDuke771/DataQ/issues/435) | 🟡 **G5** assert encryption-at-rest in IaC + offer CMK | — |

### Privacy pack (design-captured 2026-07-04)

Builds on **G3/#433** (authoritative warehouse-tag classification) and **absorbs the
classification remainder** left open when #415 closed with #417's column-aware redaction —
the new pieces, no issues filed yet:

- **Profiler-side PII auto-detection** as an additional classification *source*: the profiler
  already visits every column; lightweight format/regex detectors (no LLM) persist a per-column
  classification tag, and redaction reads **tags** instead of name heuristics. Classification
  sources compose: warehouse tags (#433) > governance-catalog pull (Theme 14) > profiler
  heuristics, most-authoritative wins.
- **`pii_drift` monitor kind** — "a column that looks like email addresses just appeared in a
  table not classified as containing PII." Rides the ADR 0012 seam next to Theme 1's
  `schema_drift`; the alert privacy teams actually fear, and one mainstream DQ tools don't fire.
- **Zero-sample "privacy mode"** — a deployment-level switch where failing-row samples are
  never persisted (aggregates + `metric_value` + unexpected-counts only). Mostly a write-path
  gate; turns the existing redaction stack into a tiered posture: full → column-aware
  redacted (#417) → zero-sample. The first question HIPAA-tier / EU deployments ask.

---

## Theme 5 — Alerting depth (beyond the v1 Teams/Slack/email seam)

v1 ships the `ResultPublisher` seam with Teams + Slack + email, severity routing, dedup, snooze.
These enrich and de-risk it:

| # | Title |
|---|---|
| [#416](https://github.com/TheurgicDuke771/DataQ/issues/416) | Enrich Slack/email alerts: deep link to run, per-check expected-vs-observed, actionable sample, run metadata |
| [#415](https://github.com/TheurgicDuke771/DataQ/issues/415) | Actionable failing-row samples: column-aware redaction (PII vs identifier vs safe) — *closed with #417; the classification remainder is design-captured in Theme 4's privacy pack (2026-07-04)* |
| [#386](https://github.com/TheurgicDuke771/DataQ/issues/386) | Tie `dedup._RANK` to a shared severity source so it can't drift from `routing.route_for` |
| [#387](https://github.com/TheurgicDuke771/DataQ/issues/387) | `suppression.py` should early-return `False` on `run.status == 'failed'` |
| [#388](https://github.com/TheurgicDuke771/DataQ/issues/388) | Single-source the `alert_on` literals (model CHECK ↔ validation) to prevent drift |
| [#389](https://github.com/TheurgicDuke771/DataQ/issues/389) | Rename `teams_webhook_secret_name` → channel-neutral before a 2nd ResultPublisher ships |
| _(no issue yet)_ | **Generic HMAC-signed outbound-webhook `ResultPublisher`** — one publisher makes the alerting side vendor-neutral: signed JSON POST (mirroring the Airflow *ingest* signing pattern), payloads pass the same redaction rules. Prerequisite: the #389 channel-neutral rename. |
| _(no issue yet)_ | **Per-destination payload templates + auth header** on the webhook publisher — a thin template layer gives **create-only coverage of PagerDuty (Events API v2), Opsgenie, ServiceNow (inbound REST), and Jira (Automation inbound webhooks) with zero vendor-specific code** in DataQ. Bidirectional/ITSM-grade sync is Theme 14, deliberately separate. |
| [#492](https://github.com/TheurgicDuke771/DataQ/issues/492) | **ADF webhook live delivery (deferred from v1).** Azure Log Alerts V2 drop query rows (only dimensions; `runId` high-cardinality); ADF metric alerts are aggregate (no `runId`); v1 alerting is per-suite with **no workspace/orchestration-failure channel**. Revisit needs either a workspace alert channel + metric-alert→bound-suite attribution (failure-alert), or Log-Analytics diagnostics + a dimension-split scheduled-query rule / Logic-App reshaper (per-run). The receiver + in-app URL generator already shipped; the live all-status poll covers ADF monitoring for v1. See the #492 deferral note for full caveats |

---

## Theme 6 — Consistency & transaction hardening

Surfaced by the ACID/SCD review (single-DB ACID is sound; these close the deliberate gaps):

| # | Title |
|---|---|
| [#308](https://github.com/TheurgicDuke771/DataQ/issues/308) | 🟠 Double-trigger race in `_trigger_suites` (non-atomic dedup, no unique constraint) |
| [#310](https://github.com/TheurgicDuke771/DataQ/issues/310) | History/audit strategy remainder (ADR 0020 landed `connection_versions`; audit-log + soft-delete deferred) |
| [#371](https://github.com/TheurgicDuke771/DataQ/issues/371) | Validation-error handler chokes on Pydantic `field_validator` `ValueError` ctx |
| [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) | `SecretStore` has no `delete`: webhook/connection secrets orphan on clear/delete |
| [#306](https://github.com/TheurgicDuke771/DataQ/issues/306) | Validate provider/env query params on orchestration read endpoints (silent `200 []` on typo) |

---

## Theme 7 — Performance & scale

Rides the harness's parameterizable volume (ADR 0021; HARNESS_TODO §6). Baseline-first, regression budget:

| # | Title |
|---|---|
| [#327](https://github.com/TheurgicDuke771/DataQ/issues/327) | 🟠 Batch the profiler's N+1 per-column top-values warehouse queries |
| [#323](https://github.com/TheurgicDuke771/DataQ/issues/323) | 🟠 Index + batch the result-retention sweep for large `results` tables |
| [#318](https://github.com/TheurgicDuke771/DataQ/issues/318) | Run progress is binary 0%→100% (GX atomic batch) — no true per-check incremental progress |

---

## Theme 8 — Datasource / connection depth

| # | Title |
|---|---|
| [#194](https://github.com/TheurgicDuke771/DataQ/issues/194) | Snowflake key-pair: support encrypted (passphrase-protected) private keys |
| [#195](https://github.com/TheurgicDuke771/DataQ/issues/195) | Snowflake key-pair: migrate GX runner off deprecated `kwargs['connect_args']` private_key path |
| [#351](https://github.com/TheurgicDuke771/DataQ/issues/351) | Test Connection button on the New/Edit connection page (draft-connection test endpoint) |
| [#244](https://github.com/TheurgicDuke771/DataQ/issues/244) | Suite-on-suite triggering (run a suite when another suite completes) |
| [#466](https://github.com/TheurgicDuke771/DataQ/issues/466) | Interactive datasource browsing — container browser (ADLS/S3) + 3-level catalog→schema→table picker (UC); from the W1–6 not-started triage (run/check paths shipped via explicit targets; browsing superseded & deferred) |
| _(no issue yet)_ | JSON flat-file support — `FlatFileCheckRunner`/profiler/run-target accept `json` alongside `csv`/`parquet` (the W2 file-asset config task listed JSON; v1 shipped CSV/Parquet only) |
| _(no issue yet)_ | **Generic PostgreSQL adapter** — `ConnectionAdapter` + thin `CheckRunner` on the shared `gx_runner` (same shape as the Snowflake path; GX supports Postgres natively via SQLAlchemy). One **engine-generic** adapter (never an Azure-branded one — ADR 0010/0013) covers Azure Database for PostgreSQL, **Azure HorizonDB** (fully PG-compatible — standard connection strings/drivers; *Preview* as of 2026-07, so support it as "it's Postgres" and don't advertise a named integration until GA), and AWS RDS / GCP Cloud SQL / self-hosted for free. Dogfoodable against the app's own Postgres, so integration tests need no new harness infra. This is the G-f "generic-RDBMS cheap first win". |
| _(no issue yet)_ | **Generic MSSQL / T-SQL adapter** — one adapter covers **Microsoft Fabric** (Warehouse + Lakehouse SQL analytics endpoint + Fabric SQL database — all standard SQL Server TDS endpoints, port 1433, ODBC 18+ / pyodbc; GX supports mssql via SQLAlchemy), **Azure SQL Database**, Synapse dedicated pools — and, engine-generic like the PG row (ADR 0010/0013), any standard SQL Server endpoint (on-prem, AWS RDS for SQL Server). The one real design cost: Fabric endpoints want **Entra ID auth** (user or service principal; SQL auth unsupported on some Fabric items) → the adapter needs a client-credentials token flow — same KV-held secret model already used for ADF. Bonus: anything mirrored into Fabric (Cosmos DB, HorizonDB, …) becomes checkable through its mirror's SQL analytics endpoint with zero extra adapter code. |
| _(no issue yet)_ | **OneLake flat-file spike** — OneLake speaks the ADLS Gen2 DFS API (`onelake.dfs.fabric.microsoft.com`); verify the existing ADLS flat-file adapter reaches Fabric lake files with just an endpoint override + Entra auth before promising it. Spike first, then a small extension — not a new adapter. |
| _(no issue yet)_ | **S3-compatible `endpoint_url` override** on the existing S3 adapter — one config field unlocks MinIO, Cloudflare R2, and on-prem object stores (ADR 0010/0013 vendor-neutral; same shape as the OneLake spike). |
| _(no issue yet)_ | **Google BigQuery adapter** — the single biggest warehouse omission: Snowflake/Databricks/BigQuery is the modern top-three and DataQ covers two. GX supports it via `sqlalchemy-bigquery`; same shape as the Snowflake path (dialect + connection-spec + service-account-key auth in the SecretStore). |
| _(no issue yet)_ | **Amazon Redshift adapter** — the AWS warehouse; pairs with the existing S3 adapter (AWS shops almost always run both). `sqlalchemy-redshift` is Postgres-dialect-adjacent → a near-sibling of the generic PG adapter. |
| _(no issue yet)_ | **Google Cloud Storage flat-file adapter** — completes the object-store trio next to ADLS Gen2 + S3 (`gcsfs`; the `FlatFileCheckRunner` + batch resolution already do the hard part). With BigQuery + Redshift this yields the ADR 0013 BYOL claim: **warehouse + object store covered on all three clouds**. |
| _(no issue yet)_ | **MySQL / MariaDB adapter** — the other half of the open-source OLTP world; a dialect swap on the engine-generic SQLAlchemy runner once the PG adapter proves the pattern. Enormous installed base, near-zero marginal cost. |
| _(no issue yet)_ | **Trino adapter (+ Amazon Athena)** — the G-f multiplier: one SQLAlchemy dialect inherits the user's entire Trino/Starburst connector ecosystem (Hive, Iceberg, Cassandra, Mongo, Kafka-topics-as-tables — anything their cluster federates) with no per-store adapters on our side; **Athena** rides the same dialect family for serverless AWS. The cheapest answer to "4 datasources vs the 30–50 a category product ships". |

**Recommended order** (decided 2026-07-03, extended 2026-07-04; sits alongside — not competing
with — the Theme-1 opening sequence; different layer of the stack): PG adapter → MSSQL/Fabric
adapter → OneLake spike; then the cloud-triad completion (BigQuery → Redshift → GCS → MySQL),
with Trino/Athena slotted whenever federation demand shows up (it can jump the queue — it's the
G-f multiplier).

**Decided-against (2026-07-03): no native Cosmos DB adapter.** Cosmos has no SQL dialect
SQLAlchemy/GX can drive, and the DataFrame-reader alternative (Cosmos SDK → pandas, the UC-runner
shape) has RU-cost + scale problems that make it a bad default. Coverage path of record: **via its
Fabric mirror** → the MSSQL adapter reads the mirror's SQL analytics endpoint. Revisit a native
adapter only on real demand.

**Demand-driven boundary (recorded 2026-07-04) — major but deliberately not default:**
Oracle / Teradata / SAP HANA (real enterprise demand, heavy drivers + licensing — build on a
concrete prospect, not speculatively); **MongoDB** (same no-SQL-dialect logic as Cosmos, same
routing answer: via Trino's connector or a mirror); **Kafka / streaming** (not batch — that's the
DQX v1.1 lane, ADR [0003](../docs/adr/0003-gx-only-for-v1.md)); **Delta / Iceberg table formats
on raw object storage** — Iceberg is already tracked as [#286](https://github.com/TheurgicDuke771/DataQ/issues/286)
(Theme 2); a `delta` flat-file format would slot next to the JSON row above.

---

## Theme 9 — Results & reporting depth

| # | Title |
|---|---|
| [#345](https://github.com/TheurgicDuke771/DataQ/issues/345) | Results export — PDF report of an executed suite run |
| [#283](https://github.com/TheurgicDuke771/DataQ/issues/283) | Check version history — restore/revert to a previous version |
| [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) | Results: dedupe the runs fetch across tabs + share the date-window presets with Dashboard |
| [#424](https://github.com/TheurgicDuke771/DataQ/issues/424) | Run-detail sample header says 'values redacted' even when non-PII values surface (#417 follow-up) |
| _(no issue yet)_ | **Per-check metric trend view** (added 2026-07-04) — a "this check over time" chart of `metric_value` with the warn/fail/critical threshold bands drawn on it. The payoff view the ADR 0016/0012 scalar-metric design was built for; one recharts component + an existing-data query. |
| _(no issue yet)_ | **Run comparison (diff two runs)** — per-check status deltas, metric deltas, new/removed checks between any two runs of a suite ("what changed between yesterday's pass and today's fail"). Pairs with Theme 2's RCA evidence card; valuable standalone. |
| _(no issue yet)_ | **In-app notification center** — alerts are outbound-only (Teams/Slack/email) today; a bell/feed surface with read-ack state + deep links to runs gives alerts an in-app home, and becomes the UI surface for incident state when Theme 14's ITSM tier-2 lands. |

---

## Theme 10 — Test-hardening & frontend-refactor backlog

Quality debt deliberately parked until Week 8 / post-v1 (CONTRIBUTING rule 4a — periodic, not per-PR):

| # | Title |
|---|---|
| [#278](https://github.com/TheurgicDuke771/DataQ/issues/278) | Triage the 63 mutmut survivors in `custom_sql.py` |
| [#322](https://github.com/TheurgicDuke771/DataQ/issues/322) | Flaky frontend test: `LiveRunProgress` poll-until-terminal |
| [#197](https://github.com/TheurgicDuke771/DataQ/issues/197) | Extract a shared antd `Select` helper (`selectOption`) |
| [#199](https://github.com/TheurgicDuke771/DataQ/issues/199) | Extract a `useAsyncAction` toast helper |
| [#204](https://github.com/TheurgicDuke771/DataQ/issues/204) | Consolidate drawer/delete duplication (`confirmDelete`, `errorMessage`, submit guards) |
| [#229](https://github.com/TheurgicDuke771/DataQ/issues/229) | Extract a shared `AsyncBody`/`AsyncTable` loading/error/empty helper |
| [#236](https://github.com/TheurgicDuke771/DataQ/issues/236) | Extract a shared `connectionOptionLabel(c)` helper |
| [#326](https://github.com/TheurgicDuke771/DataQ/issues/326) | `RunNowPanel`: redundant `{open && …}` guard alongside `Modal destroyOnClose` |
| [#237](https://github.com/TheurgicDuke771/DataQ/issues/237) | `ImportSuiteDrawer`: unreachable empty-connections hint (dead UI) |

---

## Theme 11 — Design-only deferrals (no issue filed yet)

Captured in ADRs / progress.md, not yet broken into backlog issues:

| Item | Where | Note |
|---|---|---|
| **DQX engine** for UC streaming/DLT | ADR [0003](../docs/adr/0003-gx-only-for-v1.md) | v1.1 — same `UnityCatalogCheckRunner` interface; UI `engine: gx \| dqx` toggle |
| **Reconciliation two-connection model** | ADR [0014](../docs/adr/0014-reconciliation-comparison-check-kind.md) → ADR 0015 (pending) | unblocks the `comparison` monitor kind |
| **HashiCorp Vault** `SecretStore` spike | HARNESS_TODO §5 | validates the ADR 0010/0013 seam (Key Vault = one impl) |
| **Performance/scale harness** | ADR 0021 / HARNESS_TODO §6 | the script behind #327/#323 above |
| **Dark mode / marketing page** | — | prototype deferrals → **Theme 12** below |

---

## Theme 12 — Prototype deferrals (DataQ Design System, out of v1 scope)

From the **DataQ Design System** prototype but deliberately out of v1. _(Open the prototype via the
`DesignSync` tool — project `317fec67-2b8a-498c-8f6b-523750916a8d`.)_

| Item | Note | Design ref |
|---|---|---|
| **Marketing landing page** | **Build when DataQ goes multi-tenant** — single-tenant v1 is an Azure-AD-gated console with no public surface, so a marketing site has no home yet | `templates/marketing/{MarketingNav,Hero,Features,MarketingClose}.jsx` |
| **Dark mode** | `[data-theme="dark"]` semantic remap + opt-in toggle; **architecturally ready** (v1 components already use semantic tokens — flip is "zero component changes" iff the visual-fidelity pass keeps that discipline) | `tokens/dark.css` + `guidelines/dark-theme.card.html` |

**Not adopted (intentional, per design review):** the prototype's **"View as" role switch** (real authz
is server-side — Azure AD + per-suite sharing, CLAUDE.md §11), its **framework-free component code +
`_ds_bundle.js` / `window.*` globals** (production uses real antd), and the **teal "Enterprise Monitor"
palette** (the shipped indigo theme stands).

---

## Theme 13 — MCP tool expansion (candidate endpoints)

The v1 MCP server exposes 8 curated tools (ADR [0008](../docs/adr/0008-mcp-server.md));
the REST surface has ~52 more endpoints. Candidates below, tiered by risk — every new
tool stays a thin service-layer wrapper with `require_permission` authz + sample
redaction, exactly like the existing 8 (`backend/app/mcp/server.py`). Cross-cutting
dependencies: **#488** (workspace-admin visibility in MCP tools) and **#461 / ADR 0026**
(DataQ-issued API keys, which unblock headless MCP clients).

**Filed (2026-07-04, carried from the retired `WEEK8_TODO` working tracker):**

| # | Title |
|---|---|
| [#583](https://github.com/TheurgicDuke771/DataQ/issues/583) | `profile_column` 422s on SQL suites without explicit `table`/`schema` — default to the suite's run target (found in the #550 MCP client E2E) |
| [#584](https://github.com/TheurgicDuke771/DataQ/issues/584) | NL tool-selection spot-check — watch a real LLM client route the 4 canonical queries (descriptions are LLM-facing, CLAUDE.md §10; the selection in #550 was author-made) |

**Tier 1 — high-value safe reads:**

| Candidate tool | Wraps | NL query it serves |
|---|---|---|
| `list_checks` / `get_check` | `GET /suites/{id}/checks[/{id}]` | "what checks does the orders suite have?" |
| `get_check_history` | `GET .../checks/{id}/history` | "how has null-rate trended this week?" |
| `list_runs` | `GET /runs` (filterable) | "show me yesterday's failed runs" |
| `get_run_results` | `GET /runs/{id}` (redacted) | "why did run X fail?" |
| `list_connections` | `GET /connections` — names/types/health **only, never config or secrets** | "which connections are unhealthy?" |
| `list_schedules` | `GET /schedules` | "when does the orders suite run?" |
| `list_trigger_bindings` | `GET /trigger-bindings` | "what triggers the gold suite?" |
| `get_notification_config` | `GET /suites/{id}/notifications` | "who gets alerted for this suite?" |
| `get_suite_performance` | `GET /dashboard/summary` (per-suite slice) | "which suite is worst this month?" |
| `export_suite` | `GET /suites/{id}/export` | "export the orders suite" |

**Tier 2 — mutating, edit-permission-gated:**

| Candidate tool | Wraps | Note |
|---|---|---|
| `dryrun_check` | `POST /suites/{id}/checks/dryrun` | the LLM author-preview loop — pairs with `create_check` |
| `update_check` / `delete_check` | `PATCH`/`DELETE` check | delete needs confirm-style ergonomics |
| `snooze_check` / `unsnooze_check` | check snooze endpoints | "snooze this alert for 24h" |
| `cancel_run` | `POST /runs/{id}/cancel` | |
| `create_schedule` / `delete_schedule` | schedules CRUD | "run this suite daily at 9am IST" |
| `create_trigger_binding` | `POST /trigger-bindings` | "run this suite when the orders DAG succeeds" |
| `import_suite` | `POST /suites/import` | |
| `suggest_column_policy` | `POST /suites/{id}/column-policy/suggest` | redaction-policy assistant |
| `test_connection` | `POST /connections/{id}/test` | action but non-destructive |

**Excluded (deliberate):** connection create/update/reauth (**credentials transiting an
LLM — hard no**); the orchestration webhooks (M2M surface); admin endpoints (workspace-
admin scoping pending #488); share mutations (permission escalation through a
conversational interface); `_probe` (demo-only); `/me` + `/users/search` (low value).

---

## Theme 14 — Ecosystem & vendor-neutral portability (added 2026-07-04)

Meet users in the tools they already run — incident/ITSM, test management, data governance,
GitOps — and keep every integration behind a seam with **open standards first** (ADR 0010/0013:
no tool becomes architecture; each vendor is one impl). All design-captured, no issues filed yet.

### Checks-as-code: `dataq.yaml` + a CI gate

Formalize the existing export/import format into a **versioned declarative suite format** plus a
CLI / GitHub Action: `dataq validate` in a data-pipeline repo's CI, `dataq apply` to sync,
**drift detection** between repo and workspace. The market leaders (Soda, GX Cloud) lead with
this workflow; DataQ's angle is that the format **round-trips with a full UI and the
suite-scoped authz model** — checks-as-code tools mostly have no real UI, UI tools no real
GitOps. Hard dependency: **ADR 0026 / #461** (PATs) — this is its killer use case, and the
argument for scheduling PATs early in the cycle.

### Incident / ITSM — tier 2, bidirectional

Tier 1 (create-only PagerDuty / Opsgenie / ServiceNow / Jira via the generic signed-webhook
publisher + payload templates) lives in **Theme 5** and needs zero vendor code. Tier 2 is the
ITSM-grade half: DataQ incident objects (the G-d design) sync *both ways* — ack/resolve in
PagerDuty/ServiceNow reflects in DataQ, Jira issue links attach to results — and the **Theme 2
agentic-RCA evidence card is the incident payload**, so the ticket arrives with the diagnosis
pre-attached. Per-vendor impls behind one `IncidentProvider`-shaped seam, only after tier 1
proves demand.

### Test-management publishing (TestRail, Xray, Zephyr)

DQ artifacts map cleanly: suites → test plans, checks → cases, runs → test runs with results.
A result-*exporter* seam (a `ResultPublisher` sibling — per-run push, not per-alert) publishes
run outcomes into the test-management tool of record; pairs with Theme 9's PDF export (#345)
as the "reporting into someone else's system of record" family.

### Data-governance catalogs (+ OpenLineage)

Two directions, one seam:

- **Pull** — consume the catalog's glossary/PII classifications as an authoritative
  classification source (feeds Theme 4's privacy pack + #433, ranked above profiler
  heuristics), and **pull lineage** to power the RCA blast-radius (Theme 2) and gap G-d
  instead of building a lineage graph ourselves.
- **Push** — publish DQ results as **quality facets/assertions on catalog entities**, so data
  consumers see check status where they shop for data. DataHub and OpenMetadata both have
  first-class assertion/data-quality APIs — natural open-source reference impls; Microsoft
  Purview covers the Azure story; Collibra / Atlan / Alation are commercial targets behind the
  same seam.
- **Open standard first: emit OpenLineage events** from runs — one vendor-neutral event stream
  consumed by Marquez, DataHub, Purview, Atlan and the orchestrators' own lineage backends;
  cheaper and more neutral than any point integration, and the likely first slice.

**Shared primitive:** this whole section keys on the **asset entity** (Theme 3's Asset-first IA
capture, phase 1) with **OpenLineage dataset naming as the canonical identity** — build that
first; catalog mapping, lineage nodes, and quality-facet publishing all resolve through it.

### Observability & deploy portability

Completes the last half-open ADR 0010 seam (tracked issues previously unmapped in this doc):

| # | Title |
|---|---|
| [#524](https://github.com/TheurgicDuke771/DataQ/issues/524) | Migrate the log pipeline opencensus → OTel (opencensus is EOL; spans already OTel-native via #525) |
| _(no issue yet)_ | **Generic OTLP exporter endpoint** (`OTEL_EXPORTER_OTLP_ENDPOINT`) — App Insights becomes one backend among any OTLP consumer (Grafana/Tempo, Datadog, Jaeger); the observability twin of the `DATAQ_AUTH_*` generic-OIDC cutover (ADR 0028), and a BYOL/marketplace prerequisite in spirit (ADR 0013) |
| [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) | AWS + GCP deploy IaC behind the provider-agnostic seams (ADR 0028 follow-up) |

---

## How this maps to GitHub

- **Status** lives on GitHub — un-scheduled issues sit on the `v1.1 Backlog` milestone; scheduled ones move to a `v1.1 Week N` milestone (see `docs/progress.md` §Cycle plan). This doc mirrors the backlog by theme.
- When you pick up a theme, **file the design-only items** (Themes 2, 3, 4, 5, 8, 9, 11, 14 carry them) as issues on that milestone first.
- New post-v1 work: open the issue, milestone it `v1.1 Backlog` (or the target week if already scheduled), and add a row to the
  matching theme here. Keep the detailed *design* in the three linked docs, not in this index.
