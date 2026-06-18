import { api } from './client';

export interface MeResponse {
  id: string;
  aad_object_id: string;
  email: string;
  display_name: string | null;
  last_seen_at: string | null;
  /** Whether this user may use the /admin endpoints — gates the Admin nav + route
   *  (server-side authz still enforces; this only decides what to render). */
  is_workspace_admin: boolean;
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>('/me');
  return data;
}
