import { api } from './client';

/**
 * Suite sharing — grant/list/update/revoke per-user access to a suite, plus the
 * directory search that turns an email/name into the `user_id` a share keys on.
 *
 * Permission ladder (backend `suite_authz`): `view` < `edit` < `admin`; `owner`
 * is the implicit, immutable creator (never a share row). Managing shares needs
 * `admin`; listing needs `view`.
 */

/** Grantable share levels — NOT `owner` (that's the creator, not a share). */
export type SharePermission = 'view' | 'edit' | 'admin';

/** The caller's effective level on a suite, as stamped on `SuiteRead.my_permission`. */
export type EffectivePermission = SharePermission | 'owner';

/** Mirrors the backend `ShareRead` — a share enriched with the grantee's identity. */
export interface Share {
  suite_id: string;
  user_id: string;
  permission: SharePermission;
  email: string;
  display_name: string | null;
}

/** Mirrors the backend `UserSummary` — the directory-picker sliver of a user. */
export interface UserSummary {
  id: string;
  email: string;
  display_name: string | null;
}

export async function listShares(suiteId: string): Promise<Share[]> {
  const { data } = await api.get<Share[]>(`/suites/${suiteId}/shares`);
  return data;
}

export async function grantShare(
  suiteId: string,
  payload: { user_id: string; permission: SharePermission },
): Promise<Share> {
  const { data } = await api.post<Share>(`/suites/${suiteId}/shares`, payload);
  return data;
}

export async function updateShare(
  suiteId: string,
  userId: string,
  permission: SharePermission,
): Promise<Share> {
  const { data } = await api.patch<Share>(`/suites/${suiteId}/shares/${userId}`, { permission });
  return data;
}

export async function revokeShare(suiteId: string, userId: string): Promise<void> {
  await api.delete(`/suites/${suiteId}/shares/${userId}`);
}

/** Search the user directory by email/display-name substring (min 2 chars). */
export async function searchUsers(q: string): Promise<UserSummary[]> {
  const { data } = await api.get<UserSummary[]>('/users/search', { params: { q } });
  return data;
}
