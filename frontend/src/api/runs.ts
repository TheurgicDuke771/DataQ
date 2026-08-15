import { api } from './client';
import { toListPage, type ListPage } from './listPage';
import type { OrchestrationProvider } from './triggerBindings';

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
  /** The asset resolved from the suite's target, stamped at dispatch (ADR 0034,
   *  #760); null for older rows / a targetless suite. Links the run back to its
   *  asset (#773). */
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

/** Mirrors `ResultRead`. `sample_failures` is the GX failing-row sample, redacted
 *  at the API boundary (#226): the numeric counts are kept; the raw cell values
 *  are masked to `"<redacted>"`. `redaction` / `redacted_columns` (#424) are the
 *  authoritative signal for *how much* of that sample is masked — read these
 *  instead of sniffing `sample_failures` for the `"<redacted>"` sentinel, which
 *  breaks the moment a genuine value equals it. `redaction` is null when the
 *  sample carried no data-bearing content to redact one way or the other (only
 *  aggregate counts, or no sample at all). */
export interface Result {
  id: string;
  check_id: string;
  status: ResultStatus;
  metric_value: number | null;
  duration_ms: number | null;
  observed_value: Record<string, unknown> | null;
  expected_value: Record<string, unknown> | null;
  sample_failures: Record<string, unknown> | null;
  redaction: 'full' | 'partial' | 'none' | null;
  redacted_columns: string[];
  /** How much of the dataset this check saw (#595); null = a complete read. */
  sampling?: ResultSampling | null;
}

/**
 * Mirrors the `results.sampling` record (backend `sampling.sampling_record`).
 *
 * **Branch on `sampled`, never on `rows < total_rows`.** `total_rows` is
 * legitimately null for a head sample that stopped reading rather than pay for a
 * count, and a "sample" that covered the whole dataset is not a sample —
 * labelling one would put a caveat on every small target and train the reader to
 * ignore the caveat that matters. The absence of the field entirely means a
 * complete read, which is also what every result written before scale-aware
 * execution means, so nothing needed backfilling.
 *
 * It is per **result**, not per run: within one run a volume monitor pushes its
 * `COUNT(*)` down and is exact while the expectations beside it saw 100k of 5M.
 */
export interface ResultSampling {
  /** `head` (first N rows in storage order) or `random`. */
  strategy: string;
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
}

/** One page of `GET /pipeline_runs` — the body (`items`) plus the
 *  `provider`/`status`-filtered population `total`, read off the
 *  `X-Total-Count` header (#1108, the `/assets` `AssetListPage` shape #925).
 *  `total` can exceed `items.length` when the fetch is capped below the true
 *  population (e.g. the Results page's single `LIST_LIMIT`-row fetch) — that's
 *  the truncation a caller needs to render honestly rather than silently. */
export type PipelineRunListPage = ListPage<PipelineRun>;

/** One page of `GET /runs` — the body (`items`) plus the caller-accessible
 *  population `total` from `X-Total-Count` (#1108). The total is scoped to the
 *  suites the caller can see (unlike `/assets`, which is workspace-true), and is
 *  the population the `limit`/`offset` slice into — so `items.length < total` is
 *  the only reliable way to know the page is truncated. */
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
  provider?: OrchestrationProvider;
  status?: string;
  limit?: number;
  offset?: number;
}): Promise<PipelineRunListPage> {
  const { data, headers } = await api.get<PipelineRun[]>('/pipeline_runs', { params });
  return toListPage(data, headers);
}

/** Download a comparison result's derived report (ADR 0015 §4) — fetched with
 *  the authenticated client (a plain anchor carries no bearer), then saved via
 *  a transient object URL. Nothing persists server-side. */
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
