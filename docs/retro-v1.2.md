# DataQ v1.2.0 — retrospective

> Internal document (outside the published site). Written 2026-10-04 at the
> `v1.2.0` close, twelve days before the planned 2026-10-16 end of the eight-week
> cycle (2026-08-22 → 2026-10-04). Companion to [progress-v1.2.md](progress-v1.2.md)
> (the frozen per-PR ledger) and [retro-v1.1.md](retro-v1.1.md) (the retro this one
> builds on).

## What shipped

The cycle's thesis was "DQ intelligence + operability", and both halves landed.

- **Intelligence.** The expectation catalog grew behind a server-side allowlist
  (W2). The `LLMProvider` seam (ADR 0042) carries SQL generation, check
  suggestions and root-cause narratives, all off by default and validator-gated
  (W3–W4). Automated coverage (ADR 0047) creates system-owned suites over the
  asset inventory, proposes rules into a review queue, and reports coverage and
  the false-positive rate on the Dashboard.
- **Engines and datasources.** Per-check engines (ADR 0036): Snowflake DMF and
  Databricks DQX. The engine-generic SQL base (ADR 0045) took PostgreSQL,
  MySQL/MariaDB, Trino, Athena and Redshift; SQL Server, Azure SQL, Synapse and
  Fabric SQL arrived on their own driver decision (ADR 0044); OneLake rides the
  ADLS adapter. Five datasources became eleven.
- **Operability.** Reusable notification channels; the admin area as six routed
  pages with in-app membership (ADR 0043) and guided offboarding; a hash chain
  over the audit log; a pipeline gate a DAG can ask for a verdict (ADR 0046);
  a generated Python client and `dataq` CLI with an API compatibility policy;
  suites as YAML files with validate, drift and apply.
- **Surface quality.** The accessibility pass took the axe baseline from 107
  violations to 0 across every route in both themes. The docs were rebuilt on a
  Diátaxis layout with versioned copies per release. Health scores reached
  assets, connections and dimensions; bulk check actions, a first-run path and
  designed empty states closed the last weeks.
- **MCP.** 46 → 54 tools, with the tool-surface gates declared as data and an
  honesty review on every change to the file.
- **Where it runs.** Our own Azure, AWS, Snowflake and Databricks estate was
  retired on 2026-10-03 after four build-and-verify waves in credential-expiry
  order. The product keeps full cloud support. The prebuilt-image stack became
  production-like: HTTPS from a generated CA, TLS on every internal hop, and
  data and a persistent vault in a directory.

**By the numbers:** 533 commits · 513 issues closed across the eight weekly
milestones plus 223 in the rolling backlog · **6 new ADRs** (0042–0047) · 33
migrations · backend tests 4,180 → **8,536** · frontend 1,012 →
**1,651** · datasources 5 → **11** · MCP tools 46 → **54**.

## What worked (keep doing)

- **Verify, then retire, in expiry order.** The platform-retirement waves built
  and live-verified every cloud-dependent item before the credential behind it
  lapsed (Databricks, then Snowflake, then AWS, then Azure). Nothing was
  shipped "we will check it later" against a platform that no longer exists.
- **A review on every PR, and acting on all of it.** `/code-review` kept
  finding defects that green tests did not: an upgrade guide whose roll-back
  could not work (an older image cannot migrate a newer schema), an export that
  ignored the ordinal the same PR introduced, a "you cannot" message shown
  before the role had loaded. Every finding ended fixed or filed.
- **Special-purpose reviewers for the surfaces that fail in one way.** The MCP
  honesty reviewer and the migration-safety reviewer each caught things a
  general pass does not look for: a suggestion's rationale described as "up to
  a week old" when it is never re-measured; a neighbour docstring that still
  said a figure was not readable after the tool that reads it landed.
- **Mutation-checking regression tests.** Still the cheapest way to learn a test
  proves nothing. In the last week alone it confirmed the new MCP gate, the
  empty-state guards and the check-ordering tests each fail without their fix.
- **Decisions put to the user early and recorded.** Unclassified checks stay in
  the asset score; rollups are workspace-wide; the OTP response stays uniform;
  LLM suggestions stay on demand. Each one unblocked a week of work and is
  written where the code lives (an ADR or the issue).

## What hurt (do differently)

- **The test environment is blind to privileges and commits.** The W2 deploy
  500ed every audited mutation: the audit chain's seal `UPDATE` ran into the
  append-only `REVOKE`, which tests cannot see because they run as a superuser.
  The same blindness hid access events that were never committed. Both now
  have real-database harnesses, but the lesson is general: a rule enforced by
  the database must be tested as the application's own role.
- **No cloud to verify against.** Since 2026-10-03 the "only a live run is
  evidence" rule cannot be met for Snowflake, Unity Catalog, Azure or AWS.
  Work since then is verified locally and says so; #2224 tracks re-running the
  live batteries once a test instance exists. Until then, a driver-boundary
  change to those four is a known risk, not a verified one.
- **One changelog, one insertion point.** Every PR adds its entry at the top of
  the same list, so the second of any two open PRs conflicts, a conflicting PR
  gets no CI, and each needs main merged in by hand. A merge commit also skips
  the pre-commit hooks, which let a type error through once. Week 8 spent more
  time re-merging than reviewing. Fix in v1.3: changelog fragments, one file
  per PR, assembled at release.
- **Tests that pass on insert order.** The test session is one transaction, so
  `now()` ties and rows come back in the order they were written. Two tests
  asserted an order that was luck; one failed in CI the day the ordering key
  changed. Assert order only on a column the test sets.
- **Weekly milestones became buckets.** Week 7 closed 162 issues and Week 6
  closed 93 against plans of about ten rows each. The plan table and the milestone stopped
  describing the same thing, and the Snapshot counts drifted until re-counted
  live. Keep the weekly milestone to its planned rows; unplanned work goes to
  the backlog milestone even when it ships that week.
- **A shared working tree.** Review agents and background waits share one
  checkout. Branch switches, a local API that serves whatever branch is checked
  out, and a new model column without its migration applied each cost time.
  Use a separate worktree for anything that is not the branch in hand.

## Decisions of record

- **The dev and test estate is retired; the product keeps cloud support**
  (2026-09-28, executed 2026-10-03). Cloud hosting stays the recommended
  production path.
- **DQX is never a DataQ dependency.** Each run submits a job to the user's own
  workspace (ADR 0036 amendment).
- **Unity Catalog: pushdown is the default for a new check type**; the frame
  lane needs a recorded reason (2026-08-28).
- **OTP keeps the uniform response** (#1239, ADR 0032).
- **LLM suggestions under automatic coverage stay on demand**; the reconciler
  never calls the model (#1660, ADR 0047 amendment, 2026-10-04).
- **Health scores:** unclassified checks stay in the asset score; the delta's
  "previous" is each suite's latest run as of N days ago; connection and
  dimension rollups are workspace-wide (2026-10-04).
- **The listing-readiness checklist is deferred to v1.3** (#732, 2026-10-04).

## Final verification before the tag

- CI green on `main` at the close commit; zero open PRs.
- All eight weekly milestones closed with 0 open; the Week 8 milestone held
  only the cycle epic (#1518), closed with it.
- Backend 8,536 tests and frontend 1,651 tests green. The last migration
  (`49bd96efd9fc`, `checks.ordinal`) was run up, down and up on a scratch
  database.
- `packages/dataq-client` is at `1.2.0`, matching the tag.
- **Not verified:** anything against a live Snowflake, Unity Catalog, Azure or
  AWS since 2026-10-03 (#2224). The Week 8 browser spec for the first-run panel
  ran in CI only.

## What rolls (explicitly, never silently)

- **76 open issues** carry into v1.3 planning: the `v1.2 Backlog` milestone (74)
  and `v1.3 Backlog` (#732, plus anything filed after this close).
- **#2224** — the cloud live-verification epic, waiting on a test instance.
- **#717** — Iceberg v3, trigger-gated.
- **Security follow-ups filed, not fixed:** #2348 (deleting a check rewrites run
  history), #2383 (a suggested value set outlives a later PII classification),
  #2240 (aggregate min/max on a sensitive column), #2260 (oauthlib advisory,
  server-side only).

The next cycle's inputs are this document, the open backlog, and
[context/roadmap-v1.2-v1.3.md](../context/roadmap-v1.2-v1.3.md) (v1.3:
automation and external evidence).
