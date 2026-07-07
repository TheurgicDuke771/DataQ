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
| **Current cycle** | **v1.1 — 6 weeks + a stretch week, 2026-07-04 → 2026-08-15 (+ W7 stretch to 2026-08-22)** (planned 2026-07-04 from [context/post-v1-roadmap.md](../context/post-v1-roadmap.md)). Sequencing is **subscription-driven**: Weeks 1–3 extract everything that needs the expiring Snowflake (lapses within days) and Azure (~2026-07-25) subscriptions, then wind down gracefully; Weeks 4–6 run the roadmap's recommended opening sequence (Theme-1 `schema_drift` + `anomaly` → scale-aware execution G-b → incident/lineage design G-d) on cloud-independent infra; W7 is the stretch/burn-down buffer. See [Cycle plan](#cycle-plan--v11-6-weeks--stretch-2026-07-04--2026-08-22) below. |
| **Open issues** | **59** (W2 2026-07-05 — **alerting batch**: #386 (dedup severity single-source) + #387 (suppression guard) + #388 (alert_on drift-guard) closed via [#656](https://github.com/TheurgicDuke771/DataQ/pull/656); #389 closed won't-fix (Slack/email publishers made the per-channel name correct); **#655 closed via [#657](https://github.com/TheurgicDuke771/DataQ/pull/657)** — one canonical `db.models.SEVERITY_RANK` + `FAILING_TIERS` + shared `worst_severity()` helper (converged the 3 rank maps + the builder/run_service worst-severity loops); **#416 closed via [#659](https://github.com/TheurgicDuke771/DataQ/pull/659)** — enriched Slack/email alerts (deep link + metadata + expected-vs-observed via a shared pure `alerting/render.py`); **follow-up #661 closed via [#662](https://github.com/TheurgicDuke771/DataQ/pull/662)** — fully **tabular email** (run-details + failing-checks tables) + **owner name** across Slack/email/Teams (Teams card brought to #416 parity: metadata facts + "View run" action). #416 + #661 **deployed + live-verified in prod** (fresh non-deduped failing run → Slack + email delivered, enriched format confirmed). — **UI-gap remediation batch** (user-reported: "these aren't in the UI"): six gaps where a backend feature shipped without its frontend, each built → agentic-reviewed → merged → **[#630](https://github.com/TheurgicDuke771/DataQ/issues/630) dbt connection UI** ([#637](https://github.com/TheurgicDuke771/DataQ/pull/637)), **[#631](https://github.com/TheurgicDuke771/DataQ/issues/631) PAT management UI** ([#636](https://github.com/TheurgicDuke771/DataQ/pull/636)), **[#632](https://github.com/TheurgicDuke771/DataQ/issues/632) stale RunNowPanel stub** ([#638](https://github.com/TheurgicDuke771/DataQ/pull/638)), **[#633](https://github.com/TheurgicDuke771/DataQ/issues/633) per-suite Slack/email** (full-stack: migration `d2e3f4a5b6c7` + both publishers + panel, [#639](https://github.com/TheurgicDuke771/DataQ/pull/639)), **[#635](https://github.com/TheurgicDuke771/DataQ/issues/635) policy-panel catalog dropdown** ([#641](https://github.com/TheurgicDuke771/DataQ/pull/641)), **[#634](https://github.com/TheurgicDuke771/DataQ/issues/634) auto-classify column-policy on suite create** (async, fail-soft, [#642](https://github.com/TheurgicDuke771/DataQ/pull/642)); follow-ups filed [#640](https://github.com/TheurgicDuke771/DataQ/issues/640) (flaky LiveRunProgress) + [#643](https://github.com/TheurgicDuke771/DataQ/issues/643) (stale policy on target change). **Deployed to prod 2026-07-05** (run 28757740586, sha b394a889): the migrate job applied `d2e3f4a5b6c7` (the #633 `suite_notifications` columns) then rolled api/worker/frontend — prod healthy (frontend 200, api 401 auth-enforced), App Insights clean (no schema/column errors). A **`ui-tester` agent** (desktop+mobile UI + backend↔frontend parity audit) was added ([#645](https://github.com/TheurgicDuke771/DataQ/pull/645)) to catch this gap class going forward. — **Second UI-gap batch (user-flagged + agent-sweep findings, merged 2026-07-06, each agentic-reviewed):** [#618](https://github.com/TheurgicDuke771/DataQ/issues/618) stale "Azure AD (MSAL)" label → label derived from the runtime `authMode` + MSAL comment-rot scrub ([#679](https://github.com/TheurgicDuke771/DataQ/pull/679)); [#647](https://github.com/TheurgicDuke771/DataQ/issues/647) admin webhook-config mislabeled dbt rows as airflow → per-provider dispatch, `DBT_WEBHOOK_SECRET_NAME` documented ([#680](https://github.com/TheurgicDuke771/DataQ/pull/680)); [#652](https://github.com/TheurgicDuke771/DataQ/issues/652) dbt in the Suite-Triggers dropdown + Pipeline-runs filter + admin Webhooks labels ([#681](https://github.com/TheurgicDuke771/DataQ/pull/681)); [#654](https://github.com/TheurgicDuke771/DataQ/issues/654) connection version-history drawer on the edit page ([#683](https://github.com/TheurgicDuke771/DataQ/pull/683)); [#653](https://github.com/TheurgicDuke771/DataQ/issues/653) per-check alert snooze/unsnooze in the suite detail panel ([#684](https://github.com/TheurgicDuke771/DataQ/pull/684) + follow-ups [#687](https://github.com/TheurgicDuke771/DataQ/pull/687)); [#651](https://github.com/TheurgicDuke771/DataQ/issues/651) author-time GX-registry validation of expectation-kind checks ([#686](https://github.com/TheurgicDuke771/DataQ/pull/686) + review follow-ups [#689](https://github.com/TheurgicDuke771/DataQ/issues/689)→[#690](https://github.com/TheurgicDuke771/DataQ/pull/690): dict-key size gap, bounded 422 echo, 1k→10k cap, PATCH no longer bricks pre-#651 legacy rows); [#617](https://github.com/TheurgicDuke771/DataQ/issues/617) mobile/responsive pass ([#688](https://github.com/TheurgicDuke771/DataQ/pull/688) — suite-detail stacks below `md`, tables get `scroll={{x}}`, Sider-trigger/header overlap fixed); **residual [#692](https://github.com/TheurgicDuke771/DataQ/issues/692) closed via [#693](https://github.com/TheurgicDuke771/DataQ/pull/693)** (user-flagged after #688: the checks-list `SimpleList.Item` rows still collapsed to char-per-line at ≤360px because the never-shrinking actions squeezed the content — fixed in the shim with `flex-wrap` + a `min(100%,180px)` content floor so actions drop to their own line; header brand `nowrap`; live-verified 320/390/1280 + `SimpleList.test.tsx`). Follow-ups filed to `v1.1 Backlog`: [#682](https://github.com/TheurgicDuke771/DataQ/issues/682) (webhook-auth metadata onto the `OrchestrationProvider` seam) + [#685](https://github.com/TheurgicDuke771/DataQ/issues/685) (version-history purge/redact path). — **#524 + #589 closed via [#628](https://github.com/TheurgicDuke771/DataQ/pull/628)** — opencensus→OTel log migration + generic OTLP endpoint, both behind a new shared `otel.py` exporter seam; agentic review (code-reviewer + silent-failure-hunter) folded in; **deployed + parity live-verified** (local OTLP Collector received spans+logs redacted over the wire; prod App Insights `traces` now carry `cloud_RoleName=dataq-api/worker` via the OTel exporter, marker probe traced end-to-end). W1 progress 2026-07-04/05: #194/#195/#587 closed via #602/#603/#607; **#461 closed via #613** (PATs phase 1 merged — live PAT mint pending prod deploy) and **#583 via #614**; #588 rehearsed→reversed→reopened on W3; **#609 closed 2026-07-05** (self-hosted dbt Core lineage — dbt build green local + ACA Job, DataQ suite green on a dynamic table, artifacts to ADLS; unblocks #611) + **#611 closed 2026-07-05** (dbt as a third `OrchestrationProvider` — ADR 0029, #620; deployed to prod + **webhook live-verified**: signed event → pipeline_run → bound mart suite fired green; poll live-read deferred to #622) + **#372 + #622 closed 2026-07-05** (SecretStore.delete #625 + custom least-priv KV get/list/set/soft-delete role #624 — both deployed & prod-verified: connection-delete soft-deletes its secret) + #286 pulled W7→W2; [#604](https://github.com/TheurgicDuke771/DataQ/issues/604) — CI-flaky ConnectionNew test — filed and closed same-day by #603; filed still-open: [#601](https://github.com/TheurgicDuke771/DataQ/issues/601) prettierignore gap + [#605](https://github.com/TheurgicDuke771/DataQ/issues/605) surface run failure reasons, both `v1.1 Backlog`). At the 2026-07-04 full backlog remap: **55 scheduled** onto `v1.1 Week 1..6` + **10** on `v1.1 Week 7 — stretch` + the cycle epic [#597](https://github.com/TheurgicDuke771/DataQ/issues/597). **`v1.1 Backlog` (renamed from `Backlog (post-v1 / testing)`) holds only the new filings #601/#605** — every other open issue sits on a week milestone; the backlog milestone is the default for new filings. Every scheduled issue carries an **Acceptance criteria** checklist and every milestone description its **Exit gate** (both added 2026-07-04). Theme map in [post-v1-roadmap.md](../context/post-v1-roadmap.md). |
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
| ~~[#583](https://github.com/TheurgicDuke771/DataQ/issues/583)~~ | ~~MCP `profile_column` 422s on SQL suites without explicit table/schema~~ — closed by [#614](https://github.com/TheurgicDuke771/DataQ/pull/614) (defaults to the run target) |
| [#584](https://github.com/TheurgicDuke771/DataQ/issues/584) | MCP NL tool-selection spot-check — the softest W7 tick (WEEK8_TODO carry-over, filed at its retirement) |

### Long-standing follow-ups (pre-go-live filings, all on Backlog)

| # | Title |
|---|---|
| ~~[#194](https://github.com/TheurgicDuke771/DataQ/issues/194)~~ | ~~Snowflake key-pair: encrypted (passphrase-protected) private keys~~ — closed by [#602](https://github.com/TheurgicDuke771/DataQ/pull/602) |
| ~~[#195](https://github.com/TheurgicDuke771/DataQ/issues/195)~~ | ~~Snowflake key-pair: migrate off deprecated GX `connect_args` private_key path~~ — closed by [#603](https://github.com/TheurgicDuke771/DataQ/pull/603) |
| [#197](https://github.com/TheurgicDuke771/DataQ/issues/197) / [#199](https://github.com/TheurgicDuke771/DataQ/issues/199) / [#204](https://github.com/TheurgicDuke771/DataQ/issues/204) | Week-4 frontend refactor nits (shared antd-Select test helper · `useAsyncAction` toast helper · drawer/delete dedup) |
| [#327](https://github.com/TheurgicDuke771/DataQ/issues/327) | Column profiler N+1 query batching |
| [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) / [#351](https://github.com/TheurgicDuke771/DataQ/issues/351) | Week-6 results/connection-page follow-ups |
| [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) | `SecretStore` has no delete — webhook/connection secrets orphan on clear/delete |
| ~~[#524](https://github.com/TheurgicDuke771/DataQ/issues/524)~~ | ~~opencensus → OTel log-export migration~~ — closed by [#628](https://github.com/TheurgicDuke771/DataQ/pull/628) (also landed #589, the generic OTLP endpoint) |
| [#529](https://github.com/TheurgicDuke771/DataQ/issues/529) | MCP tool expansion (candidate tiers from #530 — Theme 13) |
| [#532](https://github.com/TheurgicDuke771/DataQ/issues/532) | Dry-run preview is Snowflake-only — extend to Unity Catalog + flat-file suites |
| [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) | AWS/GCP deploy IaC (post-v1 per ADR 0028) |
| [#461](https://github.com/TheurgicDuke771/DataQ/issues/461) | DataQ-issued API keys / service tokens — **phase 1 (PATs) built** [PR #613](https://github.com/TheurgicDuke771/DataQ/pull/613), v1.1 W1; service principals remain phase 2 |

_The rest of the 55 are mapped by theme in [post-v1-roadmap.md](../context/post-v1-roadmap.md) — that doc, plus the GitHub milestone, is the full register; this table only names the ones the v1 ledger and CLAUDE.md §13 called out individually._

### Pending design decisions

| Decision | Affects | Status |
|---|---|---|
| Two-connection model for `comparison` checks (**ADR 0015**, reserved in ADR 0014) | The reconciliation/`comparison` monitor kind, when its theme is picked up | ⬜ open, non-blocking until then |

### Standing decisions of record & guardrails (carried from the go-live close)

| Decision | Record |
|---|---|
| ADR 0026 (API keys) — **phase 1 (user-scoped PATs) Accepted + built** (v1.1 W1, #461/PR #613; sha256-at-rest rationale in the phase-1 record); service principals stay phase 2; HTTP Basic rejected; the az-CLI pre-auth interim (#565) retires once PATs are the standing headless credential | [ADR 0026](adr/0026-auth-api-keys-and-principal-seam.md) |
| Databricks **Free Edition** = demo/eval only; paid workspace before any commercial use | gap **G-h**, [post-v1-roadmap.md](../context/post-v1-roadmap.md) |
| **Pre-marketplace harness teardown** — strip Flows A/B/C, the 5 harness connections, demo users, and the seeded-breach check before any marketplace/customer-facing artifact | gap **G-i**, [post-v1-roadmap.md](../context/post-v1-roadmap.md) + `deploy/README.md` |
| **Ops/renewal timers consciously skipped** — the Sept-2026 demo-credential cluster self-signals via #419 alerting; recovery = re-mint + KV update | [retro-v1.md](retro-v1.md) |
| Key Vault **purge protection left off** (demo-scoped vault) | `deploy/README.md` |
| **Recurring cadences stay manual** (weekly security scan — CONTRIBUTING r36, quarterly MCP supply-chain audit — r39 (next ~2026-10-01), Dependabot triage) — no timer infrastructure, run session-driven; extends the ops-timers-skipped decision. Revisit trigger: second contributor or production-critical use. Recorded 2026-07-04 at the `WEEK8_TODO` retirement (its C2 item) | this table (extends [retro-v1.md](retro-v1.md)'s ops-timers decision) |

---

## Cycle plan — v1.1 (6 weeks + stretch, 2026-07-04 → 2026-08-22)

> Planned 2026-07-04 from [context/post-v1-roadmap.md](../context/post-v1-roadmap.md) (the
> generator input). GitHub mirror: milestones **`v1.1 Week 1..7`** (due Saturdays), the cycle
> epic [#597](https://github.com/TheurgicDuke771/DataQ/issues/597), and the **DataQ Roadmap**
> project (all 65 scheduled issues carry the `v1.1 week` single-select + Status).
> **The backlog is fully mapped** (2026-07-04 remap, two waves): `Backlog (post-v1 / testing)`
> was renamed **`v1.1 Backlog`**, 8 week-fit issues moved into W4–W6, then the remaining 31
> were grouped and mapped — 21 into W2–W6, 10 onto the appended **W7 stretch** — leaving the
> backlog milestone **empty** (it stays as the default for new filings). Every scheduled issue
> carries an **Acceptance criteria** checklist; every milestone description carries its
> **Exit gate** (mirrored per week below). Design-captured non-issue work (asset entity, PG
> adapter, LLM seams, …) remains themed in the roadmap doc as next-cycle generator input.
>
> **Sequencing is subscription-driven, not theme-driven, for the first half:** the
> **Snowflake subscription lapses within days** of planning and the **Azure subscription ends
> ~2026-07-25** (end of W3). So W1–3 extract everything that needs live cloud — last-window
> Snowflake work, verify-against-App-Insights portability work, PATs while the AAD path is
> still the reference — and end in a *deliberate* wind-down (G-i teardown + `terraform
> destroy`) instead of a lapse. W4–6 then run the roadmap's recommended opening sequence
> (Theme-1 `schema_drift` + `anomaly` → scale-aware execution G-b → incident/lineage design
> G-d) on infra that survives: the local stack, S3 (AWS), and Databricks Free Edition.

### v1.1 W1 — Snowflake close-out + PATs — **COMPLETE 2026-07-05** (5 ✅ + #588 re-homed W3; milestone closed; exit gate MET)

> **Premise correction (2026-07-04, post-planning):** the Snowflake trial in fact runs to
> **~2026-07-25** ($384 credits left) — the same window as Azure, not "within days". The
> last-window work (#194/#195/#587) was already done and stands; #588 was rehearsed,
> reversed, and re-homed to W3 (real lapse). The extended window adds three W2 items:
> [#609](https://github.com/TheurgicDuke771/DataQ/issues/609) (dbt-modeled lineage objects
> on Snowflake — G-d test bed; **rescoped 2026-07-04 to self-hosted dbt Core**, not
> Airflow-hosted), [#611](https://github.com/TheurgicDuke771/DataQ/issues/611) (dbt as a
> **third `OrchestrationProvider`** — webhook + artifact-poll, host-agnostic; split from
> #609 — dbt Cloud free tier has no API/scheduler, and Snowflake/Databricks hosting would
> couple the provider to the vendor's run API), and
> [#286](https://github.com/TheurgicDuke771/DataQ/issues/286)
> (Iceberg v2 ADLS spike, pulled forward from the W7 stretch).

⚡ **The three Snowflake-live rows are day-1 work, in this order** — the subscription lapses
within days; after it does they can no longer be live-verified. **PATs (#461) start right
behind them** (pulled forward from W2 at planning, 2026-07-04): the biggest unlocker in the
backlog, and it must land while Azure AD is still the reference validator.

| Status | Task | Theme / gap |
|---|---|---|
| ✅ | [#194](https://github.com/TheurgicDuke771/DataQ/issues/194) Snowflake key-pair: encrypted (passphrase-protected) private keys — **live-verified** (combined `{private_key, passphrase}` secret payload, one `secret_ref`, atomic re-auth rotation; encrypted-key test-connection + GX suite run green on live Snowflake 2026-07-04) — [PR #602](https://github.com/TheurgicDuke771/DataQ/pull/602) | Theme 8 |
| ✅ | [#195](https://github.com/TheurgicDuke771/DataQ/issues/195) Snowflake key-pair: migrate off deprecated GX `connect_args` path — **upgraded to bugfix**: the old route never passed GX 1.17 validation for key-pair suite runs; now the supported kwargs form (base64-DER `private_key`, `role` required), live-verified with zero deprecation warnings — [PR #603](https://github.com/TheurgicDuke771/DataQ/pull/603) | Theme 8 |
| ✅ | [#587](https://github.com/TheurgicDuke771/DataQ/issues/587) Snowflake scale/volume baseline — **captured live 2026-07-04**: 1.2M-row suite run 12.2s wall / <50MB worker delta / ~0.08 credits; recorded in [perf-baseline-snowflake.md](perf-baseline-snowflake.md) — [PR #607](https://github.com/TheurgicDuke771/DataQ/pull/607) | Theme 7 / G-b |
| 🔵 | [#588](https://github.com/TheurgicDuke771/DataQ/issues/588) Retire the harness Snowflake leg — **rehearsed 2026-07-04 then REVERSED same day** (trial actually runs to ~2026-07-25): procedure proven end-to-end (incl. history-queryability check: 1,940 results, all with `metric_value`), leg re-keyed on a fresh 25-day PAT; **reopened + re-homed to W3** to run at actual lapse with #590 — [PR #608](https://github.com/TheurgicDuke771/DataQ/pull/608) + reversal notes on the issue | ops / G-i |
| ✅ | [#461](https://github.com/TheurgicDuke771/DataQ/issues/461) **PATs phase 1** (ADR 0026): second authenticator behind `get_current_user`, REST + MCP identically — [PR #613](https://github.com/TheurgicDuke771/DataQ/pull/613) (`api_keys` + `dq_live_` sha256-at-rest, uniform 401, show-once `/me/api-keys`, PAT-or-JWT composite on `/mcp`; ADR 0026 **Accepted, phase 1**). **Live exit MET 2026-07-05** post-deploy: admin PAT `dq_live_NNZ5…` (30d, dataq-admin — /me admin:true, 10 suites, /admin 200) + member PAT `dq_live_uTSi…` (90d, Olivia — 4 suites, /admin 403), both exercised vs prod REST + `/mcp/`; uniform-401 probes green; evidence on the issue | Theme 3 / G-e |
| ✅ | [#583](https://github.com/TheurgicDuke771/DataQ/issues/583) MCP `profile_column`: default to the suite run target — same resolver as the run path (incl. flat-file batch materialization); explicit params still win; targetless suite → actionable error — [PR #614](https://github.com/TheurgicDuke771/DataQ/pull/614) | Theme 13 |

**Exit gate: MET 2026-07-05** (as amended by the 2026-07-04 premise correction — #588's
retirement moved to W3 with the real trial lapse): #194/#195 live-verified + #587 baseline
recorded, **and PATs live** — 1 workspace-admin + 1 member PAT minted and exercised against
prod REST + `/mcp/` (two-tier authz matrix: 10-vs-4 suites, /admin 200-vs-403). The PATs
are the standing headless credential from here (az-CLI-bearer interim #565 retired).

### v1.1 W2 — Portability: OTel logs, secrets lifecycle, dry-run depth (due 2026-07-18) — 11/14

Land the vendor-neutral seams **while App Insights / Key Vault / live `/mcp` still exist to
verify parity against** (ADR 0010/0013/0028 discipline). Live checks from here on run on the
W1 admin/member PATs instead of the Azure-CLI token workaround (#565).

| Status | Task | Theme / gap |
|---|---|---|
| ✅ | [#609](https://github.com/TheurgicDuke771/DataQ/issues/609) Self-hosted dbt Core lineage on Snowflake (RETAIL→ANALYTICS_STG views→ANALYTICS dynamic tables; runs as `DATAQ_LOADER`) — `dbt build` green local + ACA Job, DataQ suite (checks + freshness) green on `MART_ORDER_REVENUE`, artifacts to ADLS (2026-07-05); unblocks #611 | Theme 14 / G-d |
| ✅ | [#611](https://github.com/TheurgicDuke771/DataQ/issues/611) dbt as a **third `OrchestrationProvider`** (ADR 0029) — `DbtProvider` + adapter + 3-scheme artifact reader (adls/s3/file) + HMAC `POST /orchestration/events/dbt` + 10-min poll + migration + `integrations/dbt/` snippet (#620, agentic review + migration-safety addressed). **Deployed to prod + live-verified 2026-07-05**: signed webhook → `pipeline_run` (dbt, succeeded) → bound mart suite fired + ran green against the #609 dynamic table. Poll-path live ADLS read deferred to [#622](https://github.com/TheurgicDuke771/DataQ/issues/622) (api UAMI read-only on KV) | Theme 14 / G-d |
| ✅ | [#524](https://github.com/TheurgicDuke771/DataQ/issues/524) opencensus → OTel log-export migration — `AzureLogHandler` replaced with the OTel `LoggerProvider` + `AzureMonitorLogExporter` behind a new shared `otel.py` exporter seam; structlog redaction preserved on **both** body and exported attributes (stricter than opencensus); opencensus + the #393/#405 `createLock` hardening dropped; fork-safe via `os.register_at_fork`; telemetry setup isolated (best-effort, never crashes startup — review). **Merged #628** (code-reviewer + silent-failure-hunter addressed). **Deployed to prod + live-verified 2026-07-05**: post-roll App Insights `traces` now carry `cloud_RoleName=dataq-api/dataq-worker` (the OTel `service.name`), a marker probe request found end-to-end; spans still land too | Theme 14 |
| ✅ | [#589](https://github.com/TheurgicDuke771/DataQ/issues/589) Generic OTLP exporter endpoint — `OTEL_EXPORTER_OTLP_ENDPOINT` adds an OTLP/HTTP backend for **spans + logs** behind the same `otel.py` seam (Azure = one impl, ADR 0010); both may run at once (the parity check); base-endpoint semantics; env-var reference documented. **Merged #628**. **Parity live-verified 2026-07-05**: local OTel Collector received spans **and** logs over the wire with redaction intact (body `token=<redacted>` + attribute `password=<redacted>`), and App Insights received the same via the Azure exporter — both backends behind one seam | Theme 14 |
| ✅ | [#372](https://github.com/TheurgicDuke771/DataQ/issues/372) `SecretStore.delete` — added to the Protocol + all 3 impls (fail-soft); connection-delete + webhook clear/delete remove the orphaned secret; unique per-set webhook ref avoids KV soft-delete name-reuse (review). **Deployed + live-verified on prod KV** 2026-07-05 (create-with-secret → delete → secret soft-deleted). #625 | Theme 6 |
| ✅ | [#622](https://github.com/TheurgicDuke771/DataQ/issues/622) App KV identity was read-only → API connection-create-with-secret 502'd (found via #611). Custom least-priv **get/list/set/soft-delete** role (not built-in Officer). **Applied + verified on prod** (secret write 200, live ADLS read 200, delete soft-deletes). #624 | Theme 6 / infra |
| ⬜ | [#532](https://github.com/TheurgicDuke771/DataQ/issues/532) Dry-run preview: extend Snowflake-only → Unity Catalog + flat-file (moved from W1 — cloud-independent, no deadline) | Theme 8 |
| ⬜ | [#584](https://github.com/TheurgicDuke771/DataQ/issues/584) MCP NL tool-selection spot-check vs live `/mcp` (4 canonical queries), authenticated via the W1 PATs | Theme 13 |
| ✅ | [#386](https://github.com/TheurgicDuke771/DataQ/issues/386) Alerting batch (1/4): `dedup._RANK` now derives from the shared `base.FAILING_TIERS` (+ drift-guard test); values unchanged (#656) | Theme 5 |
| ✅ | [#387](https://github.com/TheurgicDuke771/DataQ/issues/387) Alerting batch (2/4): `suppression.all_failures_snoozed` early-returns `False` on `run.status=='failed'` so an execution failure can't be snooze-silenced (defensive; #656) | Theme 5 |
| ✅ | [#388](https://github.com/TheurgicDuke771/DataQ/issues/388) Alerting batch (3/4): `alert_on` literals locked to `ALERT_ON_POLICIES` (model↔validation already single-sourced; routing constants + CHECK-constraint SQL drift-guarded; #656) | Theme 5 |
| ✅ | [#389](https://github.com/TheurgicDuke771/DataQ/issues/389) Alerting batch (4/4): **closed won't-fix** — overtaken by the Slack/email publishers; `teams_webhook_secret_name` is now correctly channel-specific + symmetric with `slack_webhook_secret_name`, renaming would break that symmetry. Follow-up [#655](https://github.com/TheurgicDuke771/DataQ/issues/655) filed (converge the 3 severity-rank maps) | Theme 5 |
| ✅ | [#416](https://github.com/TheurgicDuke771/DataQ/issues/416) Enrich Slack/email alerts — "View run" deep link (reuses `PUBLIC_BASE_URL`), run metadata (env/trigger/started/duration), per-check expected-vs-observed + redacted sample preview; shared pure `alerting/render.py` formatters ([#659](https://github.com/TheurgicDuke771/DataQ/pull/659)). Live Slack+email visual verify deferred to the smoke lane (no transport in CI) | Theme 5 |
| ⬜ | [#488](https://github.com/TheurgicDuke771/DataQ/issues/488) Workspace-admin visibility in MCP tools + schedules — rides PATs + #584 (mapped 2026-07-04) | Theme 3 |

**Exit gate:** observability + secrets + alerting vendor-neutral and Azure-verified — OTel logs
in BOTH App Insights and a local OTLP consumer; `SecretStore.delete` verified on live KV;
dry-run covers UC + flat-file; alerting batch merged; #488/#584 pass — every live check
authenticated by a W1 PAT.

### v1.1 W3 — Azure wind-down + local-first posture (due 2026-07-25) — 0/10

Azure ends ~this week's due date. Order matters: final live validation first, teardown last.
_(Planning correction 2026-07-04: #492 — ADF webhook live delivery — was scheduled here as a
"final decision" item but had in fact **closed 2026-07-02** during the W7 live smoke, delivered
via the Action-Group metric-alert path; re-homed to its Week-7 milestone.)_

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | Final live-prod E2E of the W1–2 landings (OTel parity, PAT auth, secrets lifecycle) before anything is destroyed | — |
| ⬜ | [#590](https://github.com/TheurgicDuke771/DataQ/issues/590) Azure wind-down: G-i harness teardown, `terraform destroy`, credential retirement, state disposition (harness compute already stopped 2026-07-04 — wake via `harness_window.sh`, see the #590 runbook) | ops / G-i |
| ⬜ | [#591](https://github.com/TheurgicDuke771/DataQ/issues/591) Local-first runtime posture: docker-compose parity for secrets/auth/observability; surviving datasources = local files + S3 + Databricks Free | ops / Theme 14 |
| ⬜ | [#197](https://github.com/TheurgicDuke771/DataQ/issues/197) Refactor batch (1/7): shared antd `selectOption` test helper (batch mapped from backlog 2026-07-04 — local code work for the ops-heavy week; lands the shared helpers before W5's UI features) | Theme 10 |
| ⬜ | [#199](https://github.com/TheurgicDuke771/DataQ/issues/199) Refactor batch (2/7): `useAsyncAction` toast helper | Theme 10 |
| ⬜ | [#204](https://github.com/TheurgicDuke771/DataQ/issues/204) Refactor batch (3/7): consolidate drawer/delete duplication | Theme 10 |
| ⬜ | [#229](https://github.com/TheurgicDuke771/DataQ/issues/229) Refactor batch (4/7): shared `AsyncBody`/`AsyncTable` helper | Theme 10 |
| ⬜ | [#236](https://github.com/TheurgicDuke771/DataQ/issues/236) Refactor batch (5/7): shared `connectionOptionLabel(c)` helper | Theme 10 |
| ⬜ | [#237](https://github.com/TheurgicDuke771/DataQ/issues/237) Refactor batch (6/7): `ImportSuiteDrawer` unreachable empty-connections hint | Theme 10 |
| ⬜ | [#326](https://github.com/TheurgicDuke771/DataQ/issues/326) Refactor batch (7/7): `RunNowPanel` redundant `{open && …}` guard | Theme 10 |

**Exit gate:** the Azure exit is deliberate and complete — final live E2E green BEFORE teardown,
#590 done (nothing billable remains except by choice), a fresh clone reaches a green local E2E
with zero Azure dependencies (#591), and the frontend refactor batch is merged.

### v1.1 W4 — `schema_drift` monitor kind (due 2026-08-01) — 0/7

Cloud-independent from here on. Engine follow-ups land first — they touch the code #592 builds on.

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#427](https://github.com/TheurgicDuke771/DataQ/issues/427) Reuse one warehouse connection per monitor run | Theme 1 |
| ⬜ | [#428](https://github.com/TheurgicDuke771/DataQ/issues/428) Consolidate SQL-identifier validation + dedup `run_monitors` boilerplate | Theme 1 |
| ⬜ | [#429](https://github.com/TheurgicDuke771/DataQ/issues/429) Fix `MonitorRunner` `isinstance`-on-Protocol gate | Theme 1 |
| ⬜ | [#592](https://github.com/TheurgicDuke771/DataQ/issues/592) `schema_drift` end-to-end (baseline snapshot + diff engine + authoring UI, all datasource paths) — baseline persistence designed for two consumers (W5 anomaly) | Theme 1 / G-a |
| ⬜ | [#520](https://github.com/TheurgicDuke771/DataQ/issues/520) Freshness/volume monitors: add flat-file (S3/local) support — SQL-only today (mapped from backlog 2026-07-04; same `run_monitors` engine code, and #592's flat-file path needs it) | Theme 1 |
| ⬜ | [#476](https://github.com/TheurgicDuke771/DataQ/issues/476) Profiler identifier-casing + CSV-delimiter limitations — same flat-file introspection code #592 touches (mapped 2026-07-04) | Theme 7 |
| ⬜ | [#124](https://github.com/TheurgicDuke771/DataQ/issues/124) DQ-dimension classification on checks — check-model/editor open this week anyway (mapped 2026-07-04) | Theme 2 |

**Exit gate:** `schema_drift` demoable end-to-end (author → dry-run → run → banded severity) on
flat-file + UC + local SQL; monitor kinds no longer SQL-only (#520); engine follow-ups closed;
baseline-persistence shape documented for W5 anomaly reuse.

### v1.1 W5 — `anomaly` monitor kind + metric trends (due 2026-08-08) — 0/8

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#593](https://github.com/TheurgicDuke771/DataQ/issues/593) `anomaly` kind — rolling z-score + seasonality baseline over `metric_value` history; `skip` on cold start | Theme 1 / G-a |
| ⬜ | [#594](https://github.com/TheurgicDuke771/DataQ/issues/594) Per-check `metric_value` trend view with threshold bands (+ anomaly-baseline overlay — doubles as #593's visual debugger) | Theme 9 |
| ⬜ | [#568](https://github.com/TheurgicDuke771/DataQ/issues/568) Validate severity-threshold ordering at authoring time | Theme 1 |
| ⬜ | [#424](https://github.com/TheurgicDuke771/DataQ/issues/424) Run-detail sample header says 'values redacted' even when non-PII values surface (mapped from backlog 2026-07-04 — results-surface week) | Theme 9 |
| ⬜ | [#349](https://github.com/TheurgicDuke771/DataQ/issues/349) Results: dedupe the runs fetch across tabs + share date-window presets with Dashboard (mapped from backlog 2026-07-04 — same surfaces as #594) | Theme 9 |
| ⬜ | [#345](https://github.com/TheurgicDuke771/DataQ/issues/345) Results export — PDF report of an executed run (mapped 2026-07-04 — results-surface week) | Theme 9 |
| ⬜ | [#283](https://github.com/TheurgicDuke771/DataQ/issues/283) Check version history — restore/revert to a previous version (mapped 2026-07-04) | Theme 9 |
| ⬜ | [#351](https://github.com/TheurgicDuke771/DataQ/issues/351) Test Connection on the New/Edit connection page (draft-connection test endpoint; mapped 2026-07-04) | Theme 8 |

**Exit gate:** the anomaly kind produces banded deviation scores on real `metric_value` history
(`skip` on cold start) and the trend view renders bands + baseline overlay; threshold ordering
validated at authoring (#568); the results/connections UX batch (#345/#283/#351/#424/#349) merged.

### v1.1 W6 — scale-aware execution + hardening + cycle close (due 2026-08-15) — 0/15

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
| ⬜ | [#318](https://github.com/TheurgicDuke771/DataQ/issues/318) Per-check incremental run progress — natural rider on #595's partitioned execution (mapped 2026-07-04) | Theme 7 |
| ⬜ | [#457](https://github.com/TheurgicDuke771/DataQ/issues/457) Partial-index/predicate drift guard for a 3rd OrchestrationProvider (mapped 2026-07-04 — small-bug batch) | Theme 6 |
| ⬜ | [#310](https://github.com/TheurgicDuke771/DataQ/issues/310) History/audit strategy ADR (audit log + soft-delete decision) — pairs with the #596 design doc (mapped 2026-07-04) | Theme 6 / ADR 0020 |
| ⬜ | Cycle retro + `v1.1.0` tag + next-cycle planning input refresh | — |

**Exit gate:** a deliberately oversized local file / UC table runs under the memory cap with
sampled-ness recorded (vs the #587 baseline); perf + hardening/small-bug batches green; the
G-d design doc (#596) + audit ADR (#310) merged with next-cycle phase-1 issues filed; retro
written; `v1.1.0` tagged.

### v1.1 W7 — stretch: backlog burn-down (due 2026-08-22) — 0/10

Appended at the 2026-07-04 full remap (user decision: map everything; W7 holds what didn't fit
W1–6). Burn down after the W6 close; **anything left rolls to v1.2 at the retro — explicitly,
never silently**.

| Status | Task | Theme / gap |
|---|---|---|
| ⬜ | [#431](https://github.com/TheurgicDuke771/DataQ/issues/431) Compliance G1: data-access audit trail (the HIPAA gate) | Theme 4 |
| ⬜ | [#432](https://github.com/TheurgicDuke771/DataQ/issues/432) Compliance G2: data-subject-rights machinery | Theme 4 |
| ⬜ | [#433](https://github.com/TheurgicDuke771/DataQ/issues/433) Compliance G3: warehouse-tag PII classification (Snowflake source lapses W1 — UC tags remain) | Theme 4 |
| ⬜ | [#434](https://github.com/TheurgicDuke771/DataQ/issues/434) Compliance G4: region/residency assertion | Theme 4 |
| ⬜ | [#435](https://github.com/TheurgicDuke771/DataQ/issues/435) Compliance G5: encryption-at-rest in IaC + CMK (partially blocked post-#590 — no cloud IaC target) | Theme 4 |
| ⬜ | [#529](https://github.com/TheurgicDuke771/DataQ/issues/529) MCP tool expansion — tier-1 read-only set (week-sized) | Theme 13 |
| ⬜ | [#286](https://github.com/TheurgicDuke771/DataQ/issues/286) Apache Iceberg v2/v3 table-format support (spike first) | Theme 2 |
| ⬜ | [#244](https://github.com/TheurgicDuke771/DataQ/issues/244) Suite-on-suite triggering | Theme 8 |
| ⬜ | [#466](https://github.com/TheurgicDuke771/DataQ/issues/466) Interactive datasource browsing (ADLS half blocked post-Azure; S3 + UC picker remain) | Theme 8 |
| ⬜ | [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) AWS/GCP deploy IaC (blocked until a target cloud subscription exists) | Theme 14 |

**Exit gate (soft — stretch):** every remaining item closed or explicitly rolled to v1.2 with a
rationale note at the retro; nothing silently dropped.

### Not scheduled (design-captured, no issues yet)

**`v1.1 Backlog` is empty** — all filed issues are mapped above. What remains unscheduled is
the design-captured, not-yet-filed work themed in
[post-v1-roadmap.md](../context/post-v1-roadmap.md): Theme 2 LLM authoring/RCA (`LLMProvider`
seam, follows G-d), Theme 3 asset entity + asset-first IA (phase 1 gets filed by the #596
design doc), the Theme 8 adapter expansion (generic PG adapter is the natural v1.2 opener —
dogfoodable locally), Theme 14 ecosystem integrations, and the Theme 3/4 privacy/a11y packs.
These are the v1.2 generator input.

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
