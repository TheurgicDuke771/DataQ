import type { AssetSummary, RunOutcome } from '../../api/assets';

/**
 * Asset health derivation (#760) — pure, so it can be unit-tested without
 * rendering antd (kept out of the `.tsx` so the tag component can fast-refresh).
 *
 * Health considers BOTH axes the Results surface distinguishes (`resultsFormat`'s
 * `RUN_STATUS_COLORS` conventions): the run's *execution* status and the checks'
 * *data-quality* severity. Precedence:
 * - a failing check tier present            → that tier (warn < fail < critical)
 * - latest run failed operationally         → "Run failed" (error — never green:
 *   a failed run wrote no results, so severity alone would read passing)
 * - latest run still queued/running         → "Queued"/"Running" (not green yet)
 * - latest run cancelled, nothing evaluated → "Cancelled"
 * - a finished run, all evaluated passed    → "Passing"
 * - nothing has run yet                     → "No runs"
 */
export type Health = { label: string; color: string };

const SEVERITY_HEALTH: Record<'warn' | 'fail' | 'critical', Health> = {
  warn: { label: 'Warning', color: 'warning' },
  fail: { label: 'Failing', color: 'error' },
  critical: { label: 'Critical', color: 'magenta' },
};

// Execution-status healths, colour-matched to resultsFormat.RUN_STATUS_COLORS.
const RUN_FAILED_HEALTH: Health = { label: 'Run failed', color: 'error' };
const STATUS_HEALTH: Record<string, Health> = {
  queued: { label: 'Queued', color: 'default' },
  running: { label: 'Running', color: 'processing' },
  cancelled: { label: 'Cancelled', color: 'warning' },
};

/**
 * Asset-level health from the summary aggregation (severity + run-state flags).
 *
 * The **compact roll-up** of both axes below, for space-constrained rows (the
 * assets tree/table). Where there is room to be precise — the asset detail — render
 * `connectionHealth` and `suiteHealth` side by side instead (#803).
 */
export function assetHealth(
  summary: Pick<
    AssetSummary,
    'worst_severity' | 'last_run_at' | 'has_failed_run' | 'has_active_run'
  >,
): Health {
  if (summary.worst_severity) return SEVERITY_HEALTH[summary.worst_severity];
  if (summary.has_failed_run) return RUN_FAILED_HEALTH;
  if (summary.has_active_run) return STATUS_HEALTH.running;
  if (summary.last_run_at !== null) return { label: 'Passing', color: 'success' };
  return { label: 'No runs', color: 'default' };
}

/**
 * **Connection health (#803)** — *could DataQ reach and execute against the
 * datasource behind this asset?* Says nothing about whether the data is good.
 *
 * Derivation (no polling loop — read straight off the runs already recorded):
 * - a latest run whose execution `failed`, or any check that `error`ed (the
 *   datasource threw) → **Errors**;
 * - any `skip` (a precondition wasn't met — e.g. the batch hasn't landed) →
 *   **Degraded**: it executed, it just found nothing to check;
 * - a run in flight → **Running**; a clean concluded run → **Reachable**;
 * - nothing has ever run → **No runs** (unknown, not healthy).
 */
export function connectionHealth(
  summary: Pick<
    AssetSummary,
    'has_operational_error' | 'has_skip' | 'has_active_run' | 'last_run_at'
  >,
): Health {
  if (summary.has_operational_error) return { label: 'Errors', color: 'error' };
  if (summary.has_skip) return { label: 'Degraded', color: 'warning' };
  if (summary.has_active_run) return STATUS_HEALTH.running;
  if (summary.last_run_at !== null) return { label: 'Reachable', color: 'success' };
  return { label: 'No runs', color: 'default' };
}

/**
 * **Suite health (#803)** — the ADR 0005 severity-weighted verdict of the suites
 * on this asset. Purely about the *data*; operational `error`/`skip` results are
 * excluded (they're `connectionHealth`'s business, per #122).
 *
 * Crucially this keys "Passing" off `checks_total > 0` — the count of *evaluated*
 * checks — not off "a run happened". A run that failed operationally, or one whose
 * checks all skipped, evaluated nothing, so it reports **No data** rather than a
 * green "Passing" it hasn't earned (the bug the old single signal had once the
 * operational flags were pulled out of it).
 */
export function suiteHealth(
  summary: Pick<AssetSummary, 'worst_severity' | 'checks_total' | 'has_active_run' | 'last_run_at'>,
): Health {
  if (summary.worst_severity) return SEVERITY_HEALTH[summary.worst_severity];
  if (summary.has_active_run) return STATUS_HEALTH.running;
  if (summary.checks_total > 0) return { label: 'Passing', color: 'success' };
  if (summary.last_run_at !== null) return { label: 'No data', color: 'default' };
  return { label: 'No runs', color: 'default' };
}

/** Per-suite health from its latest run (execution status + severity). */
export function runHealth(run: RunOutcome): Health {
  if (run.run_id === null) return { label: 'No runs', color: 'default' };
  if (run.worst_severity) return SEVERITY_HEALTH[run.worst_severity];
  if (run.status === 'failed') return RUN_FAILED_HEALTH;
  const byStatus = run.status !== null ? STATUS_HEALTH[run.status] : undefined;
  if (byStatus) return byStatus;
  return { label: 'Passing', color: 'success' };
}
