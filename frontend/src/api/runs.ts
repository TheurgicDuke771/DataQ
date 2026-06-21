import { api } from './client';

/**
 * Runs / results / pipeline-runs API — the read surface behind the Results page
 * (backend `runs.py`, PR-C0b). The DQ-run reads are suite-scoped: the backend
 * filters to suites the caller can access, so this client never has to. Manual
 * run *triggering* (`runSuite` → `POST /suites/{id}/run`) lives here too, since
 * it produces a `Run`.
 */

/** Run execution lifecycle — `status` is execution, not data quality. */
export const RUN_STATUSES = ['queued', 'running', 'succeeded', 'failed', 'cancelled'] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/** Result severity tier (ADR 0005) + the two operational statuses (#122). */
export type ResultStatus = 'pass' | 'warn' | 'fail' | 'critical' | 'skip' | 'error';

/** Mirrors the backend `RunRead`. */
export interface Run {
  id: string;
  suite_id: string;
  status: RunStatus;
  triggered_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

/** Mirrors `ResultRead` — `sample_failures` is withheld by the API (PII, ADR 0018). */
export interface Result {
  id: string;
  check_id: string;
  status: ResultStatus;
  metric_value: number | null;
  duration_ms: number | null;
  observed_value: Record<string, unknown> | null;
  expected_value: Record<string, unknown> | null;
}

/** Mirrors `RunDetailRead` — a run plus its result rows. */
export interface RunDetail extends Run {
  results: Result[];
}

/** Mirrors `CheckProgressRead` — `status` is null while the check is pending. */
export interface CheckProgress {
  check_id: string;
  name: string;
  status: ResultStatus | null;
}

/**
 * Mirrors `RunProgressRead` — the compact live-progress shape the run-progress
 * UI polls: run lifecycle + per-check resolution + a status histogram. Lighter
 * than the full run+results detail (`getRun`).
 */
export interface RunProgress {
  run_id: string;
  suite_id: string;
  status: RunStatus;
  total_checks: number;
  completed_checks: number;
  counts: Record<string, number>;
  checks: CheckProgress[];
  started_at: string | null;
  finished_at: string | null;
}

/** Mirrors `PipelineRunRead` — a monitored orchestrator run (`pipeline_runs` ≠ `runs`). */
export interface PipelineRun {
  id: string;
  provider: 'adf' | 'airflow';
  connection_id: string;
  provider_run_id: string;
  pipeline_or_dag_id: string;
  env: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  failure_reason: string | null;
  created_at: string;
}

export async function listRuns(params?: {
  suite_id?: string;
  status?: RunStatus;
  limit?: number;
}): Promise<Run[]> {
  const { data } = await api.get<Run[]>('/runs', { params });
  return data;
}

export async function getRun(runId: string): Promise<RunDetail> {
  const { data } = await api.get<RunDetail>(`/runs/${runId}`);
  return data;
}

/**
 * Trigger a run of a suite (`POST /suites/{id}/run`). Edit-gated; returns the
 * queued `Run` (HTTP 202). The backend resolves the suite's target up front, so
 * a targetless/misconfigured suite fails with 422, and a broker outage with 503.
 */
export async function runSuite(suiteId: string): Promise<Run> {
  const { data } = await api.post<Run>(`/suites/${suiteId}/run`);
  return data;
}

/**
 * Poll a run's live progress (`GET /runs/{id}/progress`). Suite-scoped (view).
 * Cheaper than `getRun` — no observed/expected payloads — so it's the call the
 * live-progress UI hits on its polling interval.
 */
export async function getRunProgress(runId: string): Promise<RunProgress> {
  const { data } = await api.get<RunProgress>(`/runs/${runId}/progress`);
  return data;
}

/**
 * Cancel a non-terminal run (`POST /runs/{id}/cancel`). Edit-gated; returns the
 * updated `Run`. An already-finished run → 409. Cancel is cooperative (best-effort
 * for an in-flight run), so it may race a fast run to completion.
 */
export async function cancelRun(runId: string): Promise<Run> {
  const { data } = await api.post<Run>(`/runs/${runId}/cancel`);
  return data;
}

export async function listPipelineRuns(params?: {
  provider?: 'adf' | 'airflow';
  status?: string;
  limit?: number;
}): Promise<PipelineRun[]> {
  const { data } = await api.get<PipelineRun[]>('/pipeline_runs', { params });
  return data;
}
