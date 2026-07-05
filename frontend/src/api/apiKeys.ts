import { api } from './client';

/**
 * Personal access tokens (PATs) — the caller's own DataQ-issued API keys (ADR 0026
 * phase 1, #461). A PAT authenticates as you (`Authorization: Bearer dq_live_…`) on
 * the REST API and `/mcp` alike, inheriting your per-suite access.
 *
 * User-scoped: every call operates on the signed-in user's keys. The plaintext
 * token is returned **exactly once**, by `createApiKey`; list/read expose metadata
 * only (prefix, expiry, revocation, last-used) — never the secret.
 */

/** Backend expiry bounds (`api_key_service`): no non-expiring keys. */
export const PAT_DEFAULT_EXPIRY_DAYS = 90;
export const PAT_MAX_EXPIRY_DAYS = 365;

/** Mirrors the backend `ApiKeyRead` — metadata only, never the token. */
export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  last_used_at: string | null;
}

/** Mirrors `ApiKeyCreated` — the creation response, the ONLY place `token` appears. */
export interface ApiKeyCreated extends ApiKey {
  token: string;
}

export async function listApiKeys(): Promise<ApiKey[]> {
  const { data } = await api.get<ApiKey[]>('/me/api-keys');
  return data;
}

export async function createApiKey(payload: {
  name: string;
  expires_in_days: number;
}): Promise<ApiKeyCreated> {
  const { data } = await api.post<ApiKeyCreated>('/me/api-keys', payload);
  return data;
}

export async function revokeApiKey(keyId: string): Promise<void> {
  await api.delete(`/me/api-keys/${keyId}`);
}
