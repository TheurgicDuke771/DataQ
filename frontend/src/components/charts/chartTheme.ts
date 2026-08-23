import type { ResultStatus, RunStatus } from '../../api/runs';
import { SEVERITY_SCALE } from '../../theme';

/**
 * Chart colour tokens + shared axis/grid styling for the recharts-based dashboard widgets (ADR
 * 0022).
 */

/** Severity → series hex (pass green · warn gold · fail red · critical magenta). */
export const RESULT_STATUS_CHART_COLORS: Record<ResultStatus, string> = {
  pass: SEVERITY_SCALE.good,
  warn: SEVERITY_SCALE.warning,
  fail: SEVERITY_SCALE.bad,
  critical: 'var(--dq-critical)', // magenta-6
  skip: SEVERITY_SCALE.neutral,
  error: 'var(--dq-error)', // volcano-6
};

/** Run execution status → series hex (succeeded green · failed red · running indigo · …). */
export const RUN_STATUS_CHART_COLORS: Record<RunStatus, string> = {
  queued: SEVERITY_SCALE.neutral,
  running: 'var(--dq-primary)', // indigo — matches the brand "in-flight" accent
  succeeded: SEVERITY_SCALE.good,
  failed: SEVERITY_SCALE.bad,
  cancelled: SEVERITY_SCALE.warning,
};

/**
 * Non-status chart tokens — the indigo primary for neutral/aggregate series (e.g. total runs),
 * plus the hairline grid + muted axis tints so every chart frames the same way the cards/tables
 */
export const CHART_COLORS = {
  primary: 'var(--dq-primary)',
  grid: 'var(--dq-border)',
  axis: 'var(--dq-muted)',
} as const;

/** Shared recharts style props so axes/grid/tooltip are consistent across widgets. */
export const AXIS_TICK = { fontSize: 12, fill: CHART_COLORS.axis } as const;
export const GRID_PROPS = { stroke: CHART_COLORS.grid, strokeDasharray: '3 3' } as const;
export const TOOLTIP_STYLE = {
  borderRadius: 8,
  border: `1px solid var(--dq-border)`,
  fontSize: 13,
} as const;

/** Series colour for a result severity. */
export function severityColor(status: ResultStatus): string {
  return RESULT_STATUS_CHART_COLORS[status];
}

/** Series colour for a run execution status. */
export function runStatusColor(status: RunStatus): string {
  return RUN_STATUS_CHART_COLORS[status];
}
