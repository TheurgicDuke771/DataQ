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
| **Open issues** | **93** open repo-wide (2026-10-25, live re-count): Week 4 0, Week 5 5, Week 6 24, Week 7 28, Week 8 27, `v1.3 Backlog` 9 (#1689 rolled 2026-10-25); every open issue has a milestone. Weeks 1–4 are closed; Week 4 shipped all six items (#1667, #1686 on 2026-10-24; #1516/#1840, #1687, #888, #244 on 2026-10-25) and its milestone is closed. Week 5 (due 2026-11-08) starts with #1977 ODCS. |
| **Open PRs** | **0 open** (2026-10-25) apart from the one carrying this update. |

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
| ✅ | [#1661](https://github.com/TheurgicDuke771/DataQ/issues/1661) feat(monitors): zero-config auto-baselines — unknown-unknown detection without an authored check — ADR 0047 already covers all but sensitivity config above the check level. Decided 2026-10-13: automatic checks inherit at run time (asset override → workspace setting → built-in default); ADR 0047 amended. Shipped: ADR amendment ([#2422](https://github.com/TheurgicDuke771/DataQ/pull/2422)), migration `543b75689c8c` ([#2423](https://github.com/TheurgicDuke771/DataQ/pull/2423)), settings API ([#2425](https://github.com/TheurgicDuke771/DataQ/pull/2425)), run-time inheritance ([#2426](https://github.com/TheurgicDuke771/DataQ/pull/2426)), admin and asset UI ([#2427](https://github.com/TheurgicDuke771/DataQ/pull/2427)), false-positive breakdown by automatic check and by connection. Filed: [#2424](https://github.com/TheurgicDuke771/DataQ/issues/2424) |
| ✅ | [#2369](https://github.com/TheurgicDuke771/DataQ/issues/2369) feat(checks): enable/disable a check without deleting it (single and bulk) — migration [PR #2414](https://github.com/TheurgicDuke771/DataQ/pull/2414), code [PR #2415](https://github.com/TheurgicDuke771/DataQ/pull/2415) |
| ✅ | [#1674](https://github.com/TheurgicDuke771/DataQ/issues/1674) feat(privacy): profiler-side PII auto-detection as an additional classification source — researched 2026-10-07; the five design decisions were taken 2026-10-20 (normalised `asset_column_classifications` table, email + encoded detectors only, additive-only `detected` rung below warehouse tags and the suite policy, `identifier_column` clears it, Admin-only audited dismissal, no lineage propagation) and recorded in ADR 0049. Shipped 2026-10-20: migration `c7d2e9a41f05`, the write path on every own-target profile (REST, MCP, suggestion refresh, LLM context, worker auto-classify), the overlay in `effective_column_tags`, the asset detail + `PATCH /assets/{id}/classifications`, MCP `get_asset` fields, the **Detected personal data** panel; the nine `/code-review` findings (partial-profile stamp, effect honesty, the two policy-suggest doors, savepoint + upsert on the write, `detected` kept for other-table probes, column in the body, one tag lookup, detector as a label) fixed in-PR; two CI rounds (a CodeQL import cycle → leaf `classification_vocab` module; an order-dependent audit assertion). Merged as [PR #2470](https://github.com/TheurgicDuke771/DataQ/pull/2470). Filed: [#2471](https://github.com/TheurgicDuke771/DataQ/issues/2471) (pre-existing frontend test at the vitest timeout) |
| ✅ | [#1675](https://github.com/TheurgicDuke771/DataQ/issues/1675) feat(monitors): pii_drift monitor kind — PII-looking data appearing in an unclassified column — decided 2026-10-20 (profile at run time; baseline-and-skip on run 1, a fixed baseline that moves only on re-baseline — the roll-forward first built was reverted after review because it auto-resolved the incident one run later; fail by default at 1 new column) and built on `claude/sharp-ramanujan-fy5cej`: migration `d4e8f1a27b63` widening both `kind_valid` constraints, the stateful executor over the profiler + classification source, author-time gate (every profilable type), incident `kind_detail`, dry-run, re-baseline, catalog entry under a new **Privacy** category, docs and MCP docstrings — [PR #2478](https://github.com/TheurgicDuke771/DataQ/pull/2478), merged 2026-10-20 after four `/code-review` rounds (31 findings fixed in-PR; #2475/#2476/#2477 filed) |
| ✅ | [#1762](https://github.com/TheurgicDuke771/DataQ/issues/1762) feat(notifications): promote an existing per-suite webhook to a reusable channel — [PR #2412](https://github.com/TheurgicDuke771/DataQ/pull/2412) (guided in-app action, no migration) |

Filed from this week's reviews, in later weeks: [#2413](https://github.com/TheurgicDuke771/DataQ/issues/2413) (Week 6), [#2416](https://github.com/TheurgicDuke771/DataQ/issues/2416) (Week 6), [#2417](https://github.com/TheurgicDuke771/DataQ/issues/2417) (Week 7).

### v1.3 Week 2 — operating DataQ & the first outside user (due 2026-10-18)

| Status | Task |
|---|---|
| ✅ | [#1704](https://github.com/TheurgicDuke771/DataQ/issues/1704) ops: backup/restore + tested upgrade path for DataQ's own database (the BYOL survival story) — [PR #2459](https://github.com/TheurgicDuke771/DataQ/pull/2459): runbook `operate/backup-restore-upgrade.md`, `backend/scripts/upgrade_path.py` upgrade-path gate in `Backend — tests` (v1.2.0 → head → v1.2.0 rehearsed; image-only rollback is not viable, `alembic downgrade` with the new tree is the doctrine), Rollback line per release (CONTRIBUTING rule 31a) |
| ✅ | [#1707](https://github.com/TheurgicDuke771/DataQ/issues/1707) feat(telemetry): opt-in, privacy-respecting product usage telemetry seam (default OFF) — ADR 0050: OpenTelemetry signals (`dataq.usage.events` counter + `dataq.usage.<event>` log events) through the existing exporter seam, a file sink for no-collector deployments; two gates (`TELEMETRY_SINK` + the audited admin opt-in); twelve-event closed vocabulary enforced in code. Migration [PR #2472](https://github.com/TheurgicDuke771/DataQ/pull/2472) (`b9ddde2123fe`), seam PR in review — [PR #2472](https://github.com/TheurgicDuke771/DataQ/pull/2472) migration, [PR #2486](https://github.com/TheurgicDuke771/DataQ/pull/2486) seam (ADR 0050) |
| ✅ | [#2387](https://github.com/TheurgicDuke771/DataQ/issues/2387) Ship THIRD-PARTY-NOTICES (or an SPDX SBOM) in the images and GitHub releases — [PR #2461](https://github.com/TheurgicDuke771/DataQ/pull/2461): `scripts/check-licenses.py` gate in CI + pre-commit, `THIRD-PARTY-NOTICES.md` in both images (frontend serves `/third-party-notices`), SPDX SBOM + notices attached to the GitHub Release by a read-only tag-time job; one override (`caio`), zero violations |
| ✅ | [#2388](https://github.com/TheurgicDuke771/DataQ/issues/2388) Portable install artifact: a Helm chart (ADR 0013 Phase 2) — [PR #2505](https://github.com/TheurgicDuke771/DataQ/pull/2505): `deploy/helm/dataq/` (migrate as a pre-install/pre-upgrade hook, no credential in values, non-root read-only pods), rendered and kubeconform-checked in CI; kind smoke is a documented manual step |
| ✅ | [#2253](https://github.com/TheurgicDuke771/DataQ/issues/2253) docs(marketing): a walkthrough of the demo profile on the marketing page (video or annotated screenshots) — [PR #2501](https://github.com/TheurgicDuke771/DataQ/pull/2501): five annotated screenshots of the demo profile on the marketing page; screenshots 3 and 4 need recapturing after [#2499](https://github.com/TheurgicDuke771/DataQ/issues/2499) and [#2502](https://github.com/TheurgicDuke771/DataQ/issues/2502) |
| ✅ | [#1664](https://github.com/TheurgicDuke771/DataQ/issues/1664) docs(positioning): adopt the 'Data Quality Control Plane' framing on the marketing page + docs site — [PR #2497](https://github.com/TheurgicDuke771/DataQ/pull/2497): marketing page, docs landing, features tour and README; counts checked against the code |
| ✅ | [#2395](https://github.com/TheurgicDuke771/DataQ/issues/2395) Changelog fragments: one file per PR, assembled at release — [PR #2460](https://github.com/TheurgicDuke771/DataQ/pull/2460): `changelog.d/<n>.<section>.md`, rendered into the docs page by a mkdocs hook, assembled by `scripts/changelog.py` at release, CI requires a fragment (or the `no-changelog` label) on user-facing PRs; CONTRIBUTING rule 43 |
| ✅ | [#2440](https://github.com/TheurgicDuke771/DataQ/issues/2440) feat(monitors): per-period metrics with yearly seasonality and seasonal periods on the anomaly monitor — **unplanned, net-new, taken on the maintainer's direction 2026-10-13** in a cycle scoped as no net-new features. ADR 0048 ([#2441](https://github.com/TheurgicDuke771/DataQ/pull/2441)). Shipped: per-period metrics ([#2442](https://github.com/TheurgicDuke771/DataQ/pull/2442)), history seeded from the table ([#2444](https://github.com/TheurgicDuke771/DataQ/pull/2444)), yearly seasonality in scoring for the per-period metrics ([#2445](https://github.com/TheurgicDuke771/DataQ/pull/2445)), the declared-calendar schema `seasonal_periods` + `check_seasonal_periods` ([#2447](https://github.com/TheurgicDuke771/DataQ/pull/2447), migration `b94674780cb8`). the calendar service, admin API (`/admin/seasonal-periods`), attachments and declared-period scoring ([#2469](https://github.com/TheurgicDuke771/DataQ/pull/2469)). detected periods, the promote action and their REST/MCP reads ([#2487](https://github.com/TheurgicDuke771/DataQ/pull/2487)). Yearly on the whole-table metrics and in automatic coverage ([#2491](https://github.com/TheurgicDuke771/DataQ/pull/2491)). The admin calendar, editor fields, trend expected value and run-detail explanation ([#2492](https://github.com/TheurgicDuke771/DataQ/pull/2492)). Closed 2026-10-21; follow-ups [#2490](https://github.com/TheurgicDuke771/DataQ/issues/2490), [#2498](https://github.com/TheurgicDuke771/DataQ/issues/2498) in `v1.3 Backlog`. Filed: [#2443](https://github.com/TheurgicDuke771/DataQ/issues/2443) |

### v1.3 Week 3 — datasources, deploy targets & auth (due 2026-10-25)

| Status | Task |
|---|---|
| ✅ | [#1681](https://github.com/TheurgicDuke771/DataQ/issues/1681) feat(datasource): Google BigQuery adapter — the biggest warehouse omission — [PR #2489](https://github.com/TheurgicDuke771/DataQ/pull/2489) type-set migration, [PR #2493](https://github.com/TheurgicDuke771/DataQ/pull/2493) adapter on the engine-generic SQL base (`sqlalchemy-bigquery`, per-query `maximum_bytes_billed` cap); live-verified on the BigQuery emulator, real BigQuery unverified |
| ✅ | [#1683](https://github.com/TheurgicDuke771/DataQ/issues/1683) feat(datasource): Google Cloud Storage flat-file adapter — completes the object-store trio — [PR #2484](https://github.com/TheurgicDuke771/DataQ/pull/2484): `gcs` type on the shared flat-file runner via `google-cloud-storage` (gcsfs rejected: its gRPC probe stalls ~60 s per bucket off Google), service-account key or anonymous, optional endpoint; live-verified on fake-gcs-server, real GCS unverified |
| ➡️ | [#505](https://github.com/TheurgicDuke771/DataQ/issues/505) Post-v1: AWS + GCP deploy IaC (behind the provider-agnostic seams) — moved to `v1.3 Backlog` 2026-10-21 on the maintainer's decision: needs a live cloud (#2224) |
| ➡️ | [#2309](https://github.com/TheurgicDuke771/DataQ/issues/2309) feat(datasources): keyless auth — managed identity / IAM role / workload identity — moved to `v1.3 Backlog` 2026-10-21 on the maintainer's decision: needs a live cloud (#2224) |
| ✅ | [#2310](https://github.com/TheurgicDuke771/DataQ/issues/2310) feat(audit): an object-lock tamper anchor (S3 Object Lock / Azure immutable blob) — [PR #2480](https://github.com/TheurgicDuke771/DataQ/pull/2480) |
| ➡️ | [#717](https://github.com/TheurgicDuke771/DataQ/issues/717) Revisit Iceberg v3 support (deletion vectors, row lineage) behind a capability gate — ADR 0030 — moved to `v1.3 Backlog` 2026-10-21 on the maintainer's decision: pick-up gate not met (pyiceberg v3 deletion-vector reads) |

Week 3 also carried, unplanned and on the maintainer's direction 2026-10-21: CONTRIBUTING rules 44–45 and their guard tests ([#2512](https://github.com/TheurgicDuke771/DataQ/pull/2512)), the OpenAPI contract gate with the comparison-report fix ([#2515](https://github.com/TheurgicDuke771/DataQ/pull/2515), #2514), the rule-44 audit of v1.3's UI ([#2517](https://github.com/TheurgicDuke771/DataQ/pull/2517), #2516), the CodeQL import-cycle and dead-alias clean-up ([#2510](https://github.com/TheurgicDuke771/DataQ/pull/2510), [#2513](https://github.com/TheurgicDuke771/DataQ/pull/2513)), the dependency-audit fix ([#2508](https://github.com/TheurgicDuke771/DataQ/pull/2508), #2506) and the accessibility wording on the marketing page ([#2521](https://github.com/TheurgicDuke771/DataQ/pull/2521); conformance report filed as #2520).

### v1.3 Week 4 — product surfaces (due 2026-11-01) — CLOSED 2026-10-25, 6/6 shipped, one week early

| Status | Task |
|---|---|
| ✅ | [#1687](https://github.com/TheurgicDuke771/DataQ/issues/1687) feat(ui): in-app notification center — bell/feed with read-ack + deep links — [PR #2538](https://github.com/TheurgicDuke771/DataQ/pull/2538), merged 2026-10-25 (`notifications` table + migration `c38847820d74`, in-app `ResultPublisher`, `/notifications` API, header bell + drawer, `NOTIFICATIONS_RETENTION_DAYS` purge; review also fixed the "all N checks passed" overclaim on errored/skipped checks across feed, Teams, Slack and email) |
| ✅ | [#1667](https://github.com/TheurgicDuke771/DataQ/issues/1667) feat(ui): global search / command palette (⌘K) — jump to any suite/check/connection/run by name — [PR #2525](https://github.com/TheurgicDuke771/DataQ/pull/2525), merged 2026-10-24 |
| ✅ | [#1686](https://github.com/TheurgicDuke771/DataQ/issues/1686) feat(results): run comparison — diff two runs of a suite — [PR #2527](https://github.com/TheurgicDuke771/DataQ/pull/2527) (`GET /runs/{id}/compare/{other}`, the compare page + "Compare with…" control; each side as the check was at that run via `check_versions`) |
| ✅ | [#888](https://github.com/TheurgicDuke771/DataQ/issues/888) feat: tagging for suites / assets / connections / checks (workspace labels + list filters) — [PR #2539](https://github.com/TheurgicDuke771/DataQ/pull/2539), merged 2026-10-25 (`tags` + four join tables, migration `e27cb7be87cd` chained after `c38847820d74`; `tag=` AND filters on `/suites` and `/assets`; `GET /tags` / `DELETE /tags/{id}`; chips, pickers and filters in the UI; a tagged asset pins against the orphan sweep; MCP reads and updates carry tags) |
| ✅ | [#1516](https://github.com/TheurgicDuke771/DataQ/issues/1516) feat(ui): Profile / Workspace-Settings IA pickup — replace the labelled placeholders — [PR #2535](https://github.com/TheurgicDuke771/DataQ/pull/2535), merged 2026-10-25 (also closes #1840; `GET /admin/workspace-config` read-only retention + sign-in readout with `sign_in_mode` / `idp_decides_signup`; Profile access summary; follow-up [#2537](https://github.com/TheurgicDuke771/DataQ/issues/2537) filed, Week 7) |
| ✅ | [#244](https://github.com/TheurgicDuke771/DataQ/issues/244) Suite-on-suite triggering (run a suite when another suite completes) — [PR #2541](https://github.com/TheurgicDuke771/DataQ/pull/2541), merged 2026-10-25. **ADR 0051 (Accepted 2026-10-25):** `provider='suite'` in the existing `trigger_bindings` (a trigger provider, never an orchestration provider), fired from the worker after the alert hooks when the upstream run succeeded with no check at fail/critical; self-binding and loops refused at authoring under an advisory lock, chain depth capped at 5, `view` on the upstream required; migration `7a1c5d9e2b44` rebuilds the dedup index concurrently. The review's nine findings were fixed in the PR; the OTP revoked-session E2E spec was made robust to a background 401 (the header bell's poll) |

### v1.3 Week 5 — integrations & governance (due 2026-11-08)

| Status | Task |
|---|---|
| 🟡 | [#1977](https://github.com/TheurgicDuke771/DataQ/issues/1977) feat: consumer-side data contract validation — contract entity + ODCS import/export — **ADR 0052** ([PR #2543](https://github.com/TheurgicDuke771/DataQ/pull/2543), Proposed); build in three phases: backend core ([PR #2550](https://github.com/TheurgicDuke771/DataQ/pull/2550): migration `fb29a430991e`, translation table, import/reconcile/export, REST), then rollup + traceability + MCP reads + manual linking, then the read-only UI |
| ➡️ | [#1689](https://github.com/TheurgicDuke771/DataQ/issues/1689) feat(integrations): test-management result publishing — TestRail / Xray / Zephyr behind a result-exporter seam — moved to `v1.3 Backlog` 2026-10-25 on the maintainer's decision: the issue's own pick-up gate (a concrete prospect naming a tool) is not met |
| ⬜ | [#1690](https://github.com/TheurgicDuke771/DataQ/issues/1690) feat(catalog): governance-catalog integration — push DQ quality facets + pull classifications (DataHub / OpenMetadata-REST / Collibra seam) |
| ✅ | [#685](https://github.com/TheurgicDuke771/DataQ/issues/685) feat(connections): purge/redact path for connection version history (immutable snapshots retain edited-out config values) | — [PR #2546](https://github.com/TheurgicDuke771/DataQ/pull/2546): config emptied, `redacted_at`/`redacted_by` columns (migration `878cff0aebbe`); connections workspace-admin, checks suite owner/admin; redacted check versions cannot be restored; ADR 0020 amended
| ✅ | [#1613](https://github.com/TheurgicDuke771/DataQ/issues/1613) RunDetail/RunReport show the check's CURRENT engine/dimension, not as-of-the-result (historical_check_context gap) — [PR #2544](https://github.com/TheurgicDuke771/DataQ/pull/2544) |
| ✅ | [#2255](https://github.com/TheurgicDuke771/DataQ/issues/2255) feat(privacy): a reason per entry in redacted_columns (own tag / inherited tag / policy / value classifier / default mask) | — [PR #2548](https://github.com/TheurgicDuke771/DataQ/pull/2548), merged 2026-10-25: `redaction_reasons` beside `redacted_columns` on REST, MCP, alerts and the run page; masking unchanged (rungs pinned first)

### v1.3 Week 6 — bug fixes & security (due 2026-11-15)

| Status | Task |
|---|---|
| ⬜ | [#2348](https://github.com/TheurgicDuke771/DataQ/issues/2348) Deleting a check silently rewrites the history of every run it took part in |
| ⬜ | [#2383](https://github.com/TheurgicDuke771/DataQ/issues/2383) Suggested rules: a value set saved before a column was classified as PII stays readable |
| ⬜ | [#2240](https://github.com/TheurgicDuke771/DataQ/issues/2240) security: mask an aggregate min/max metric_value on policy-sensitive columns at read time |
| ⬜ | [#2111](https://github.com/TheurgicDuke771/DataQ/issues/2111) fix(dispatch): send_task from the API subscribes to the Redis result backend per run — extra round-trips and a GC re-entrancy deadlock |
| ⬜ | [#2260](https://github.com/TheurgicDuke771/DataQ/issues/2260) deps: oauthlib 3.3.1 carries CVE-2026-49265/49264 (server-side only); databricks-sql-connector pins oauthlib<4.0.0 |
| ⬜ | [#2530](https://github.com/TheurgicDuke771/DataQ/issues/2530) deps: filelock 3.32.x carries CVE-2026-112940/112913/112907 (ReadWriteLock + Windows-only paths, unused); snowflake-connector-python 4.x caps filelock<4 — ignored in CI until the connector 5.x bump |
| ✅ | [#2531](https://github.com/TheurgicDuke771/DataQ/issues/2531) deps: fastmcp / black / dompurify / zod advisories failed the audit gate on every open PR — filed and fixed 2026-10-24, [PR #2532](https://github.com/TheurgicDuke771/DataQ/pull/2532) |
| ✅ | [#2374](https://github.com/TheurgicDuke771/DataQ/issues/2374) checks: a volume check with only min_rows is refused with 'needs integer min_rows/max_rows' although the value given is an integer | — fixed in [PR #2550](https://github.com/TheurgicDuke771/DataQ/pull/2550): a floor, a ceiling or both; each refusal names the key
| ⬜ | [#2243](https://github.com/TheurgicDuke771/DataQ/issues/2243) fix(flatfile): a suite target's file_format is ignored by the run path (profiler-only) |
| ⬜ | [#2133](https://github.com/TheurgicDuke771/DataQ/issues/2133) Redirect guard: an omitted field and its explicit default compare as 'moved' (Trino auth_type/sslmode) |
| ⬜ | [#2068](https://github.com/TheurgicDuke771/DataQ/issues/2068) bug (latent): RunDetail does not refetch when :runId changes on the same mounted instance |
| ⬜ | [#1824](https://github.com/TheurgicDuke771/DataQ/issues/1824) otp: a broker outage on the code-request path burns the caller's per-email quota with no email sent |
| ✅ | [#1840](https://github.com/TheurgicDuke771/DataQ/issues/1840) Settings → Notifications tab says a workspace-wide default channel is 'a post-v1 follow-up', but workspace channels exist — fixed with #1516 in [PR #2535](https://github.com/TheurgicDuke771/DataQ/pull/2535) |
| ⬜ | [#2311](https://github.com/TheurgicDuke771/DataQ/issues/2311) security(adf): enforce webhook replay/freshness on ADF alerts (firedDateTime) |
| ⬜ | [#2392](https://github.com/TheurgicDuke771/DataQ/issues/2392) Docs workflow: a release tag's Pages deploy omits the version it just published, and one stuck deploy blocked every publish for four days |
| ⬜ | [#2136](https://github.com/TheurgicDuke771/DataQ/issues/2136) Flaky test: test_revoke_all_for_user_is_attributed_to_the_actor orders audit events by a tied timestamp |
| ⬜ | [#2413](https://github.com/TheurgicDuke771/DataQ/issues/2413) Promote to channel: a legacy ref whose secret is gone becomes a channel that never delivers |
| ⬜ | [#2416](https://github.com/TheurgicDuke771/DataQ/issues/2416) A suite with no runnable check still runs and ends succeeded with zero results (dashboard, gate and coverage read it as clean) |
| ✅ | [#2506](https://github.com/TheurgicDuke771/DataQ/issues/2506) deps: certifi PYSEC-2026-4320 and follow-redirects GHSA-q3jh-wrc6-jgq8/GHSA-8r9p-f939-6h3c failed the audit gate on main — filed and fixed 2026-10-21, [PR #2508](https://github.com/TheurgicDuke771/DataQ/pull/2508) |
| ✅ | [#2514](https://github.com/TheurgicDuke771/DataQ/issues/2514) OpenAPI: the comparison-report download was declared as JSON, so the generated client crashed on it — [PR #2515](https://github.com/TheurgicDuke771/DataQ/pull/2515), which also adds the OpenAPI contract gate |
| ✅ | [#2516](https://github.com/TheurgicDuke771/DataQ/issues/2516) v1.3 UI surfaces against CONTRIBUTING rule 44 (focus return, announcements, accessible names) — [PR #2517](https://github.com/TheurgicDuke771/DataQ/pull/2517) |
| ⬜ | [#2476](https://github.com/TheurgicDuke771/DataQ/issues/2476) privacy: detections a pii_drift run records do not mask that same run's failing samples — filed from the #2478 review, milestoned 2026-10-21 |

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
| ✅ | [#2494](https://github.com/TheurgicDuke771/DataQ/issues/2494) e2e-demo capture flake: each capture route bounded, no global network-idle wait — [PR #2524](https://github.com/TheurgicDuke771/DataQ/pull/2524), merged 2026-10-24 |
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
| ⬜ | [#2520](https://github.com/TheurgicDuke771/DataQ/issues/2520) a11y: publish a WCAG 2.1 AA conformance report (ACR/VPAT) once the Week-7 audits close — filed 2026-10-21 |

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
