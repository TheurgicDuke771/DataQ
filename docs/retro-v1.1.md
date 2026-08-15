# DataQ v1.1.0 — retrospective

> Internal document (excluded from the published site). Written 2026-08-15 at the
> `v1.1.0` tag, closing the six-week post-v1 cycle (2026-07-04 → 2026-08-15).
> Companion to [progress.md](progress.md) (the live per-PR tracker, §Cycle plan)
> and [retro-v1.md](retro-v1.md) (the v1 retro this one builds on). The W7
> stretch milestone (backlog burn-down, due 2026-08-22) stays open past the tag
> — see "What rolls" below.

## What shipped

The leap from "GX runner" to DQ platform that the v1 retro forecast: all five
reserved monitor kinds are now real (`comparison` W3 · `schema_drift` W4 ·
`anomaly` W5, joining freshness/volume), classified on the ADR 0038 dimension
axis and rolled into asset scorecards; the G-d assets/lineage/incidents track
end-to-end (asset entity on OpenLineage naming, OL emission + dbt manifests +
warehouse `GET_LINEAGE` pull, incidents with evidence cards, the nav inversion);
scale-aware execution (G-b) — sampling + partition batching + an OOM guardrail
that refuses instead of SIGKILLing, with sampled-ness recorded per result and
surfaced through UI, report, and exports; Iceberg as a fifth datasource (native
`pyiceberg`, ADR 0030) and S3-compatible stores (MinIO/R2/…, #1063); the email
OTP authenticator (ADR 0032, Mailpit-backed local default) and rate limiting
(ADR 0035); the OpenBao secret-store seam (ADR 0039) that ended
plaintext-Redis-by-default and Azure-only production secrets; warehouse
inventory sync (ADR 0040); the history/audit decision (ADR 0041); honest
incremental run progress; and a hardening tail measured in the hundreds
(dependency/CodeQL/supply-chain triages, perf batches, flake kills).

**By the numbers:** 498 commits · ~434 issues/PRs closed across the six weekly
milestones + 253 in the rolling Backlog milestone · **14 new ADRs** (0028–0041)
· backend tests 1,289 → **4,180** (97.25%, gate ≥80%) · frontend 337 → **1,012**
· issue/PR numbers #571 → #1335 consumed in six weeks.

## What worked (keep doing)

- **The #953 rule — "for anything crossing a driver boundary, only a live run is
  evidence" — earned its name over and over.** This cycle alone: UC freshness
  had *never* worked (driver returns MAX-of-TIMESTAMP as `str`); the profiler's
  obvious `UNION ALL` batching compiled cleanly and was refused by a live
  engine; `TABLESAMPLE (6e-05 PERCENT)` — Python float repr — was a live parse
  error on exactly the huge-table case the feature targets; two stacked crashes
  hid in one Snowflake SQL literal. Every one was invisible to a green,
  well-covered suite, and every one fell out of a live pre-merge run. W6 made
  live verification a **merge gate** for driver-boundary PRs, not a post-merge
  chore — keep it there.
- **Mandated multi-angle review before merge, with mutation-checked fixes.**
  The W6 close batch alone surfaced ~40 real findings pre-merge, including two
  **pre-existing production bugs** found by reviewing adjacent code (the
  profiler `freq` alias capture; the API silently dropping a suite's `sampling`
  block — the feature under review was unreachable and its own tests passed).
  The review fan-outs also repeatedly caught the reviewer-proof failure mode:
  regression tests that pass against the unfixed code (three rewritten in W6
  after mutation checks).
- **Fixed-or-filed, no pre-existing excuse.** Every review finding in the cycle
  ended as an in-PR fix or a numbered issue; "verified-benign" got recorded with
  evidence. The follow-up graph (#1318–#1320, #1326–#1331, #1334…) is the
  planning input for the next cycle, for free.
- **Subscription-driven sequencing.** Front-loading everything that needed the
  expiring Snowflake/Azure subscriptions (W1–W3), then running cloud-independent
  (W4–W6), meant zero deadline-forced compromises when the wind-down windows
  arrived.
- **Seams keep absorbing change.** ADR 0039 dropped a fourth SecretStore behind
  the Week-2 seam untouched; the monitor-kind discriminator took three new kinds
  without a schema rewrite; ADR 0036's connection-anchored engines and ADR
  0041's audit log both slot behind existing seams.

## What hurt (do differently)

- **The orchestration that made the cycle fast nearly ended it.** Parallel
  implementation agents + 4–8-finder review fan-outs delivered the W5 and W6
  batches in single sessions — and blew through the monthly spend limit **four
  times** in the close-out session, each kill leaving an agent mid-edit and
  costing a recovery pass. Standing directive recorded (memory +
  this document): work **inline and sequentially**, smallest-fan-out reviews,
  one at a time, until the budget posture changes. Scale of orchestration is now
  a resource decision, not a default.
- **A CONFLICTING PR gets zero CI, silently.** When `main` moved under PR #1325,
  GitHub stopped building the test-merge commit — so no `pull_request` events,
  no Actions runs, presenting as "CI stopped scheduling" and costing a day of
  misdiagnosis (delivery incident? Actions outage?). The fix is trivial (merge
  main into the branch); the *diagnosis* is the lesson: `mergeable:
  CONFLICTING` is the first thing to check when runs vanish.
- **Tracker rows rot when closes happen elsewhere.** The W6 table sat at 0/14
  while 8 of its rows had been closed by earlier batches under other headings —
  the same point-in-time-counts lesson as v1, now on checkbox state. The
  merge-hook nudge helps only if the row exists where the close happens; a
  weekly reconcile against `gh issue list` (not memory) is the durable habit.
- **Per-phase commits are a systemic contract change, not a local one.** #1332's
  review found the cycle's sharpest architecture lesson: making result rows
  visible mid-run silently broke three consumers that relied on
  "only terminal succeeded runs have rows" (rollups, alert dedup, baseline
  durability). The fix — a shared `AGGREGATABLE_RUN_STATUSES` read-side filter —
  is the pattern: when a write-side invariant weakens, make the read side own
  the rule, in one place.

## Decisions of record (2026-08-15)

- **Sequential, no-agents working mode** until the credit posture changes
  (user directive; supersedes the batch-3-4 guidance).
- **Iceberg scan cap deferred to its own measurement** (#1328): inheriting
  `RUN_MAX_SCAN_ROWS` would refuse a rung Iceberg is measured to survive.
- **Per-check GX increments declined by design** (#318/#1332): splitting the
  atomic batch would re-read the dataset per check; the honest fallback
  (heartbeat + server elapsed) is the deliverable.
- **Check ordering ordinal deferred** (#1334): `CHECK_ORDER` is honest where
  authoring times differ; same-transaction imports tie and need a real ordinal.

## Final verification before the tag

- All six W6 issues closed with CI green on every merge; the two
  driver-boundary PRs (#1323, #1325) live-verified **pre-merge** against real
  Snowflake + Unity Catalog (equivalence, type fidelity, TABLESAMPLE/
  REPEATABLE semantics, no 25-way-join planner cliff).
- Zero open PRs at the tag; the W6 milestone holds only the cycle epic (#597),
  closed with it.
- Backend 4,180 tests / 97.25% and frontend 1,012 tests green on `main`;
  both migrations in the release (`fbf4fe92e295`, `4d23b47ee564`) are additive
  and tested up/down/up.

## What rolls (explicitly, never silently)

The **W7 stretch milestone** (due 2026-08-22) stays open past this tag with its
11 issues: the compliance track G1–G5 (#431–#435), MCP tier-1 expansion (#529),
and the remaining backlog burn-down candidates. Whatever remains at its close
rolls to the next cycle's planning **by name**, per the standing rule. The next
cycle's inputs are this document, the open follow-up graph above, and
[context/post-v1-roadmap.md](../context/post-v1-roadmap.md).
