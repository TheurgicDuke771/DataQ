import { api } from './client';

/**
 * Dashboard summary API — the read model behind the Enhanced Monitoring
 * Dashboard (backend `dashboard.py`, ADR 0022). One suite-scoped aggregate:
 * KPIs + a per-day run trend + per-suite performance. The backend scopes the
 * data to the caller's accessible (owned-or-shared) suites, so this client
 * never has to. KPIs are nullable: an empty workspace returns `null` rather
 * than a fabricated 0/100 (KPI honesty).
 */

/** Per-suite performance state band (mirrors `SuitePerformanceRead.state`). */
export type PerformanceState = 'optimal' | 'stable' | 'critical' | 'unknown';

/** Mirrors `KpisRead` — `null` when no severity results are in the window. */
export interface Kpis {
  health_score: number | null;
  pass_rate: number | null;
  total_runs: number;
  active_connections: number;
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

/**
 * Fetch the dashboard summary over a trailing window (`window_days`, 1–90;
 * default 7 server-side). The range selector maps 24h/7d/30d → 1/7/30.
 */
export async function getDashboardSummary(windowDays?: number): Promise<DashboardSummary> {
  const { data } = await api.get<DashboardSummary>('/dashboard/summary', {
    params: windowDays ? { window_days: windowDays } : undefined,
  });
  return data;
}
