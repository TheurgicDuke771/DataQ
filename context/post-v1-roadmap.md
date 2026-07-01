# Post-v1 roadmap — the single home for deferred work

> **What this is:** the canonical index of everything deliberately deferred past DataQ v1 —
> design themes (with their detailed design docs) **and** the concrete issue backlog. Every
> issue on the GitHub **`Backlog (post-v1 / testing)`** milestone is, by definition, post-v1
> and is mirrored here under the theme it belongs to.
>
> **What this is not:** a commitment or a schedule. v1 is the 8-week product (see
> [DataQ_platform_roadmap.md](DataQ_platform_roadmap.md) +
> [progress.md](../docs/progress.md)). These are the things we consciously chose *not* to build for v1,
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

**DataQ-issued API keys / service tokens (#461, ADR [0026](../docs/adr/0026-auth-api-keys-and-principal-seam.md) proposed).** Auth today is Azure-AD-only (delegated/SSO) for both REST and `/mcp` — the deepest remaining vendor lock-in (the `get_current_user` seam has one real impl; `users.aad_object_id` is Azure-shaped) and it blocks BYOL-on-AWS/GCP (ADR 0013) and headless/programmatic access (a long-lived scoped key beats a ~60-min refreshing token for CI / always-on MCP clients). The fix is a **second authenticator behind the same `get_current_user` seam** so the **REST API and MCP accept it identically** — never MCP-only — which also finally *exercises* the seam (ADR 0010). Phase it: **user-scoped PATs first** (inherit the owner's per-suite grants → zero new authz; optional read-only down-scope), defer standalone **service-account principals** (they force generalizing `aad_object_id` → a generic principal with pluggable identity bindings + non-user suite sharing). Credential bar: hashed-at-rest + show-once + prefix + expiry + revocation + audit, in a new `api_keys` table (not the retrievable-secret SecretStore), with key lifecycle tied to the owner so it can't outlive a deactivated account.

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

---

## Theme 5 — Alerting depth (beyond the v1 Teams/Slack/email seam)

v1 ships the `ResultPublisher` seam with Teams + Slack + email, severity routing, dedup, snooze.
These enrich and de-risk it:

| # | Title |
|---|---|
| [#416](https://github.com/TheurgicDuke771/DataQ/issues/416) | Enrich Slack/email alerts: deep link to run, per-check expected-vs-observed, actionable sample, run metadata |
| [#415](https://github.com/TheurgicDuke771/DataQ/issues/415) | Actionable failing-row samples: column-aware redaction (PII vs identifier vs safe) — *partly done in #417; classification remainder here* |
| [#386](https://github.com/TheurgicDuke771/DataQ/issues/386) | Tie `dedup._RANK` to a shared severity source so it can't drift from `routing.route_for` |
| [#387](https://github.com/TheurgicDuke771/DataQ/issues/387) | `suppression.py` should early-return `False` on `run.status == 'failed'` |
| [#388](https://github.com/TheurgicDuke771/DataQ/issues/388) | Single-source the `alert_on` literals (model CHECK ↔ validation) to prevent drift |
| [#389](https://github.com/TheurgicDuke771/DataQ/issues/389) | Rename `teams_webhook_secret_name` → channel-neutral before a 2nd ResultPublisher ships |
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

---

## Theme 9 — Results & reporting depth

| # | Title |
|---|---|
| [#345](https://github.com/TheurgicDuke771/DataQ/issues/345) | Results export — PDF report of an executed suite run |
| [#283](https://github.com/TheurgicDuke771/DataQ/issues/283) | Check version history — restore/revert to a previous version |
| [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) | Results: dedupe the runs fetch across tabs + share the date-window presets with Dashboard |
| [#424](https://github.com/TheurgicDuke771/DataQ/issues/424) | Run-detail sample header says 'values redacted' even when non-PII values surface (#417 follow-up) |

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

## How this maps to GitHub

- **Status** lives on the GitHub `Backlog (post-v1 / testing)` milestone — this doc mirrors it by theme.
- When you pick up a theme, **file the design-only items** (Themes 2, 11) as issues on that milestone first.
- New post-v1 work: open the issue, milestone it `Backlog (post-v1 / testing)`, and add a row to the
  matching theme here. Keep the detailed *design* in the three linked docs, not in this index.
