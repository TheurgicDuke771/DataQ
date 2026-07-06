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

/** Mirrors the backend `TriggerBindingRead`. */
export interface TriggerBinding {
  id: string;
  provider: OrchestrationProvider;
  pipeline_or_dag_id: string;
  env: string;
  suite_id: string;
  enabled: boolean;
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
