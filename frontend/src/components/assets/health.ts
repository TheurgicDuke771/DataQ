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

/** Asset-level health from the summary aggregation (severity + run-state flags). */
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

/** Per-suite health from its latest run (execution status + severity). */
export function runHealth(run: RunOutcome): Health {
  if (run.run_id === null) return { label: 'No runs', color: 'default' };
  if (run.worst_severity) return SEVERITY_HEALTH[run.worst_severity];
  if (run.status === 'failed') return RUN_FAILED_HEALTH;
  const byStatus = run.status !== null ? STATUS_HEALTH[run.status] : undefined;
  if (byStatus) return byStatus;
  return { label: 'Passing', color: 'success' };
}
