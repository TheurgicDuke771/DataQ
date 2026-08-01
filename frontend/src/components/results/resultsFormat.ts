import type { PipelineRun, ResultStatus, RunStatus } from '../../api/runs';

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

/**
 * antd `Progress` bar status per run lifecycle state. A `Record` (not a switch)
 * so a new `RunStatus` value is a compile error here rather than silently
 * falling through to `'normal'`.
 */
export const RUN_BAR_STATUS: Record<RunStatus, 'success' | 'exception' | 'active' | 'normal'> = {
  queued: 'normal',
  running: 'active',
  succeeded: 'success',
  failed: 'exception',
  cancelled: 'exception',
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

/**
 * The `triggered_by` marker a pipeline run stamps on the DQ runs it triggers:
 * `<provider>:<pipeline_or_dag_id>:<provider_run_id>` (backend
 * `orchestration_service._trigger_suites`). The Results pipeline tab uses it to
 * correlate a monitored pipeline run back to the DQ run(s) it kicked off — one
 * pipeline run can trigger several (one per trigger binding), all sharing this
 * marker. Kept in sync with the backend format; pure so it can be unit-tested.
 */
export function pipelineRunMarker(p: PipelineRun): string {
  return `${p.provider}:${p.pipeline_or_dag_id}:${p.provider_run_id}`;
}

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
 * Anomaly's (#593) cold-start `observed_value` payload —
 * `{insufficient_history: true, points, min_points, ...}` — reads as a bare
 * `skip` tag plus a raw JSON blob otherwise; the author has to reverse-engineer
 * that shape to learn "it just hasn't seen enough runs yet". A friendlier
 * "collecting history: k of n points" says the same thing plainly. Returns
 * `null` for anything else (not cold-start, or a payload missing the fields —
 * render nothing rather than a guessed count) so callers can skip the hint
 * cleanly. Pure so it's unit-testable without a check/result fixture.
 */
export function anomalyColdStartHint(observedValue: unknown): string | null {
  if (observedValue === null || typeof observedValue !== 'object') return null;
  const v = observedValue as Record<string, unknown>;
  if (v.insufficient_history !== true) return null;
  const points = typeof v.points === 'number' ? v.points : null;
  const minPoints = typeof v.min_points === 'number' ? v.min_points : null;
  if (points === null || minPoints === null) return null;
  return `Collecting history: ${points} of ${minPoints} points`;
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

/**
 * True when `iso` falls within the last `windowDays` days (inclusive). Used by
 * the Results date filter; a null/unparseable timestamp is treated as out of
 * window so rows with no date never leak past a date filter. Kept pure so it can
 * be unit-tested without rendering.
 */
export function isWithinWindowDays(iso: string | null, windowDays: number): boolean {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return false;
  return t >= Date.now() - windowDays * 24 * 60 * 60 * 1000;
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
  return formatDurationMs(ms);
}

/** A millisecond interval as the same compact human string (`—` when negative
 *  or not a number — clock skew / no data). */
export function formatDurationMs(ms: number): string {
  if (Number.isNaN(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSeconds = Math.round(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

/**
 * The document title (and the filename a browser's Save-as-PDF dialog
 * suggests) while a run is loaded (#345): suite + short run id + today's
 * date, so the artifact is identifiable without opening it.
 */
export function runReportTitle(
  suiteName: string | null,
  run: { id: string; suite_id: string },
): string {
  const subject = suiteName ?? `Run ${run.suite_id.slice(0, 8)}`;
  const date = new Date().toLocaleDateString();
  return `${subject} — Run ${run.id.slice(0, 8)} — ${date} · DataQ`;
}
