import { api } from './client';

/**
 * Runs / results / pipeline-runs API — the read surface behind the Results page
 * (backend `runs.py`, PR-C0b). The DQ-run reads are suite-scoped: the backend
 * filters to suites the caller can access, so this client never has to. Manual
 * run *triggering* (`POST /suites/{id}/run`) is the execution UI's concern, not
 * this read module's.
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

export async function listPipelineRuns(params?: {
  provider?: 'adf' | 'airflow';
  status?: string;
  limit?: number;
}): Promise<PipelineRun[]> {
  const { data } = await api.get<PipelineRun[]>('/pipeline_runs', { params });
  return data;
}
