import { api } from './client';
import { apiPath } from './path';

/** A proposed rule in the automatic-coverage review queue (ADR 0047). */
export interface Suggestion {
  id: string;
  suite_id: string;
  source: 'profile' | 'llm';
  status: 'pending' | 'accepted' | 'rejected';
  name: string;
  expectation_type: string;
  config: Record<string, unknown>;
  rationale: string | null;
  check_id: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
}

export async function listSuggestions(suiteId: string): Promise<Suggestion[]> {
  const { data } = await api.get<Suggestion[]>(apiPath`/suites/${suiteId}/suggestions`);
  return data;
}

export async function acceptSuggestion(id: string): Promise<Suggestion> {
  const { data } = await api.post<Suggestion>(apiPath`/suggestions/${id}/accept`);
  return data;
}

export async function rejectSuggestion(id: string): Promise<Suggestion> {
  const { data } = await api.post<Suggestion>(apiPath`/suggestions/${id}/reject`);
  return data;
}
