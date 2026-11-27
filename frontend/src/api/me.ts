import { api } from './client';

import type { WorkspaceRole } from './admin';

export interface MeResponse {
  id: string;
  /**
   * Azure AD object id — null for identities provisioned without one (email-OTP users; ADR 0032
   * decision 6).
   */
  aad_object_id: string | null;
  email: string;
  display_name: string | null;
  last_seen_at: string | null;
  /**
   * The caller's EFFECTIVE workspace role (ADR 0033) — resolved server-side, so a break-glass
   * allowlist admin reads `admin` here even when their stored row says `member`.
   */
  role: WorkspaceRole;
  /**
   * Whether this user may use the /admin endpoints — gates the Admin nav + route (server-side
   * authz still enforces; this only decides what to render).
   */
  is_workspace_admin: boolean;
}

export async function fetchMe(): Promise<MeResponse> {
  const { data } = await api.get<MeResponse>('/me');
  return data;
}

/** Self-service profile update (#1139) — currently just the display name. */
export async function updateMe(displayName: string): Promise<MeResponse> {
  const { data } = await api.patch<MeResponse>('/me', { display_name: displayName });
  return data;
}

export type AccessLevel = 'owner' | 'admin' | 'edit' | 'view';

export interface OwnedSuite {
  id: string;
  name: string;
  description: string | null;
}

/** The caller's suite access, counted by the server (`GET /me/access-summary`). */
export interface AccessSummary {
  total_visible: number;
  /**
   * Suites by the level the caller holds on them. For a workspace admin every suite they did not
   * create is `admin` whether or not anyone shared it (ADR 0033), so these are not share counts.
   */
  counts_by_level: Record<AccessLevel, number>;
  /** The suites the caller created, by name; empty for a workspace viewer. */
  owned: OwnedSuite[];
}

export async function fetchAccessSummary(signal?: AbortSignal): Promise<AccessSummary> {
  const { data } = await api.get<AccessSummary>('/me/access-summary', { signal });
  return data;
}
