import { api } from './client';

/** Global search — the read behind the command palette (backend `search.py`). */

export const SEARCH_GROUPS = ['suites', 'checks', 'connections', 'runs'] as const;
export type SearchGroupName = (typeof SEARCH_GROUPS)[number];

/** Mirrors the backend `SearchHitRead`. */
export interface SearchHit {
  id: string;
  /** The entity's name; a run is labelled by its suite. */
  label: string;
  /** A check's suite, a connection's `type · env`, a run's status. */
  sublabel: string | null;
  /** The SPA path that opens the hit for this caller. */
  href: string;
  /** When a run started (or was queued); null for every other kind. */
  when?: string | null;
}

/** Mirrors the backend `SearchResponse`. */
export interface SearchResponse {
  query: string;
  groups: Record<SearchGroupName, SearchHit[]>;
  /** Groups with more matches than were returned. */
  truncated_groups: SearchGroupName[];
}

export const SEARCH_LIMIT = 8;

export async function search(q: string, signal?: AbortSignal): Promise<SearchResponse> {
  const { data } = await api.get<SearchResponse>('/search', {
    params: { q, limit: SEARCH_LIMIT },
    signal,
  });
  return data;
}
