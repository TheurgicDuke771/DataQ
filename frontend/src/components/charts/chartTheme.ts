import type { ResultStatus, RunStatus } from '../../api/runs';
import { BRAND } from '../../theme';

/**
 * Chart colour tokens + shared axis/grid styling for the recharts-based dashboard widgets (ADR
 * 0022).
 */

/** Severity → series hex (pass green · warn gold · fail red · critical magenta). */
export const RESULT_STATUS_CHART_COLORS: Record<ResultStatus, string> = {
  pass: '#52c41a', // green-6
  warn: '#faad14', // gold-6
  fail: '#ff4d4f', // red-6
  critical: '#eb2f96', // magenta-6
  skip: '#bfbfbf', // gray-5
  error: '#fa541c', // volcano-6
};

/** Run execution status → series hex (succeeded green · failed red · running indigo · …). */
export const RUN_STATUS_CHART_COLORS: Record<RunStatus, string> = {
  queued: '#bfbfbf', // gray-5
  running: BRAND.primary, // indigo — matches the brand "in-flight" accent
  succeeded: '#52c41a', // green-6
  failed: '#ff4d4f', // red-6
  cancelled: '#faad14', // gold-6
};

/**
 * Non-status chart tokens — the indigo primary for neutral/aggregate series (e.g. total runs),
 * plus the hairline grid + muted axis tints so every chart frames the same way the cards/tables
 */
export const CHART_COLORS = {
  primary: BRAND.primary,
  grid: BRAND.border,
  axis: '#8c8c8c',
} as const;

/** Shared recharts style props so axes/grid/tooltip are consistent across widgets. */
export const AXIS_TICK = { fontSize: 12, fill: CHART_COLORS.axis } as const;
export const GRID_PROPS = { stroke: CHART_COLORS.grid, strokeDasharray: '3 3' } as const;
export const TOOLTIP_STYLE = {
  borderRadius: 8,
  border: `1px solid ${BRAND.border}`,
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
