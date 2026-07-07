# Best practices

Field-tested guidance for getting signal (not noise) out of DataQ. Everything here is
convention, not enforcement — the platform works without it.

## Start with freshness and volume

Most real data incidents are *"the load didn't happen"* or *"half the rows are
missing"* — not a bad value in row 40,312. On every SQL-backed table that matters, add
a **freshness** monitor (on the load/updated timestamp) and a **volume** monitor
(expected row-count range) before writing any value-level checks. They're cheap, they
catch the incidents that page people, and their `metric_value` history builds the trend
baseline.

## Organize suites around a target, scoped to an environment

- **One suite = one target** (a table or a file batch) on **one connection/env**. A
  suite named `orders — snowflake prod` beats a grab-bag `all my checks`.
- Use suite **export / import** to promote a suite between environments (author on DEV,
  import against the UAT/PROD connection) instead of re-clicking checks.
- Share suites with **view** by default; reserve **edit** for the owning team
  (suite-level sharing is the access model — there are no folder/workspace scopes).

## Severity: make the tiers mean something

Thresholds band the observed **unexpected-%** (higher = worse; ADR 0005/0016):

- **warn** — early signal, tolerable drift. Nobody gets pinged loudly.
- **fail** — actionable breach; the standard alert.
- **critical** — page-worthy; escalates the alert (channel mention).

Anti-pattern: setting `fail` at the first nonzero unexpected-%. Use the **column
profiler** and a **dry-run** to see today's baseline, then set `warn` just above it —
you want the check green on day one and loud only when reality changes. Leave
thresholds blank for a binary pass/fail check.

## Prefer triggers over schedules where an orchestrator exists

A cron schedule guesses when the data is ready; a **trigger binding** knows — checks
run right after the pipeline/DAG succeeds, and results correlate to the pipeline run.
Schedule only what has no orchestrator (flat-file drops), and pick a cadence matched to
the data's arrival rate, not "every 5 minutes to be safe" (dedup will spare you the
repeat alerts, but the warehouse still pays for every run).

## Custom SQL: the escape hatch, not the default

Reach for **Custom SQL** when a rule spans columns or needs a join (`SELECT * FROM
{batch} WHERE returns > orders`). Keep it read-only single-statement (enforced), and
prefer a catalog expectation when one exists — expectations get per-column samples,
profiler support, and cheaper evaluation.

## Alerting hygiene

- Keep the default **warn-and-worse** threshold; drop to **fail-only** for noisy
  exploratory suites rather than disabling alerts.
- **Snooze** a known-broken check during an incident instead of deleting it — the
  history and the re-fire on expiry are the point.
- Dedup means you hear about a breakage **once** (and again on escalation) — if a suite
  feels quiet while red, that's dedup working; the Results page is the ground truth.

## Protect the samples

Failing-row samples are the one place check results can carry PII. The redactor is
column-aware: set the suite's **column policy** (or accept the classifier's
suggestion) so non-sensitive breaches stay debuggable while PII columns stay masked.
Samples are purged by the retention sweep; metric trends survive.

If you **repoint a suite to a different target** after a policy exists, DataQ does
*not* auto-re-derive the policy (it won't clobber your choices) — so re-run
**Auto-detect** on the new target to refresh the identifier / PII columns. A
`suite_policy_possibly_stale` event is logged when this happens.
