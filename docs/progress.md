# DataQ — Progress tracker (v1.3 cycle)

> The **live task tracker**, active since `v1.2.0` (2026-10-04). The completed cycle
> ledgers are archived frozen at [progress-v1.md](progress-v1.md),
> [progress-v1.1.md](progress-v1.1.md) and [progress-v1.2.md](progress-v1.2.md);
> companions: [retro-v1.md](retro-v1.md), [retro-v1.1.md](retro-v1.1.md),
> [retro-v1.2.md](retro-v1.2.md).
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
| **v1 baseline** | `v1.0.0` tagged 2026-07-04; ledger at [progress-v1.md](progress-v1.md) |
| **v1.1 baseline** | `v1.1.0` — 2026-07-04 → 2026-08-21; ledger at [progress-v1.1.md](progress-v1.1.md) |
| **v1.2 baseline** | `v1.2.0` — 2026-08-22 → 2026-10-04: 533 commits, 513 issues closed across eight weekly milestones plus 223 in the backlog, ADRs 0042–0047, datasources 5 → 11, MCP tools 46 → 54; ledger at [progress-v1.2.md](progress-v1.2.md), retro at [retro-v1.2.md](retro-v1.2.md) |
| **Current cycle** | **v1.3 — 8 weeks, 2026-10-05 → 2026-11-29**, no net-new features: roadmap and filed features first (W1–W5), then bug fixes and security (W6), polish (W7), debt, decisions and the close (W8). Roadmap theme in [context/roadmap-v1.2-v1.3.md](../context/roadmap-v1.2-v1.3.md). |
| **Environment** | No live cloud environment (estate retired 2026-10-03). Work is verified on the local stacks; live verification against Snowflake, Unity Catalog, Azure or AWS waits on [#2224](https://github.com/TheurgicDuke771/DataQ/issues/2224). |
| **Open issues** | **83** open repo-wide (2026-10-07, live re-count), all in the eight v1.3 weekly milestones; `v1.3 Backlog` is empty and `v1.2 Backlog` is closed. |
| **Open PRs** | **0 open** (2026-10-07), apart from the one carrying this update. |

---

## Carried over from v1.2

Named so nothing rolls silently (see the retro's "What rolls"):

- **Security, filed and not fixed:** [#2348](https://github.com/TheurgicDuke771/DataQ/issues/2348) deleting a check rewrites run history · [#2383](https://github.com/TheurgicDuke771/DataQ/issues/2383) a suggested value set outlives a later PII classification · [#2240](https://github.com/TheurgicDuke771/DataQ/issues/2240) aggregate min/max on a sensitive column · [#2260](https://github.com/TheurgicDuke771/DataQ/issues/2260) oauthlib advisory (server-side only).
- **Waiting on something outside the repo:** [#2224](https://github.com/TheurgicDuke771/DataQ/issues/2224) cloud live verification (a test instance) · [#717](https://github.com/TheurgicDuke771/DataQ/issues/717) Iceberg v3 (trigger-gated) · [#732](https://github.com/TheurgicDuke771/DataQ/issues/732) listing readiness (decisions, or a hosted offer).
- **Process, from the retro:** changelog fragments (one file per PR) to end the serial merge conflicts ([#2395](https://github.com/TheurgicDuke771/DataQ/issues/2395), Week 2); keep a weekly milestone to its planned rows.
- Everything else open at the close was re-homed into the weekly milestones below.

---

## Cycle plan — v1.3 (8 weeks, 2026-10-05 → 2026-11-29)

> Confirmed by the maintainer 2026-10-05. **No net-new features this cycle:** build what is
> already on the roadmap or filed, features first (Weeks 1–5), then bug fixes and security
> (Week 6), polish (Week 7), and debt, decisions and the close (Week 8). Every issue open at
> the v1.2 close is in a week below; new filings go to the `v1.3 Backlog` milestone.

### v1.3 Week 1 — coverage & checks (due 2026-10-11)

| Status | Task |
|---|---|
| ⬜ | [#1661](https://github.com/TheurgicDuke771/DataQ/issues/1661) feat(monitors): zero-config auto-baselines — unknown-unknown detection without an authored check — researched 2026-10-07: ADR 0047 already covers all but sensitivity config above the check level; needs an ADR 0047 amendment and one decision (see the issue) |
| ✅ | [#2369](https://github.com/TheurgicDuke771/DataQ/issues/2369) feat(checks): enable/disable a check without deleting it (single and bulk) — migration [PR #2414](https://github.com/TheurgicDuke771/DataQ/pull/2414), code [PR #2415](https://github.com/TheurgicDuke771/DataQ/pull/2415) |
| ⬜ | [#1674](https://github.com/TheurgicDuke771/DataQ/issues/1674) feat(privacy): profiler-side PII auto-detection as an additional classification source — researched 2026-10-07: five design decisions needed before code (see the issue) |
| ⬜ | [#1675](https://github.com/TheurgicDuke771/DataQ/issues/1675) feat(monitors): pii_drift monitor kind — PII-looking data appearing in an unclassified column — blocked on #1674; also needs a migration widening the kind constraints |
| ✅ | [#1762](https://github.com/TheurgicDuke771/DataQ/issues/1762) feat(notifications): promote an existing per-suite webhook to a reusable channel — [PR #2412](https://github.com/TheurgicDuke771/DataQ/pull/2412) (guided in-app action, no migration) |

Filed from this week's reviews, in later weeks: [#2413](https://github.com/TheurgicDuke771/DataQ/issues/2413) (Week 6), [#2416](https://github.com/TheurgicDuke771/DataQ/issues/2416) (Week 6), [#2417](https://github.com/TheurgicDuke771/DataQ/issues/2417) (Week 7).

### v1.3 Week 2 — operating DataQ & the first outside user (due 2026-10-18)

| Status | Task |
|---|---|
| ⬜ | [#1704](https://github.com/TheurgicDuke771/DataQ/issues/1704) ops: backup/restore + tested upgrade path for DataQ's own database (the BYOL survival story) |
| ⬜ | [#1707](https://github.com/TheurgicDuke771/DataQ/issues/1707) feat(telemetry): opt-in, privacy-respecting product usage telemetry seam (default OFF) |
| ⬜ | [#2387](https://github.com/TheurgicDuke771/DataQ/issues/2387) Ship THIRD-PARTY-NOTICES (or an SPDX SBOM) in the images and GitHub releases |
| ⬜ | [#2388](https://github.com/TheurgicDuke771/DataQ/issues/2388) Portable install artifact: a Helm chart (ADR 0013 Phase 2) |
| ⬜ | [#2253](https://github.com/TheurgicDuke771/DataQ/issues/2253) docs(marketing): a walkthrough of the demo profile on the marketing page (video or annotated screenshots) |
| ⬜ | [#1664](https://github.com/TheurgicDuke771/DataQ/issues/1664) docs(positioning): adopt the 'Data Quality Control Plane' framing on the marketing page + docs site |
| ⬜ | [#2395](https://github.com/TheurgicDuke771/DataQ/issues/2395) Changelog fragments: one file per PR, assembled at release |

### v1.3 Week 3 — datasources, deploy targets & auth (due 2026-10-25)

| Status | Task |
|---|---|
| ⬜ | [#1681](https://github.com/TheurgicDuke771/DataQ/issues/1681) feat(datasource): Google BigQuery adapter — the biggest warehouse omission |
| ⬜ | [#1683](https://github.com/TheurgicDuke771/DataQ/issues/1683) feat(datasource): Google Cloud Storage flat-file adapter — completes the object-store trio |
| ⬜ | [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) Post-v1: AWS + GCP deploy IaC (behind the provider-agnostic seams) |
| ⬜ | [#2309](https://github.com/TheurgicDuke771/DataQ/issues/2309) feat(datasources): keyless auth — managed identity / IAM role / workload identity |
| ⬜ | [#2310](https://github.com/TheurgicDuke771/DataQ/issues/2310) feat(audit): an object-lock tamper anchor (S3 Object Lock / Azure immutable blob) |
| ⬜ | [#717](https://github.com/TheurgicDuke771/DataQ/issues/717) Revisit Iceberg v3 support (deletion vectors, row lineage) behind a capability gate — ADR 0030 |

### v1.3 Week 4 — product surfaces (due 2026-11-01)

| Status | Task |
|---|---|
| ⬜ | [#1687](https://github.com/TheurgicDuke771/DataQ/issues/1687) feat(ui): in-app notification center — bell/feed with read-ack + deep links |
| ⬜ | [#1667](https://github.com/TheurgicDuke771/DataQ/issues/1667) feat(ui): global search / command palette (⌘K) — jump to any suite/check/connection/run by name |
| ⬜ | [#1686](https://github.com/TheurgicDuke771/DataQ/issues/1686) feat(results): run comparison — diff two runs of a suite |
| ⬜ | [#888](https://github.com/TheurgicDuke771/DataQ/issues/888) feat: tagging for suites / assets / connections / checks (workspace labels + list filters) |
| ⬜ | [#1516](https://github.com/TheurgicDuke771/DataQ/issues/1516) feat(ui): Profile / Workspace-Settings IA pickup — replace the labelled placeholders (deferred-by-design) |
| ⬜ | [#244](https://github.com/TheurgicDuke771/DataQ/issues/244) Suite-on-suite triggering (run a suite when another suite completes) |

### v1.3 Week 5 — integrations & governance (due 2026-11-08)

| Status | Task |
|---|---|
| ⬜ | [#1977](https://github.com/TheurgicDuke771/DataQ/issues/1977) feat: consumer-side data contract validation — contract entity + ODCS import/export |
| ⬜ | [#1689](https://github.com/TheurgicDuke771/DataQ/issues/1689) feat(integrations): test-management result publishing — TestRail / Xray / Zephyr behind a result-exporter seam |
| ⬜ | [#1690](https://github.com/TheurgicDuke771/DataQ/issues/1690) feat(catalog): governance-catalog integration — push DQ quality facets + pull classifications (DataHub / OpenMetadata-REST / Collibra seam) |
| ⬜ | [#685](https://github.com/TheurgicDuke771/DataQ/issues/685) feat(connections): purge/redact path for connection version history (immutable snapshots retain edited-out config values) |
| ⬜ | [#1613](https://github.com/TheurgicDuke771/DataQ/issues/1613) RunDetail/RunReport show the check's CURRENT engine/dimension, not as-of-the-result (historical_check_context gap) |
| ⬜ | [#2255](https://github.com/TheurgicDuke771/DataQ/issues/2255) feat(privacy): a reason per entry in redacted_columns (own tag / inherited tag / policy / value classifier / default mask) |

### v1.3 Week 6 — bug fixes & security (due 2026-11-15)

| Status | Task |
|---|---|
| ⬜ | [#2348](https://github.com/TheurgicDuke771/DataQ/issues/2348) Deleting a check silently rewrites the history of every run it took part in |
| ⬜ | [#2383](https://github.com/TheurgicDuke771/DataQ/issues/2383) Suggested rules: a value set saved before a column was classified as PII stays readable |
| ⬜ | [#2240](https://github.com/TheurgicDuke771/DataQ/issues/2240) security: mask an aggregate min/max metric_value on policy-sensitive columns at read time |
| ⬜ | [#2111](https://github.com/TheurgicDuke771/DataQ/issues/2111) fix(dispatch): send_task from the API subscribes to the Redis result backend per run — extra round-trips and a GC re-entrancy deadlock |
| ⬜ | [#2260](https://github.com/TheurgicDuke771/DataQ/issues/2260) deps: oauthlib 3.3.1 carries CVE-2026-49265/49264 (server-side only); databricks-sql-connector pins oauthlib<4.0.0 |
| ⬜ | [#2374](https://github.com/TheurgicDuke771/DataQ/issues/2374) checks: a volume check with only min_rows is refused with 'needs integer min_rows/max_rows' although the value given is an integer |
| ⬜ | [#2243](https://github.com/TheurgicDuke771/DataQ/issues/2243) fix(flatfile): a suite target's file_format is ignored by the run path (profiler-only) |
| ⬜ | [#2133](https://github.com/TheurgicDuke771/DataQ/issues/2133) Redirect guard: an omitted field and its explicit default compare as 'moved' (Trino auth_type/sslmode) |
| ⬜ | [#2068](https://github.com/TheurgicDuke771/DataQ/issues/2068) bug (latent): RunDetail does not refetch when :runId changes on the same mounted instance |
| ⬜ | [#1824](https://github.com/TheurgicDuke771/DataQ/issues/1824) otp: a broker outage on the code-request path burns the caller's per-email quota with no email sent |
| ⬜ | [#1840](https://github.com/TheurgicDuke771/DataQ/issues/1840) Settings → Notifications tab says a workspace-wide default channel is 'a post-v1 follow-up', but workspace channels exist |
| ⬜ | [#2311](https://github.com/TheurgicDuke771/DataQ/issues/2311) security(adf): enforce webhook replay/freshness on ADF alerts (firedDateTime) |
| ⬜ | [#2392](https://github.com/TheurgicDuke771/DataQ/issues/2392) Docs workflow: a release tag's Pages deploy omits the version it just published, and one stuck deploy blocked every publish for four days |
| ⬜ | [#2136](https://github.com/TheurgicDuke771/DataQ/issues/2136) Flaky test: test_revoke_all_for_user_is_attributed_to_the_actor orders audit events by a tied timestamp |
| ⬜ | [#2413](https://github.com/TheurgicDuke771/DataQ/issues/2413) Promote to channel: a legacy ref whose secret is gone becomes a channel that never delivers |
| ⬜ | [#2416](https://github.com/TheurgicDuke771/DataQ/issues/2416) A suite with no runnable check still runs and ends succeeded with zero results (dashboard, gate and coverage read it as clean) |

### v1.3 Week 7 — polish: MCP & API, performance, accessibility (due 2026-11-22)

| Status | Task |
|---|---|
| ⬜ | [#2353](https://github.com/TheurgicDuke771/DataQ/issues/2353) mcp: list_runs returns only the raw triggered_by marker (manual:<user-uuid>), no readable label |
| ⬜ | [#2360](https://github.com/TheurgicDuke771/DataQ/issues/2360) mcp: list_assets / get_asset do not return the asset health score |
| ⬜ | [#2367](https://github.com/TheurgicDuke771/DataQ/issues/2367) mcp: get_suite_performance says 'latest completed run' but scores the latest run only if it completed |
| ⬜ | [#1838](https://github.com/TheurgicDuke771/DataQ/issues/1838) MCP: carry the read-only / state-changing / live-probe classification as ToolAnnotations on each tool; derive GATES and the docs from it |
| ⬜ | [#2356](https://github.com/TheurgicDuke771/DataQ/issues/2356) llm: SQL generation lists every column of the target in its prompt with no cap |
| ⬜ | [#2354](https://github.com/TheurgicDuke771/DataQ/issues/2354) alerting: alerts name the provider 'ADF' while the UI and run labels say 'Azure Data Factory' |
| ⬜ | [#1999](https://github.com/TheurgicDuke771/DataQ/issues/1999) perf(scheduler): dispatch is serial at ~2.4 ms/schedule — 10k due schedules takes 23.6 s of a 60 s beat tick |
| ⬜ | [#2117](https://github.com/TheurgicDuke771/DataQ/issues/2117) perf(scheduler): the remaining per-schedule dispatch cost is one serial broker publish |
| ⬜ | [#2012](https://github.com/TheurgicDuke771/DataQ/issues/2012) perf(dashboard): _suite_performance is a DISTINCT ON over the whole runs table — 82 ms at 1M |
| ⬜ | [#2010](https://github.com/TheurgicDuke771/DataQ/issues/2010) perf(dashboard): the status histogram is still linear at 1M — a materialised per-day rollup is the next step |
| ⬜ | [#1987](https://github.com/TheurgicDuke771/DataQ/issues/1987) Deep-offset paging on /runs, /pipeline_runs and /incidents is still linear — keyset/seek paging is now viable |
| ⬜ | [#1979](https://github.com/TheurgicDuke771/DataQ/issues/1979) Partial/expression indexes silently stop applying under server-side parameter binding (psycopg3) |
| ⬜ | [#1597](https://github.com/TheurgicDuke771/DataQ/issues/1597) audit_chain.verify_chain loads the full hashed audit_events set into memory per call |
| ⬜ | [#2235](https://github.com/TheurgicDuke771/DataQ/issues/2235) perf(profiler): suggest_policy_for_target on S3/ADLS builds the store client and reads the secret twice |
| ⬜ | [#1672](https://github.com/TheurgicDuke771/DataQ/issues/1672) a11y(3/4): keyboard traversal + focus management audit |
| ⬜ | [#1673](https://github.com/TheurgicDuke771/DataQ/issues/1673) a11y(4/4): screen-reader semantics — landmarks, table headers, aria-live run progress, chart text alternatives |
| ⬜ | [#2067](https://github.com/TheurgicDuke771/DataQ/issues/2067) a11y: detail pages are titled generically — name the suite / asset / check in the document title |
| ⬜ | [#2064](https://github.com/TheurgicDuke771/DataQ/issues/2064) ux: in-app navigation never resets scroll — a new page opens at the previous page's scroll offset |
| ⬜ | [#2061](https://github.com/TheurgicDuke771/DataQ/issues/2061) a11y: printing / PDF export from the dark theme inherits dark tokens |
| ⬜ | [#2051](https://github.com/TheurgicDuke771/DataQ/issues/2051) a11y: the ratchet cannot detect under-reporting — a scan of a half-loaded page passes |
| ⬜ | [#2417](https://github.com/TheurgicDuke771/DataQ/issues/2417) mcp: incident, history and snooze tools do not say when the check they describe is switched off |

### v1.3 Week 8 — test & dependency debt, decisions & cycle close (due 2026-11-29)

| Status | Task |
|---|---|
| ⬜ | [#2358](https://github.com/TheurgicDuke771/DataQ/issues/2358) test: test_an_admin_reads_the_log_newest_first fails when backend/tests/services runs before it (audit events leak across tests) |
| ⬜ | [#2135](https://github.com/TheurgicDuke771/DataQ/issues/2135) test_membership_service is not hermetic: WORKSPACE_ADMIN_EMAILS from the repo .env changes its outcome |
| ⬜ | [#2115](https://github.com/TheurgicDuke771/DataQ/issues/2115) test flake: test_the_access_write_cost_does_not_grow_with_the_result_count fails on CI noise (4.36x > 3.0) on a docs-only PR |
| ⬜ | [#2124](https://github.com/TheurgicDuke771/DataQ/issues/2124) ci: run the MySQL / MariaDB live battery against service containers |
| ⬜ | [#2312](https://github.com/TheurgicDuke771/DataQ/issues/2312) test(mssql): a licence-free local stand-in for the SQL Server lane (Babelfish for PostgreSQL) |
| ⬜ | [#1605](https://github.com/TheurgicDuke771/DataQ/issues/1605) test: extend migration-parity's literal-free predicate check beyond IS [NOT] NULL (IN/NOT IN, LIKE/NOT LIKE, IS DISTINCT FROM NULL) |
| ⬜ | [#1610](https://github.com/TheurgicDuke771/DataQ/issues/1610) suites.py: CheckDocument/CheckDocumentIn (+ SuiteDocument/SuiteDocumentIn, SourceConnectionRef/SourceConnectionRefIn) duplicate field declarations with nothing enforcing they stay in sync |
| ⬜ | [#1740](https://github.com/TheurgicDuke771/DataQ/issues/1740) incident_evidence: monitor-kind field names hardcoded independently in 4 places |
| ⬜ | [#2070](https://github.com/TheurgicDuke771/DataQ/issues/2070) chore(frontend): migrate to Vitest 5 — jest-dom matcher types no longer augment vitest's Assertion |
| ⬜ | [#2275](https://github.com/TheurgicDuke771/DataQ/issues/2275) Migrate to SQLAlchemy 2.1 (blocked on snowflake-sqlalchemy; drop the mypy plugin) |
| ⬜ | [#732](https://github.com/TheurgicDuke771/DataQ/issues/732) docs: marketplace-listing readiness checklist — free OSS + BYOL (license audit clear; prerequisites & good-to-haves) |
| ⬜ | [#1708](https://github.com/TheurgicDuke771/DataQ/issues/1708) security: external review — third-party pen test / audit pass before any commercial demo |
| ⬜ | [#2224](https://github.com/TheurgicDuke771/DataQ/issues/2224) Epic: cloud live-verification — re-run once a cloud test instance is procured |

**Exit gate (Week 8):** every item closed or rolled by name with a rationale, zero open PRs,
retro-v1.3 written, this file frozen, `v1.3.0` tagged and released with the client wheel and
the docs pinned.

---

## How to update this file

When merging a PR:

1. Find the task(s) it implements in the relevant week above.
2. Flip `⬜` → `✅` (or `⬜` → `🟡` if partial).
3. Append the PR link: `— [PR #N](https://github.com/.../pull/N)`.
4. Update the **Snapshot** table (open PRs/issues).
5. If the PR closes a carried-over item, strike it through with the closing ref.
6. If the PR added out-of-scope work, add a row with a note (same honesty rule as v1).

PR-template checkbox enforces this. If the change is purely tooling / docs that doesn't map
to a tracked task, tick the "N/A" checkbox.
