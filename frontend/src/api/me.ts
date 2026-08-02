import { api } from './client';

export interface MeResponse {
  id: string;
  /** Azure AD object id — null for identities provisioned without one (email-OTP
   *  users; ADR 0032 decision 6). Nothing in the SPA reads it today; keep it
   *  null-safe if that changes. */
  aad_object_id: string | null;
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
