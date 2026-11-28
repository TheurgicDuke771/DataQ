# DataQ v1.3.0 — retrospective

> Internal document (outside the published site). Written 2026-11-28 at the
> `v1.3.0` close, one day before the planned 2026-11-29 end of the eight-week
> cycle (2026-10-05 → 2026-11-29). Companion to [progress-v1.3.md](progress-v1.3.md)
> (the frozen per-PR ledger) and [retro-v1.2.md](retro-v1.2.md) (the retro this one
> builds on).

## What shipped

The cycle was scoped as **no net-new features**: build what was already on the
roadmap or filed (Weeks 1–5), then bug fixes and security (Week 6), polish
(Week 7), and debt, decisions and the close (Week 8). One exception was taken on
the maintainer's direction: per-period metrics with yearly seasonality (#2440,
ADR 0048).

- **Coverage and checks (W1).** Zero-config auto-baselines (ADR 0047
  amendment), enable/disable a check without deleting it, profiler-side PII
  detection as a classification source (ADR 0049) and the `pii_drift` monitor
  kind that acts on it.
- **Operating DataQ (W2).** A backup/restore runbook and an upgrade-path gate in
  CI, opt-in usage telemetry that defaults off (ADR 0050), third-party notices
  and an SPDX SBOM in the images and releases, a Helm chart, changelog fragments
  (one file per PR), and the yearly-seasonality work.
- **Datasources (W3).** Google BigQuery and Google Cloud Storage, and an
  object-lock tamper anchor for the audit chain. Eleven datasources became
  thirteen.
- **Product surfaces and integrations (W4–W5).** Suite-on-suite triggers
  (ADR 0051), tags, run comparison, the notification centre, the command palette,
  data contracts on ODCS v3 with import, export and per-clause compliance
  (ADR 0052), a purge path for connection version history, and a reason per
  redacted column. The governance-catalog integration was designed (ADR 0053,
  Proposed) and rolled: no user has named a catalog.
- **Bug fixes and security (W6).** Deleting a check no longer rewrites the
  history of the runs it took part in. Sensitive values are masked on every read
  surface (aggregate min/max, suggested value sets, contract exports, PII-drift
  samples). ADF webhooks enforce freshness. Every SQL engine has a per-query time
  limit.
- **Polish (W7).** MCP tools carry their safety class as annotations and report
  what they can't see. The scheduler and dashboard paths stay flat at a million
  rows, deep paging is keyset, and an index audit covers server-side binding.
  The keyboard and screen-reader audits finished. The docs screenshots and clips
  were re-captured for the v1.3 UI, and a weekly Firefox/WebKit lane was added.
- **Debt and decisions (W8).** Below under "Week 8".

**By the numbers:** 284 squash-merged commits · 148 issues closed across the
eight weekly milestones (W6 36, W7 45, W8 37) · **6 new
ADRs** (0048–0053) · 19 migrations · backend tests 8,536 → **10,734** collected ·
frontend 1,651 → **2,069** · datasources 11 → **13** · MCP tools 54 → **59**.

### Week 8

- **Two order-dependent failures were product bugs.** The results-rollup sweep
  claimed runs with `UPDATE … WHERE id IN (SELECT … LIMIT n FOR UPDATE SKIP
  LOCKED)`. With stale statistics the planner rescanned that subquery per row, and
  each rescan skipped what the statement had already claimed, so one batch could
  take the whole backlog (#2753). Planner tests failed when a unique rival index
  won the toss-up (#2739).
- **CI speed-up.** Backend tests in parallel (pytest-xdist, a database and Redis
  index per worker), the static demo in its own required job (#2789), and the MySQL /
  MariaDB live battery in CI against mirrored LTS images (#2805).
- **Dependencies.** Vitest 5 with jest-dom 8, five stale pnpm overrides dropped (undici's cap
  had held jsdom 31 on undici 7), anthropic 2.x (checked against
  the 2026-10-21 API version: none of the removed parameters are sent),
  fastmcp 4.2, the SQLAlchemy mypy plugin dropped. snowflake-connector 5 is held:
  it moves with snowflake-sqlalchemy 2.x, which changes how VARIANT values come
  back, and needs a live Snowflake run (#2275).
- **Test debt.** The migration tests run through one harness that applies the
  production naming convention. Migration parity catches `NOT IN` / `NOT LIKE` /
  `DISTINCT FROM` flips. A contract test ties the evidence card to what each
  monitor writes, a parity test holds the suite import and export models
  together, the S3 tests ignore the developer's AWS config, and every pnpm
  override records why it exists, with a sweep that names the stale ones.

## What worked (keep doing)

- **Asking why a flake flakes.** Both Week 8 "flakes" that blocked the CI
  speed-up had a cause worth knowing. Capturing the real plan of the failing
  statement (`EXPLAIN` from inside the failing run) turned "leftover rows?" into
  a product fix and a deterministic regression test in an hour.
- **Mutation-checking, every time.** Each regression test this week was run
  against the unfixed code first: the rollup claim (`[6, 0, 0, 0]` before the
  fix), the CAS login test, the migration naming test, the parity negations (39
  predicates fail when `_in_check` flips), the evidence-card contract, the
  override record. One of those caught a bug in the fix itself (the sweep read
  zod's `^3.25.0 || ^4.0.0` as major 3).
- **Reviews on every PR, findings always dispositioned.** The Week 8 reviews
  found three migration tests the first pass missed, nine problems in the
  override sweep (four would have called a security floor removable), and the
  landing page's README twin. Each ended fixed, filed or recorded as benign with
  the reason.
- **Decisions recorded where the work lives.** SQLAlchemy 2.1, Babelfish and the
  five cloud- or person-gated items each got a comment saying what was checked,
  what it waits on and the trigger to pick it up.

## What hurt (do differently)

- **Week 7 closed six days late.** It held 45 issues against a plan of about a
  dozen. Week 6 (36) went the same way. The v1.2 retro said to keep a weekly
  milestone to its planned rows; it didn't happen. Unplanned work that ships in
  a week still belongs in the backlog milestone.
- **CI was the bottleneck at the end.** Backend tests and the E2E job each took
  more than 10 minutes, and with 18 PRs open at once (11 of them Dependabot's)
  the runners queued for most of the afternoon. The parallel backend run cuts
  the first: `pytest` on CI went from 677 s to 447 s, less than the local 5×, because the runner
  has fewer, slower cores and runs under coverage. The E2E job fell below 8 minutes with the
  demo in its own job. The next levers are the perf-budget and mypy steps.
- **Reviews kept catching what the author's tests encoded.** The parity check's first tests
  compared against hand-written "Postgres renderings" that Postgres never produces; the override
  sweep's first version resolved with the old lockfile and called every floor met. Both were
  caught in review, and the fix both times was to ask the real system (a real `CHECK` read back
  with `pg_get_constraintdef`, the installed manifests).
- **A shared test database bites again.** Running tests from a second worktree while a full run
  was going dropped the other run's schema (299 failed, 866 errors), the trap already recorded.
  One test run per database at a time, or the per-worker databases the parallel run now has.
- **A test that passes for the wrong reason.** The first three parallel runs
  "passed" in a worktree that had no `.env`, so 3,598 database tests were
  skipped. Read the skip count, not just the red count.
- **The ledger drifted again.** The Week 8 table still listed seven items that
  closed in Week 7 sessions. Update the tracker when an item closes, not at the
  week's end.
- **Dependabot lockfile PRs conflict serially.** Each frontend bump conflicts in
  `pnpm-lock.yaml` once the one before it merges. Group the frontend dev
  dependencies in `dependabot.yml`, or batch them into one PR.
- **First pushes of new branches failed** about half the time with a bare
  "remote rejected" while the checkpoint branch pushed alongside. A retry always
  worked. Worth a look in the tooling, not the repo.

## Decisions of record

- **SQLAlchemy 2.1 waits on a live Snowflake run** (#2275, 2026-11-28): the mypy
  plugin is gone; the remaining path is snowflake-connector 5 +
  snowflake-sqlalchemy 2.x, and snowflake-connector majors are ignored in
  `dependabot.yml` until then.
- **Babelfish as the SQL Server stand-in** is a self-built image from a pinned
  upstream tag, never a single-maintainer image (#2312).
- **A conformance claim needs a person at the screen reader** (#2520): no
  automated or agent pass counts.
- **The governance catalog waits for a user to name one** (ADR 0053 Proposed,
  #1690).
- **Stay on conda for now; revisit uv in v1.4** (#2523, maintainer, 2026-11-28). The whole tree
  resolves under uv on Linux and macOS, so the open cost is re-homing the requirement chain.
- **croniter 7 waits for an audit of stored schedules** (#2804): it silently changes what some
  valid expressions mean, and DataQ evaluates users' stored cron.
- **The static demo job is a required check** on `main` (maintainer, 2026-11-28), so a PR that
  breaks the published demo can't merge green.

## Final verification before the tag

- CI green on `main` at the close commit; zero open PRs once the close-out merges.
- Weeks 1–7 closed with 0 open; Week 8 closes with the tag.
- Backend 10,734 tests collected (10,556 passed, 178 opt-in live lanes skipped, in the last
  parallel run; CI also runs the MySQL/MariaDB battery now); frontend 2,069 passed.
- `packages/dataq-client` at `1.3.0`, matching the tag.
- **Not verified:** anything against a live Snowflake, Unity Catalog, Azure or
  AWS, BigQuery or GCS since 2026-10-03 (#2224). BigQuery and GCS were
  verified on their emulators only.

## What rolls (explicitly, never silently)

- **32 open issues** in `v1.3 Backlog` carry into v1.4 planning.
- **Waiting on a cloud instance (#2224):** the Snowflake and Unity Catalog live
  lanes, the Azure stack's live plan, #2275, #2575, SQL Server's Entra, Fabric
  and ODBC legs.
- **Waiting on a person or a third party:** #2520 (manual screen-reader pass),
  #1708 (external pen test), #732 (listing decisions or a hosted offer).
- **Filed this week, in the backlog:** #2772 (one keystroke-timer-free `userEvent` setup for
  every form test), #2804 (croniter 7).
- **Decided for v1.4:** #2523 conda → uv (revisit), #2312 Babelfish (self-built image).
