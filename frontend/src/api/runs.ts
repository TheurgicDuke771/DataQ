import { api } from './client';
import { toListPage, type ListPage } from './listPage';
import type { SampleStrategy } from './suites';
import type { OrchestrationProvider } from './triggerBindings';

/**
 * Runs / results / pipeline-runs API — the read surface behind the Results page (backend
 * `runs.py`, PR-C0b).
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
  /**
   * The asset resolved from the suite's target, stamped at dispatch (ADR 0034, #760); null for
   * older rows / a targetless suite.
   */
  asset_id?: string | null;
  status: RunStatus;
  triggered_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  /** Data-quality outcome — distinct from `status` (execution): a run is
   *  `succeeded` even when checks fail. `worst_severity` is null when all passed. */
  checks_total: number;
  checks_passed: number;
  worst_severity: 'warn' | 'fail' | 'critical' | null;
  /** Redaction-safe reason for a `failed` run (#605) — a fixed classified
   *  message, never raw adapter text. Null for non-failed runs and older rows. */
  failure_reason: string | null;
}

/** Mirrors `ResultRead`. */
export interface Result {
  id: string;
  check_id: string;
  status: ResultStatus;
  metric_value: number | null;
  duration_ms: number | null;
  observed_value: Record<string, unknown> | null;
  expected_value: Record<string, unknown> | null;
  sample_failures: Record<string, unknown> | null;
  /** `zero_sample` (#1873) means the deployment's zero-sample privacy mode never
   *  persisted a sample for this result at all — distinct from a `null` sample
   *  that genuinely had nothing to redact. */
  redaction: 'full' | 'partial' | 'none' | 'zero_sample' | null;
  redacted_columns: string[];
  /** How much of the dataset this check saw (#595); null = a complete read. */
  sampling?: ResultSampling | null;
}

/** Mirrors the `results.sampling` record (backend `sampling.sampling_record`). */
export interface ResultSampling {
  /** `head` (first N rows in storage order) or `random`. */
  strategy: SampleStrategy;
  /** The row cap the suite's target asked for. */
  requested_rows?: number | null;
  /** What the check engine actually saw. */
  rows?: number | null;
  /** The population it was drawn from — null when learning it would have cost
   *  the very scan the sample exists to avoid. */
  total_rows?: number | null;
  /** The honest headline: false means the read covered everything. */
  sampled: boolean;
  /** `random` only — present when the draw was made reproducible. */
  seed?: number | null;
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
 * Mirrors `RunProgressRead` — the compact live-progress shape the run-progress UI polls: run
 * lifecycle + per-check resolution + a status histogram.
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
  /**
   * How long the run has been going, measured on the **server** clock (#318) — never recompute it
   * from `started_at` against the browser's.
   */
  elapsed_ms?: number | null;
  /**
   * True when at least one **unresolved** check belongs to a kind that resolves as a group rather
   * than one at a time — every kind but `comparison` (#318).
   */
  batched_pending?: boolean;
}

/** Mirrors `PipelineRunRead` — a monitored orchestrator run (`pipeline_runs` ≠ `runs`). */
export interface PipelineRun {
  id: string;
  provider: OrchestrationProvider;
  connection_id: string;
  provider_run_id: string;
  pipeline_or_dag_id: string;
  env: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  failure_reason: string | null;
  created_at: string;
  /**
   * DQ runs this pipeline run triggered, correlated server-side (a colliding marker yields `[]`).
   * Optional only for the rolling-deploy window where a draining api revision predates the field.
   */
  triggered_run_ids?: string[];
}

/**
 * One page of `GET /pipeline_runs` — the body (`items`) plus the `provider`/`status`-filtered
 * population `total`, read off the `X-Total-Count` header (#1108.
 */
export type PipelineRunListPage = ListPage<PipelineRun>;

/**
 * One page of `GET /runs` — the body (`items`) plus the caller-accessible population `total` from
 * `X-Total-Count` (#1108).
 */
export type RunListPage = ListPage<Run>;

export async function listRuns(params?: {
  suite_id?: string;
  /** Closed vocabulary — the backend 422s anything outside `RUN_STATUSES`
   *  rather than answering a confidently-empty page (#828). */
  status?: RunStatus;
  limit?: number;
  offset?: number;
}): Promise<RunListPage> {
  const { data, headers } = await api.get<Run[]>('/runs', { params });
  return toListPage(data, headers);
}

export async function getRun(runId: string): Promise<RunDetail> {
  const { data } = await api.get<RunDetail>(`/runs/${runId}`);
  return data;
}

/** Trigger a run of a suite (`POST /suites/{id}/run`). */
export async function runSuite(suiteId: string): Promise<Run> {
  const { data } = await api.post<Run>(`/suites/${suiteId}/run`);
  return data;
}

/** Poll a run's live progress (`GET /runs/{id}/progress`). */
export async function getRunProgress(runId: string): Promise<RunProgress> {
  const { data } = await api.get<RunProgress>(`/runs/${runId}/progress`);
  return data;
}

/** Cancel a non-terminal run (`POST /runs/{id}/cancel`). */
export async function cancelRun(runId: string): Promise<Run> {
  const { data } = await api.post<Run>(`/runs/${runId}/cancel`);
  return data;
}

export async function listPipelineRuns(params?: {
  provider?: OrchestrationProvider;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<PipelineRunListPage> {
  const { data, headers } = await api.get<PipelineRun[]>('/pipeline_runs', { params });
  return toListPage(data, headers);
}

/**
 * Download a comparison result's derived report (ADR 0015 §4) — fetched with the authenticated
 * client (a plain anchor carries no bearer), then saved via a transient object URL.
 */
export async function downloadComparisonReport(
  runId: string,
  resultId: string,
  fmt: 'csv' | 'xlsx',
): Promise<void> {
  const { data } = await api.get<Blob>(`/runs/${runId}/results/${resultId}/comparison_report`, {
    params: { fmt },
    responseType: 'blob',
  });
  const url = URL.createObjectURL(data);
  const link = document.createElement('a');
  link.href = url;
  link.download = `comparison-${resultId}.${fmt}`;
  link.click();
  // Deferred: a same-tick revoke can abort the download (Safari) — the browser
  // dereferences the blob URL asynchronously after click().
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
