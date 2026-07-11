import { api } from './client';

/**
 * Incidents API — the stateful, deduped, evidence-carrying roll-up of the
 * per-result alert signal (ADR 0034 #761). Anchored to (asset, check); at most
 * one active incident per pair.
 *
 * **Authz is derived, never granted** (backend `incident_service`): an incident is
 * visible iff the caller can view its suite; ack/resolve require `edit` on that
 * suite. The client never scopes — it renders what the API returns and lets the
 * backend 403 an unpermitted action.
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

/** Incident detail — mirrors `IncidentDetailRead` (summary + evidence + actors). */
export interface IncidentDetail extends Incident {
  acknowledged_by: string | null;
  resolved_by_user_id: string | null;
  prior_incident_id: string | null;
  acknowledge_note: string | null;
  resolution_note: string | null;
  evidence: Record<string, unknown> | null;
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
