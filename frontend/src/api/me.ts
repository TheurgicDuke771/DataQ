import { api } from './client';

import type { WorkspaceRole } from './admin';

export interface MeResponse {
  id: string;
  /** Azure AD object id — null for identities provisioned without one (email-OTP
   *  users; ADR 0032 decision 6). Nothing in the SPA reads it today; keep it
   *  null-safe if that changes. */
  aad_object_id: string | null;
  email: string;
  display_name: string | null;
  last_seen_at: string | null;
  /** The caller's EFFECTIVE workspace role (ADR 0033) — resolved server-side, so
   *  a break-glass allowlist admin reads `admin` here even when their stored row
   *  says `member`. The UI mirrors this to decide what to render; the server
   *  stays the decider and re-enforces on every request. */
  role: WorkspaceRole;
  /** Whether this user may use the /admin endpoints — gates the Admin nav + route
   *  (server-side authz still enforces; this only decides what to render).
   *  Equivalent to `role === 'admin'`; kept because every existing caller reads
   *  it and removing it would break them for no gain. */
  is_workspace_admin: boolean;
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>('/me');
  return data;
}

/** Self-service profile update (#1139) — currently just the display name.
 * Returns the refreshed `/me` body, so callers can adopt it directly instead
 * of a second round trip. */
export async function updateMe(displayName: string): Promise<MeResponse> {
  const { data } = await api.patch<MeResponse>('/me', { display_name: displayName });
  return data;
}
