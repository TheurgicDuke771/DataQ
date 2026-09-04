import { api } from './client';

/** Admin-only outbound-LLM provider config (issue #1511, ADR 0042). */

export type LlmProvider = 'anthropic' | 'openai_compatible';
export type StructuredOutputMode = 'native' | 'prompt_json';

export interface LlmConfig {
  configured: boolean;
  provider: LlmProvider | null;
  base_url: string | null;
  model: string | null;
  structured_output: StructuredOutputMode | null;
  enabled: boolean;
  /** Whether a credential is stored — the key itself is write-only, never returned. */
  has_credential: boolean;
  updated_at: string | null;
}

export interface LlmConfigUpdate {
  provider: LlmProvider;
  model: string;
  base_url?: string;
  /** Write-only. Omit to keep the stored key — except a provider/base_url change,
   *  which the backend refuses (422 `llm_config_invalid`) unless re-supplied. */
  api_key?: string;
  structured_output: StructuredOutputMode;
  enabled: boolean;
}

export type LlmTestErrorCode =
  | 'llm_provider_unavailable'
  | 'llm_provider_error'
  | 'llm_config_invalid'
  | 'llm_credential_missing'
  | 'secret_store_unavailable';

/** Result of a live probe — persists nothing. */
export interface LlmTestResult {
  ok: boolean;
  model?: string;
  latency_ms?: number;
  reply_chars?: number;
  error_code?: LlmTestErrorCode;
  error?: string;
}

export async function getLlmConfig(): Promise<LlmConfig> {
  const { data } = await api.get<LlmConfig>('/admin/llm');
  return data;
}

export async function updateLlmConfig(update: LlmConfigUpdate): Promise<LlmConfig> {
  const { data } = await api.put<LlmConfig>('/admin/llm', update);
  return data;
}

export async function testLlmConfig(update: LlmConfigUpdate): Promise<LlmTestResult> {
  const { data } = await api.post<LlmTestResult>('/admin/llm/test', update);
  return data;
}

// ── Feature invocations (#1845) ──────────────────────────────────────────────
// Every feature call is queued to a worker and answered by polling the invocation;
// `waitForLlmInvocation` is the one polling loop all three UI surfaces share.

export type LlmInvocationStatus = 'pending' | 'running' | 'succeeded' | 'failed';

export interface LlmInvocation<TResponse = Record<string, unknown>> {
  id: string;
  kind: string;
  status: LlmInvocationStatus;
  suite_id: string | null;
  response: TResponse | null;
  error: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  duration_ms: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface LlmInvocationQueued {
  invocation_id: string;
  status: 'pending';
}

export interface SqlGenerationResponse {
  sql: string;
  explanation: string;
}

export interface CheckSuggestion {
  expectation_type: string;
  name: string;
  rationale: string;
  config: Record<string, unknown>;
  dimension: string | null;
  /** Only on a `monitor:freshness` suggestion — grounded in the bound pipeline's cadence. */
  fail_threshold_hours?: number | null;
}

export interface RejectedSuggestion {
  name?: string | null;
  expectation_type?: string | null;
  reason: string;
}

/** A trigger binding that nearly matched this suite (right pipeline, wrong env). */
export interface CoverageWarning {
  provider: string;
  pipeline_or_dag_id: string;
  run_env: string | null;
  binding_env: string | null;
  last_observed_at: string;
}

export interface CheckSuggestionsResponse {
  suggestions: CheckSuggestion[];
  rejected: RejectedSuggestion[];
  coverage_warnings: CoverageWarning[];
}

export type RcaConfidence = 'high' | 'medium' | 'low';

export interface RcaHypothesis {
  cause: string;
  confidence: RcaConfidence;
  evidence_refs: string[];
}

export interface RcaNarrative {
  summary: string;
  ranked_hypotheses: RcaHypothesis[];
  /** Computed by DataQ, not the model — what the evidence card could not see. */
  blind_spots: string[];
  suggested_next_checks?: string[];
}

export async function generateSql(payload: {
  suite_id: string;
  description: string;
  include_profile?: boolean;
}): Promise<LlmInvocationQueued> {
  const { data } = await api.post<LlmInvocationQueued>('/llm/sql_generation', payload);
  return data;
}

export async function suggestChecks(suiteId: string): Promise<LlmInvocationQueued> {
  const { data } = await api.post<LlmInvocationQueued>('/llm/check_suggestions', {
    suite_id: suiteId,
  });
  return data;
}

export async function generateRcaNarrative(incidentId: string): Promise<LlmInvocationQueued> {
  const { data } = await api.post<LlmInvocationQueued>('/llm/rca_narrative', {
    incident_id: incidentId,
  });
  return data;
}

export async function getLlmInvocation<T = Record<string, unknown>>(
  invocationId: string,
): Promise<LlmInvocation<T>> {
  const { data } = await api.get<LlmInvocation<T>>(`/llm/invocations/${invocationId}`);
  return data;
}

const DEFAULT_POLL_MS = 2000;
/** Generations run on a worker against a live model; a slow local model needs minutes. */
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;

export class LlmInvocationTimeout extends Error {
  constructor(public readonly invocationId: string) {
    super('the generation is still running — try again in a moment');
    this.name = 'LlmInvocationTimeout';
  }
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const id = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(id);
      reject(new DOMException('aborted', 'AbortError'));
    });
  });

/**
 * Poll an invocation until it is terminal. Resolves with the row in either terminal state —
 * a `failed` row carries its reason in `error`, and the caller decides how to show it.
 */
export async function waitForLlmInvocation<T = Record<string, unknown>>(
  invocationId: string,
  opts: { signal?: AbortSignal; pollMs?: number; timeoutMs?: number } = {},
): Promise<LlmInvocation<T>> {
  const pollMs = opts.pollMs ?? DEFAULT_POLL_MS;
  const deadline = Date.now() + (opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  for (;;) {
    const row = await getLlmInvocation<T>(invocationId);
    if (row.status === 'succeeded' || row.status === 'failed') return row;
    if (Date.now() >= deadline) throw new LlmInvocationTimeout(invocationId);
    await sleep(pollMs, opts.signal);
  }
}

/** Queue a feature call and wait for its result in one step. */
export async function runLlmFeature<T>(
  start: () => Promise<LlmInvocationQueued>,
  opts?: { signal?: AbortSignal; pollMs?: number; timeoutMs?: number },
): Promise<LlmInvocation<T>> {
  const queued = await start();
  return waitForLlmInvocation<T>(queued.invocation_id, opts);
}
