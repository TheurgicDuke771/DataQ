import type { ResultStatus, RunStatus } from '../../api/runs';

/**
 * Pure presentation helpers for the Results surface — kept framework-free so the
 * status→colour mapping and the timing formatters can be unit-tested without
 * rendering antd.
 */

/** antd Tag colour per run execution status. */
export const RUN_STATUS_COLORS: Record<RunStatus, string> = {
  queued: 'default',
  running: 'processing',
  succeeded: 'success',
  failed: 'error',
  cancelled: 'warning',
};

/** antd Tag colour per result severity / operational status (ADR 0005 / #122). */
export const RESULT_STATUS_COLORS: Record<ResultStatus, string> = {
  pass: 'success',
  warn: 'warning',
  fail: 'error',
  critical: 'magenta',
  skip: 'default',
  error: 'volcano',
};

/** Orchestrator pipeline-run status → colour (provider-agnostic value set). */
export function pipelineStatusColor(status: string): string {
  switch (status) {
    case 'succeeded':
      return 'success';
    case 'failed':
      return 'error';
    case 'running':
      return 'processing';
    case 'cancelled':
      return 'warning';
    default:
      return 'default';
  }
}

/**
 * Render an unknown scalar (a GX observed/expected value, a profiled min/max or
 * top value) for display: an em dash for null/undefined, JSON for objects, the
 * `String` form otherwise. Falsy scalars (`0`, `false`, `''`) render as
 * themselves — not collapsed to the em dash — so a real zero isn't mistaken for
 * "no value". The em-dash sentinel matches `formatTimestamp` / `formatDuration`.
 */
export function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

/** Absolute timestamp as a locale string, or an em dash when absent. */
export function formatTimestamp(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
}

/**
 * Run duration (finished − started) as a compact human string: `850ms`, `12s`,
 * `1m 3s`. Returns an em dash when either bound is missing (queued / never
 * finished) or the interval is negative (clock skew).
 */
export function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt || !finishedAt) return '—';
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (Number.isNaN(ms) || ms < 0) return '—';
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}
