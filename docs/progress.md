# DataQ — Progress tracker (post-v1)

> The **live task tracker**, active since `v1.0.0` (tagged 2026-07-04). The completed v1
> ledger — the per-PR record of the 8-week roadmap, Weeks 1–8 — is **archived, frozen at the
> tag, in [progress-v1.md](progress-v1.md)** (companion: [retro-v1.md](retro-v1.md)).
> **Updated at the end of every PR** — the PR template has a checkbox to enforce.
> Source of truth for "what's done vs. what's left" in the current cycle. CLAUDE.md §13
> carries only the headline.

## Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Done — PR merged to `main` |
| 🟡 | In progress — open PR or partially shipped |
| ⬜ | Not started |
| 🔵 | Deferred / scope-changed (with note) |

---

## Snapshot

| | |
|---|---|
| **v1 baseline** | `v1.0.0` tagged 2026-07-04 — 187/189 roadmap tasks (~99%); all 8 weekly exit gates met; deployed to Azure Container Apps; retro at [retro-v1.md](retro-v1.md); full ledger at [progress-v1.md](progress-v1.md) |
| **Current cycle** | **v1.1 — 6 weeks, 2026-07-04 → 2026-08-15** (planned 2026-07-04 from [context/post-v1-roadmap.md](../context/post-v1-roadmap.md)). Sequencing is **subscription-driven**: Weeks 1–3 extract everything that needs the expiring Snowflake (lapses within days) and Azure (~2026-07-25) subscriptions, then wind down gracefully; Weeks 4–6 run the roadmap's recommended opening sequence (Theme-1 `schema_drift` + `anomaly` → scale-aware execution G-b → incident/lineage design G-d) on cloud-independent infra. See [Cycle plan](#cycle-plan--v11-6-weeks-2026-07-04--2026-08-15) below. |
| **Open issues** | **66** (at the 2026-07-04 backlog remap): **34 scheduled** onto the `v1.1 Week 1..6` milestones (26 from planning — #587–#596 filed then; #492 turned out already closed 2026-07-02 and moved back to its W7 milestone — plus 8 mapped from backlog at the remap) + the cycle epic [#597](https://github.com/TheurgicDuke771/DataQ/issues/597) + **31** on **`v1.1 Backlog`** (renamed from `Backlog (post-v1 / testing)` 2026-07-04) — mapped by theme in [post-v1-roadmap.md](../context/post-v1-roadmap.md), not duplicated here |
| **Open PRs** | none |
| **Coverage gates (CI-enforced, ≥80%)** | backend `--cov-fail-under=80` (98.4% / 1,289 tests at the tag) · frontend all-src `lines: 80` (~88% / 337 tests at the tag) — every post-v1 PR rides the same gates |

---

## Carried over from v1

Everything that was still open, pending, or deferred in the v1 ledger at the tag. GitHub is
the source of truth for issue state; this register mirrors it so nothing carried is lost.

### Deferred v1 roadmap tasks (the 2 🔵 of 189)

| Item | Where tracked |
|---|---|
| Interactive datasource browsing — ADLS/S3 container browser + UC 3-level catalog→schema→table picker (the two 🔵 scope-changed W2 rows; run/check paths shipped via explicit targets) | [#466](https://github.com/TheurgicDuke771/DataQ/issues/466) — post-v1-roadmap Theme 8 |

### Go-live follow-ups (filed 2026-07-03/04, open by choice — none blocking; all six since scheduled into the v1.1 cycle plan below)

| # | Title |
|---|---|
| [#563](https://github.com/TheurgicDuke771/DataQ/issues/563) | Mutation-spike survivors triage (mutmut/Stryker spike fallout) |
| [#568](https://github.com/TheurgicDuke771/DataQ/issues/568) | Severity threshold ordering unvalidated (warn/fail/critical bands can be authored out of order) |
| [#571](https://github.com/TheurgicDuke771/DataQ/issues/571) | `checks_total` shows cosmetic 0 on pre-dispatch run failures |
| [#573](https://github.com/TheurgicDuke771/DataQ/issues/573) | Flaky `SchedulesPanel` Popconfirm test in CI |
| [#583](https://github.com/TheurgicDuke771/DataQ/issues/583) | MCP `profile_column` 422s on SQL suites without explicit table/schema — default to the run target (WEEK8_TODO carry-over, filed at its retirement) |
| [#584](https://github.com/TheurgicDuke771/DataQ/issues/584) | MCP NL tool-selection spot-check — the softest W7 tick (WEEK8_TODO carry-over, filed at its retirement) |

### Long-standing follow-ups (pre-go-live filings, all on Backlog)

| # | Title |
|---|---|
| [#194](https://github.com/TheurgicDuke771/DataQ/issues/194) | Snowflake key-pair: encrypted (passphrase-protected) private keys |
| [#195](https://github.com/TheurgicDuke771/DataQ/issues/195) | Snowflake key-pair: migrate off deprecated GX `connect_args` private_key path |
| [#197](https://github.com/TheurgicDuke771/DataQ/issues/197) / [#199](https://github.com/TheurgicDuke771/DataQ/issues/199) / [#204](https://github.com/TheurgicDuke771/DataQ/issues/204) | Week-4 frontend refactor nits (shared antd-Select test helper · `useAsyncAction` toast helper · drawer/delete dedup) |
| [#327](https://github.com/TheurgicDuke771/DataQ/issues/327) | Column profiler N+1 query batching |
| [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) / [#351](https://github.com/TheurgicDuke771/DataQ/issues/351) | Week-6 results/connection-page follow-ups |
| [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) | `SecretStore` has no delete — webhook/connection secrets orphan on clear/delete |
| [#524](https://github.com/TheurgicDuke771/DataQ/issues/524) | opencensus → OTel log-export migration (spans done in #525; logs remain) |
| [#529](https://github.com/TheurgicDuke771/DataQ/issues/529) | MCP tool expansion (candidate tiers from #530 — Theme 13) |
| [#532](https://github.com/TheurgicDuke771/DataQ/issues/532) | Dry-run preview is Snowflake-only — extend to Unity Catalog + flat-file suites |
| [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) | AWS/GCP deploy IaC (post-v1 per ADR 0028) |
| [#461](https://github.com/TheurgicDuke771/DataQ/issues/461) | DataQ-issued API keys / service tokens (ADR 0026 — deferred with shape confirmed: PATs first) |

_The rest of the 55 are mapped by theme in [post-v1-roadmap.md](../context/post-v1-roadmap.md) — that doc, plus the GitHub milestone, is the full register; this table only names the ones the v1 ledger and CLAUDE.md §13 called out individually._

### Pending design decisions

| Decision | Affects | Status |
|---|---|---|
| Two-connection model for `comparison` checks (**ADR 0015**, reserved in ADR 0014) | The reconciliation/`comparison` monitor kind, when its theme is picked up | ⬜ open, non-blocking until then |

### Standing decisions of record & guardrails (carried from the go-live close)

| Decision | Record |
|---|---|
| ADR 0026 (API keys) **deferred** to post-v1 Theme 3 — PATs-first, service principals later, HTTP Basic rejected; interim = Azure CLI pre-authorized on the API scope (#565) | [ADR 0026](adr/0026-auth-api-keys-and-principal-seam.md) |
| Databricks **Free Edition** = demo/eval only; paid workspace before any commercial use | gap **G-h**, [post-v1-roadmap.md](../context/post-v1-roadmap.md) |
| **Pre-marketplace harness teardown** — strip Flows A/B/C, the 5 harness connections, demo users, and the seeded-breach check before any marketplace/customer-facing artifact | gap **G-i**, [post-v1-roadmap.md](../context/post-v1-roadmap.md) + `deploy/README.md` |
| **Ops/renewal timers consciously skipped** — the Sept-2026 demo-credential cluster self-signals via #419 alerting; recovery = re-mint + KV update | [retro-v1.md](retro-v1.md) |
| Key Vault **purge protection left off** (demo-scoped vault) | `deploy/README.md` |
| **Recurring cadences stay manual** (weekly security scan — CONTRIBUTING r36, quarterly MCP supply-chain audit — r39 (next ~2026-10-01), Dependabot triage) — no timer infrastructure, run session-driven; extends the ops-timers-skipped decision. Revisit trigger: second contributor or production-critical use. Recorded 2026-07-04 at the `WEEK8_TODO` retirement (its C2 item) | this table (extends [retro-v1.md](retro-v1.md)'s ops-timers decision) |

---

## Cycle plan — v1.1 (6 weeks, 2026-07-04 → 2026-08-15)

> Planned 2026-07-04 from [context/post-v1-roadmap.md](../context/post-v1-roadmap.md) (the
> generator input). GitHub mirror: milestones **`v1.1 Week 1..6`** (due Saturdays), the cycle
> epic [#597](https://github.com/TheurgicDuke771/DataQ/issues/597), and the **DataQ Roadmap**
> project (all 34 scheduled issues carry the `v1.1 week` single-select + Status). Everything
> not scheduled below stays themed on the **`v1.1 Backlog`** milestone (renamed from
> `Backlog (post-v1 / testing)` at the 2026-07-04 remap, when 8 week-fit issues also moved
> into W4–W6 below).
>
> **Sequencing is subscription-driven, not theme-driven, for the first half:** the
> **Snowflake subscription lapses within days** of planning and the **Azure subscription ends
> ~2026-07-25** (end of W3). So W1–3 extract everything that needs live cloud — last-window
> Snowflake work, verify-against-App-Insights portability work, PATs while the AAD path is
> still the reference — and end in a *deliberate* wind-down (G-i teardown + `terraform
> destroy`) instead of a lapse. W4–6 then run the roadmap's recommended opening sequence
> (Theme-1 `schema_drift` + `anomaly` → scale-aware execution G-b → incident/lineage design
> G-d) on infra that survives: the local stack, S3 (AWS), and Databricks Free Edition.

### v1.1 W1 — Snowflake close-out + PATs (due 2026-07-11) — 0/6

⚡ **The three Snowflake-live rows are day-1 work, in this order** — the subscription lapses
within days; after it does they can no longer be live-verified. **PATs (#461) start right
behind them** (pulled forward from W2 at planning, 2026-07-04): the biggest unlocker in the
backlog, and it must land while Azure AD is still the reference validator.

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#194](https://github.com/TheurgicDuke771/DataQ/issues/194) Snowflake key-pair: encrypted (passphrase-protected) private keys — **live-verify** | Theme 8 |
| ⬜ | [#195](https://github.com/TheurgicDuke771/DataQ/issues/195) Snowflake key-pair: migrate off deprecated GX `connect_args` path — **live-verify** | Theme 8 |
| ⬜ | [#587](https://github.com/TheurgicDuke771/DataQ/issues/587) Snowflake scale/volume baseline (harness §6 volume) — the pushdown-path reference datum for W6's G-b work | Theme 7 / G-b |
| ⬜ | [#588](https://github.com/TheurgicDuke771/DataQ/issues/588) Retire the harness Snowflake leg cleanly at lapse (schedules/bindings off, secret deleted, Flow A retired — partial G-i) | ops / G-i |
| ⬜ | [#461](https://github.com/TheurgicDuke771/DataQ/issues/461) **PATs phase 1** (ADR 0026): second authenticator behind `get_current_user`, REST + MCP identically; breaks the Azure-AD-only auth dependency early in the Azure window. Exit: **mint 1 workspace-admin + 1 member PAT** (two-tier authz matrix for all later headless/live checks; short expiry on the admin one) | Theme 3 / G-e |
| ⬜ | [#583](https://github.com/TheurgicDuke771/DataQ/issues/583) MCP `profile_column`: default to the suite run target on SQL suites | Theme 13 |

### v1.1 W2 — Portability: OTel logs, secrets lifecycle, dry-run depth (due 2026-07-18) — 0/5

Land the vendor-neutral seams **while App Insights / Key Vault / live `/mcp` still exist to
verify parity against** (ADR 0010/0013/0028 discipline). Live checks from here on run on the
W1 admin/member PATs instead of the Azure-CLI token workaround (#565).

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#524](https://github.com/TheurgicDuke771/DataQ/issues/524) opencensus → OTel log-export migration (opencensus is EOL; spans already OTel via #525) | Theme 14 |
| ⬜ | [#589](https://github.com/TheurgicDuke771/DataQ/issues/589) Generic OTLP exporter endpoint — App Insights becomes one backend among any OTLP consumer | Theme 14 |
| ⬜ | [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) `SecretStore.delete` — webhook/connection secrets orphan today; live-verify on Key Vault | Theme 6 |
| ⬜ | [#532](https://github.com/TheurgicDuke771/DataQ/issues/532) Dry-run preview: extend Snowflake-only → Unity Catalog + flat-file (moved from W1 — cloud-independent, no deadline) | Theme 8 |
| ⬜ | [#584](https://github.com/TheurgicDuke771/DataQ/issues/584) MCP NL tool-selection spot-check vs live `/mcp` (4 canonical queries), authenticated via the W1 PATs | Theme 13 |

### v1.1 W3 — Azure wind-down + local-first posture (due 2026-07-25) — 0/3

Azure ends ~this week's due date. Order matters: final live validation first, teardown last.
_(Planning correction 2026-07-04: #492 — ADF webhook live delivery — was scheduled here as a
"final decision" item but had in fact **closed 2026-07-02** during the W7 live smoke, delivered
via the Action-Group metric-alert path; re-homed to its Week-7 milestone.)_

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | Final live-prod E2E of the W1–2 landings (OTel parity, PAT auth, secrets lifecycle) before anything is destroyed | — |
| ⬜ | [#590](https://github.com/TheurgicDuke771/DataQ/issues/590) Azure wind-down: G-i harness teardown, `terraform destroy`, credential retirement, state disposition (harness compute already stopped 2026-07-04 — wake via `harness_window.sh`, see the #590 runbook) | ops / G-i |
| ⬜ | [#591](https://github.com/TheurgicDuke771/DataQ/issues/591) Local-first runtime posture: docker-compose parity for secrets/auth/observability; surviving datasources = local files + S3 + Databricks Free | ops / Theme 14 |

### v1.1 W4 — `schema_drift` monitor kind (due 2026-08-01) — 0/5

Cloud-independent from here on. Engine follow-ups land first — they touch the code #592 builds on.

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#427](https://github.com/TheurgicDuke771/DataQ/issues/427) Reuse one warehouse connection per monitor run | Theme 1 |
| ⬜ | [#428](https://github.com/TheurgicDuke771/DataQ/issues/428) Consolidate SQL-identifier validation + dedup `run_monitors` boilerplate | Theme 1 |
| ⬜ | [#429](https://github.com/TheurgicDuke771/DataQ/issues/429) Fix `MonitorRunner` `isinstance`-on-Protocol gate | Theme 1 |
| ⬜ | [#592](https://github.com/TheurgicDuke771/DataQ/issues/592) `schema_drift` end-to-end (baseline snapshot + diff engine + authoring UI, all datasource paths) — baseline persistence designed for two consumers (W5 anomaly) | Theme 1 / G-a |
| ⬜ | [#520](https://github.com/TheurgicDuke771/DataQ/issues/520) Freshness/volume monitors: add flat-file (S3/local) support — SQL-only today (mapped from backlog 2026-07-04; same `run_monitors` engine code, and #592's flat-file path needs it) | Theme 1 |

### v1.1 W5 — `anomaly` monitor kind + metric trends (due 2026-08-08) — 0/5

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#593](https://github.com/TheurgicDuke771/DataQ/issues/593) `anomaly` kind — rolling z-score + seasonality baseline over `metric_value` history; `skip` on cold start | Theme 1 / G-a |
| ⬜ | [#594](https://github.com/TheurgicDuke771/DataQ/issues/594) Per-check `metric_value` trend view with threshold bands (+ anomaly-baseline overlay — doubles as #593's visual debugger) | Theme 9 |
| ⬜ | [#568](https://github.com/TheurgicDuke771/DataQ/issues/568) Validate severity-threshold ordering at authoring time | Theme 1 |
| ⬜ | [#424](https://github.com/TheurgicDuke771/DataQ/issues/424) Run-detail sample header says 'values redacted' even when non-PII values surface (mapped from backlog 2026-07-04 — results-surface week) | Theme 9 |
| ⬜ | [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) Results: dedupe the runs fetch across tabs + share date-window presets with Dashboard (mapped from backlog 2026-07-04 — same surfaces as #594) | Theme 9 |

### v1.1 W6 — scale-aware execution + hardening + cycle close (due 2026-08-15) — 0/12

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#595](https://github.com/TheurgicDuke771/DataQ/issues/595) Sampling / partition-aware execution for flat-file + UC runners + OOM guardrail (vs the #587 pushdown baseline) | Theme 7 / G-b |
| ⬜ | [#327](https://github.com/TheurgicDuke771/DataQ/issues/327) Batch the profiler's N+1 per-column queries | Theme 7 |
| ⬜ | [#323](https://github.com/TheurgicDuke771/DataQ/issues/323) Index + batch the result-retention sweep | Theme 7 |
| ⬜ | [#596](https://github.com/TheurgicDuke771/DataQ/issues/596) Incident & lineage **design doc** (incident objects, asset-entity anchoring, OpenLineage-first pull) → files next cycle's phase-1 issues | G-d |
| ⬜ | [#563](https://github.com/TheurgicDuke771/DataQ/issues/563) Mutation-spike survivors triage | Theme 10 |
| ⬜ | [#573](https://github.com/TheurgicDuke771/DataQ/issues/573) Flaky `SchedulesPanel` Popconfirm test | Theme 10 |
| ⬜ | [#278](https://github.com/TheurgicDuke771/DataQ/issues/278) Triage the 63 mutmut survivors in `custom_sql.py` (mapped from backlog 2026-07-04 — test-hardening batch) | Theme 10 |
| ⬜ | [#322](https://github.com/TheurgicDuke771/DataQ/issues/322) Flaky `LiveRunProgress` poll-until-terminal test (mapped from backlog 2026-07-04 — test-hardening batch) | Theme 10 |
| ⬜ | [#571](https://github.com/TheurgicDuke771/DataQ/issues/571) `checks_total` shows cosmetic 0 on pre-dispatch run failures (mapped from backlog 2026-07-04 — small-bug batch) | Theme 6 |
| ⬜ | [#541](https://github.com/TheurgicDuke771/DataQ/issues/541) Audit remaining FKs without `ondelete` — delete paths may 500 like #540 (mapped from backlog 2026-07-04 — small-bug batch) | Theme 6 |
| ⬜ | [#306](https://github.com/TheurgicDuke771/DataQ/issues/306) Validate provider/env query params on orchestration reads — silent `200 []` on typo (mapped from backlog 2026-07-04 — small-bug batch) | Theme 6 |
| ⬜ | Cycle retro + `v1.1.0` tag + next-cycle planning input refresh | — |

### Deliberately NOT scheduled this cycle (the 31 on `v1.1 Backlog`)

Stays on **`v1.1 Backlog`** by choice: Theme 2 LLM authoring/RCA (needs the `LLMProvider` seam
design, follows G-d), Theme 3 asset entity + asset-first IA (its phase 1 is what the #596 design
doc files for the *next* cycle), the Theme 8 adapter expansion (PG adapter is the natural
next-cycle opener — dogfoodable locally), Theme 4 compliance (#431–#435), the Theme 5 alerting
nits/enrichment (#386–#389/#416), the Theme 10 frontend-refactor batch
(#197/#199/#204/#229/#236/#237/#326), #505 AWS/GCP IaC (no target cloud subscription yet —
becomes the re-deploy path after #590), and everything else themed in
[post-v1-roadmap.md](../context/post-v1-roadmap.md). _(2026-07-04 remap: 8 week-fit issues —
#520 → W4; #424/#349 → W5; #278/#322/#571/#541/#306 → W6 — moved out of this bucket into the
week tables above.)_

---

## How to update this file

When merging a PR:

1. Find the task(s) it implements in the relevant cycle section (once the cycle plan exists).
2. Flip `⬜` → `✅` (or `⬜` → `🟡` if partial).
3. Append the PR link: `— [PR #N](https://github.com/.../pull/N)`.
4. Update the cycle subtotal and the **Snapshot** table (open PRs/issues).
5. If the PR closes a carried-over item above, strike it through with the closing ref.
6. If the PR added out-of-scope work, add a row with a note (same honesty rule as v1).

PR-template checkbox enforces this. If the change is purely tooling / docs that doesn't map
to a tracked task, tick the "N/A" checkbox.
