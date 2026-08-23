import { api } from './client';

/**
 * Trigger bindings — map a successful orchestrator run to a suite so the suite runs on that
 * pipeline/DAG's success (CLAUDE.md §4).
 */

/** Mirrors the backend `ORCHESTRATION_PROVIDERS` tuple (db/models.py — ADR 0029). */
export const ORCHESTRATION_PROVIDERS = ['adf', 'airflow', 'dbt'] as const;
export type OrchestrationProvider = (typeof ORCHESTRATION_PROVIDERS)[number];

export const PROVIDER_LABELS: Record<OrchestrationProvider, string> = {
  adf: 'Azure Data Factory',
  airflow: 'Apache Airflow',
  dbt: 'dbt',
};

/**
 * What each provider's DataQ callback snippet hooks into — used in setup copy ("Configured in the
 * <noun> callback snippet").
 */
export const PROVIDER_CALLBACK_NOUNS: Record<OrchestrationProvider, string> = {
  adf: 'pipeline',
  airflow: 'DAG',
  dbt: 'post-build',
};

/**
 * Mirrors the backend `TriggerBindingWarningRead` — an advisory, non-blocking signal returned
 * alongside a create/update response (#1186).
 */
export interface TriggerBindingWarning {
  code: string;
  message: string;
  other_envs: string[];
}

/** Mirrors the backend `TriggerBindingRead`. */
export interface TriggerBinding {
  id: string;
  provider: OrchestrationProvider;
  pipeline_or_dag_id: string;
  env: string;
  suite_id: string;
  enabled: boolean;
  /** Populated on create/update; always `[]` on a plain list/get read (#1186). */
  warnings: TriggerBindingWarning[];
}

/** Mirrors `TriggerBindingCreate`. */
export interface TriggerBindingCreate {
  provider: OrchestrationProvider;
  pipeline_or_dag_id: string;
  env: string;
  suite_id: string;
  enabled?: boolean;
}

export async function listTriggerBindings(suiteId: string): Promise<TriggerBinding[]> {
  const { data } = await api.get<TriggerBinding[]>('/trigger-bindings', {
    params: { suite_id: suiteId },
  });
  return data;
}

export async function createTriggerBinding(payload: TriggerBindingCreate): Promise<TriggerBinding> {
  const { data } = await api.post<TriggerBinding>('/trigger-bindings', payload);
  return data;
}

/** Toggle a binding on/off without deleting it (`PATCH` — the only mutable field). */
export async function setTriggerBindingEnabled(
  id: string,
  enabled: boolean,
): Promise<TriggerBinding> {
  const { data } = await api.patch<TriggerBinding>(`/trigger-bindings/${id}`, { enabled });
  return data;
}

export async function deleteTriggerBinding(id: string): Promise<void> {
  await api.delete(`/trigger-bindings/${id}`);
}

/**
 * Mirrors the backend `NearMissRead` (#1186/#1199) — a currently-active env mismatch: a succeeded
 * pipeline/DAG run keeps landing in `run_env`, but the only ENABLED binding for this `(provider.
 */
export interface TriggerEnvNearMiss {
  provider: OrchestrationProvider;
  pipeline_or_dag_id: string;
  run_env: string;
  binding_env: string;
  updated_at: string;
}

/**
 * `GET /orchestration/near-misses` — suite-scoped like `GET /trigger-bindings` (near-misses are
 * derived from suite-owned binding rows, so the backend restricts them to owned-or-shared suites).
 */
export async function listEnvNearMisses(suiteId?: string): Promise<TriggerEnvNearMiss[]> {
  const { data } = await api.get<TriggerEnvNearMiss[]>('/orchestration/near-misses', {
    params: suiteId ? { suite_id: suiteId } : undefined,
  });
  return data;
}
