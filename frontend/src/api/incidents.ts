import { api } from './client';

/**
 * Incidents API — the stateful, deduped, evidence-carrying roll-up of the per-result alert signal
 * (ADR 0034 #761).
 */

export type IncidentStatus = 'open' | 'acknowledged' | 'resolved';
export type IncidentResolvedBy = 'user' | 'auto';

/** List-row / summary view — mirrors the backend `IncidentRead`. */
export interface Incident {
  id: string;
  asset_id: string;
  check_id: string;
  suite_id: string;
  status: IncidentStatus;
  resolved_by: IncidentResolvedBy | null;
  occurrence_count: number;
  created_at: string;
  last_seen_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  /** Lifted from the snapshotted evidence card (may be null on legacy rows). */
  check_name: string | null;
  asset_namespace: string | null;
  asset_name: string | null;
  /** Breaching tier of the most recent occurrence (warn|fail|critical). */
  latest_status: string | null;
}

/**
 * Layer-1 evidence card (`services/incident_evidence.py`, ADR 0034 decision 4). Every layer is
 * best-effort on the backend — an exception degrades it to `null` rather than poisoning the whole
 * card — so a `null` layer must render as "not available", never as an absent/omitted field.
 * `profile_diff` is `null` unconditionally today (not yet implemented).
 */
export interface EvidenceCheckLayer {
  id: string;
  name: string | null;
  expectation_type: string | null;
  kind: string;
}

export interface EvidenceAssetLayer {
  id: string;
  namespace: string;
  name: string;
  env: string;
}

export interface EvidenceFailingResultLayer {
  status: string;
  metric_value: number | null;
  observed_value: Record<string, unknown> | null;
  expected_value: Record<string, unknown> | null;
}

export interface EvidenceTrendPoint {
  status: string;
  metric_value: number | null;
  created_at: string | null;
  run_id: string;
}

export interface EvidenceSiblingCheck {
  check_name: string | null;
  status: string;
}

export interface EvidenceUpstreamPipelineRun {
  provider: string;
  pipeline_or_dag_id: string;
  provider_run_id: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  /** Positive = slower than this pipeline's own recent-history average. */
  delay_seconds_vs_history: number | null;
}

export interface EvidenceBlastRadiusAsset {
  id: string;
  namespace: string;
  name: string;
  env: string;
}

export interface IncidentEvidence {
  generated_at: string;
  check: EvidenceCheckLayer | null;
  asset: EvidenceAssetLayer | null;
  failing_result: EvidenceFailingResultLayer | null;
  metric_trend: EvidenceTrendPoint[] | null;
  sibling_checks: EvidenceSiblingCheck[] | null;
  upstream_pipeline_run: EvidenceUpstreamPipelineRun | null;
  downstream_blast_radius: EvidenceBlastRadiusAsset[] | null;
  /** Always `null` today — a live datasource profile diff of both batches is
   *  not yet implemented (see the backend module docstring). */
  profile_diff: unknown | null;
}

/** Incident detail — mirrors `IncidentDetailRead` (summary + evidence + actors). */
export interface IncidentDetail extends Incident {
  acknowledged_by: string | null;
  resolved_by_user_id: string | null;
  prior_incident_id: string | null;
  acknowledge_note: string | null;
  resolution_note: string | null;
  evidence: IncidentEvidence | null;
}

export async function listIncidents(params?: {
  asset_id?: string;
  suite_id?: string;
  state?: IncidentStatus;
  limit?: number;
}): Promise<Incident[]> {
  const { data } = await api.get<Incident[]>('/incidents', { params });
  return data;
}

export async function getIncident(incidentId: string): Promise<IncidentDetail> {
  const { data } = await api.get<IncidentDetail>(`/incidents/${incidentId}`);
  return data;
}

/** Acknowledge an incident (open → acknowledged). Needs edit on its suite. */
export async function acknowledgeIncident(
  incidentId: string,
  note?: string,
): Promise<IncidentDetail> {
  const { data } = await api.post<IncidentDetail>(`/incidents/${incidentId}/ack`, {
    note: note ?? null,
  });
  return data;
}

/** Resolve an incident (→ resolved, resolved_by=user). Needs edit on its suite. */
export async function resolveIncident(incidentId: string, note?: string): Promise<IncidentDetail> {
  const { data } = await api.post<IncidentDetail>(`/incidents/${incidentId}/resolve`, {
    note: note ?? null,
  });
  return data;
}
