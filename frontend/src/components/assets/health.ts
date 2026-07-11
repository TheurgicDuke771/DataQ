import type { AssetSummary, RunOutcome } from '../../api/assets';

/**
 * Asset health derivation (#760) — pure, so it can be unit-tested without
 * rendering antd (kept out of the `.tsx` so the tag component can fast-refresh).
 *
 * Health rolls up the caller-visible composing suites' latest runs:
 * - a failing tier present  → that tier (warn < fail < critical)
 * - all evaluated passed    → "Passing"
 * - nothing has run yet      → "No runs"
 */
export type Health = { label: string; color: string };

const SEVERITY_HEALTH: Record<'warn' | 'fail' | 'critical', Health> = {
  warn: { label: 'Warning', color: 'warning' },
  fail: { label: 'Failing', color: 'error' },
  critical: { label: 'Critical', color: 'magenta' },
};

/** Derive a health badge from a worst-severity + whether any run exists. */
export function healthOf(
  worstSeverity: 'warn' | 'fail' | 'critical' | null,
  hasRun: boolean,
): Health {
  if (worstSeverity) return SEVERITY_HEALTH[worstSeverity];
  return hasRun ? { label: 'Passing', color: 'success' } : { label: 'No runs', color: 'default' };
}

/** Asset-level health from the summary aggregation. */
export function assetHealth(summary: Pick<AssetSummary, 'worst_severity' | 'last_run_at'>): Health {
  return healthOf(summary.worst_severity, summary.last_run_at !== null);
}

/** Per-suite health from its latest run. */
export function runHealth(run: RunOutcome): Health {
  return healthOf(run.worst_severity, run.run_id !== null);
}
