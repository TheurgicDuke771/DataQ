import type { Scorecard } from './assets';
import { api } from './client';

/**
 * Dashboard summary API — the read model behind the Enhanced Monitoring Dashboard (backend
 * `dashboard.py`, ADR 0022).
 */

/** Per-suite performance state band (mirrors `SuitePerformanceRead.state`). */
export type PerformanceState = 'optimal' | 'stable' | 'critical' | 'unknown';

/** Mirrors `KpisRead` — `null` when no severity results are in the window. */
export interface Kpis {
  health_score: number | null;
  pass_rate: number | null;
  total_runs: number;
  active_connections: number;
  avg_duration_ms: number | null;
  health_score_delta: number | null;
  pass_rate_delta: number | null;
  total_runs_delta_pct: number | null;
  avg_duration_delta_pct: number | null;
}

/** Mirrors `TrendPointRead` — one zero-filled day of succeeded/failed run counts. */
export interface TrendPoint {
  day: string; // ISO date (YYYY-MM-DD)
  succeeded: number;
  failed: number;
}

/** Mirrors `SuitePerformanceRead` — a suite's health from its latest run. */
export interface SuitePerformance {
  suite_id: string;
  name: string;
  score: number | null;
  state: PerformanceState;
}

/** Mirrors `DashboardSummaryRead`. */
export interface DashboardSummary {
  window_days: number;
  kpis: Kpis;
  trend: TrendPoint[];
  suite_performance: SuitePerformance[];
}

/** Fetch the dashboard summary over a trailing window (`window_days`, 1–90; default 7 server-side). */
export async function getDashboardSummary(windowDays?: number): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>('/dashboard/summary', {
    params: windowDays ? { window_days: windowDays } : undefined,
  });
  return data;
}

/**
 * Each DQ dimension across every suite in the workspace. Workspace-wide, unlike the summary:
 * it includes suites the caller cannot open and is the same for every member.
 */
export async function getWorkspaceDimensions(): Promise<Scorecard> {
  const { data } = await api.get<Scorecard>('/dashboard/dimensions');
  return data;
}

/** Mirrors `CoverageFiguresRead` — workspace-wide, counts only. */
export interface CoverageFigures {
  assets_total: number;
  assets_watched: number;
  assets_watched_authored: number;
  assets_watched_auto_only: number;
  /** Null when the workspace has no assets. */
  coverage_pct: number | null;
  coverage_window_days: number;
  false_positive_window_days: number;
  /** Automatic-suite incidents a person resolved in the window. */
  resolved: number;
  stated: number;
  unstated: number;
  false_positive: number;
  /** `false_positive / stated`. Null when nothing was stated: not measured, not zero. */
  false_positive_rate: number | null;
}

export async function getCoverageFigures(): Promise<CoverageFigures> {
  const { data } = await api.get<CoverageFigures>('/dashboard/coverage');
  return data;
}

/** Mirrors `OnboardingStatusRead` — workspace-wide, booleans only. */
export interface OnboardingStatus {
  has_datasource: boolean;
  has_suite: boolean;
  has_check: boolean;
  has_run: boolean;
  complete: boolean;
}

export async function getOnboardingStatus(): Promise<OnboardingStatus> {
  const { data } = await api.get<OnboardingStatus>('/dashboard/onboarding');
  return data;
}
