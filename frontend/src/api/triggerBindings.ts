import { api } from './client';

/**
 * Trigger bindings — map a successful orchestrator run to a suite so the suite
 * runs on that pipeline/DAG's success (CLAUDE.md §4). Provider-agnostic: the
 * composite key (`provider`, `pipeline_or_dag_id`, `env`) → `suite_id`. Managing
 * a binding needs `edit` on the suite (backend-gated); listing needs `view`.
 * Orchestration providers are *never* a datasource — this is the only place a
 * pipeline/DAG id is bound to a suite.
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
 * What each provider's DataQ callback snippet hooks into — used in setup copy
 * ("Configured in the <noun> callback snippet"). Exhaustive over the tuple so a
 * new provider is a compile error here, not silently inherited Airflow wording
 * (the #647 mislabeling class, one layer up). ADF authenticates via URL token
 * (no snippet), so its entry is only for exhaustiveness.
 */
export const PROVIDER_CALLBACK_NOUNS: Record<OrchestrationProvider, string> = {
  adf: 'pipeline',
  airflow: 'DAG',
  dbt: 'post-build',
};

/**
 * Mirrors the backend `TriggerBindingWarningRead` — an advisory, non-blocking
 * signal returned alongside a create/update response (#1186). Today's one code:
 * `ambiguous_orchestration_url` — this binding's (provider, env) connection
 * shares its resource (e.g. an Airflow `base_url`) with a connection in a
 * DIFFERENT env, so a pipeline/DAG run attributed to that other env will not
 * match this binding — it can silently never fire.
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
 * Mirrors the backend `NearMissRead` (#1186/#1199) — a currently-active env
 * mismatch: a succeeded pipeline/DAG run keeps landing in `run_env`, but the
 * only ENABLED binding for this `(provider, pipeline_or_dag_id)` is scoped to
 * `binding_env`, so it has never fired and never will until one of the two
 * envs is corrected. Distinct from `TriggerBindingWarning` above: that one is
 * advisory and fires at create/update time from a shared-URL heuristic; this is
 * the ingest-time signal — the mismatch was actually observed happening.
 */
export interface TriggerEnvNearMiss {
  provider: OrchestrationProvider;
  pipeline_or_dag_id: string;
  run_env: string;
  binding_env: string;
  updated_at: string;
}

/**
 * `GET /orchestration/near-misses` — suite-scoped like `GET /trigger-bindings`
 * (near-misses are derived from suite-owned binding rows, so the backend restricts
 * them to owned-or-shared suites), NOT auth-only like `/orchestration/pipelines`.
 * Pass `suiteId` to narrow to one suite's bindings.
 */
export async function listEnvNearMisses(suiteId?: string): Promise<TriggerEnvNearMiss[]> {
  const { data } = await api.get<TriggerEnvNearMiss[]>('/orchestration/near-misses', {
    params: suiteId ? { suite_id: suiteId } : undefined,
  });
  return data;
}
