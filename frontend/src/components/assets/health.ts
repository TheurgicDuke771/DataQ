import type { AssetSummary, RunOutcome } from '../../api/assets';

/**
 * Asset health derivation (#760) — pure, so it can be unit-tested without rendering antd (kept out
 * of the `.tsx` so the tag component can fast-refresh).
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

/**
 * **Connection health (#803)** — *could DataQ reach and execute against the datasource behind this
 * asset?* Says nothing about whether the data is good.
 */
export function connectionHealth(
  summary: Pick<
    AssetSummary,
    | 'has_operational_error'
    | 'has_skip'
    | 'has_active_run'
    | 'has_cancelled_run'
    | 'checks_total'
    | 'last_run_at'
  >,
): Health {
  if (summary.has_operational_error) return { label: 'Errors', color: 'error' };
  if (summary.has_skip) return { label: 'Degraded', color: 'warning' };
  if (summary.has_active_run) return STATUS_HEALTH.running;
  // Something was evaluated ⇒ we connected. Positive evidence beats a cancelled
  // sibling suite, so this is checked before the cancelled fallback.
  if (summary.checks_total > 0) return { label: 'Reachable', color: 'success' };
  if (summary.has_cancelled_run) return STATUS_HEALTH.cancelled;
  if (summary.last_run_at !== null) return { label: 'Reachable', color: 'success' };
  return { label: 'No runs', color: 'default' };
}

/** **Suite health (#803)** — the ADR 0005 severity-weighted verdict of the suites on this asset. */
export function suiteHealth(
  summary: Pick<
    AssetSummary,
    'worst_severity' | 'checks_total' | 'has_active_run' | 'has_cancelled_run' | 'last_run_at'
  >,
): Health {
  if (summary.worst_severity) return SEVERITY_HEALTH[summary.worst_severity];
  if (summary.has_active_run) return STATUS_HEALTH.running;
  if (summary.checks_total > 0) {
    if (summary.has_cancelled_run) return { label: 'Partial', color: 'warning' };
    return { label: 'Passing', color: 'success' };
  }
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
