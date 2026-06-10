import { api } from './client';

/**
 * Connections API — the six configurable connection types (CLAUDE.md §4).
 * ADF + Airflow are orchestration providers, not datasources, but they are
 * still `connections` rows and managed through the same CRUD surface.
 */

export const CONNECTION_TYPES = [
  'snowflake',
  'adls_gen2',
  's3',
  'unity_catalog',
  'adf',
  'airflow',
] as const;
export type ConnectionType = (typeof CONNECTION_TYPES)[number];

export const CONNECTION_ENVS = ['dev', 'qa', 'uat', 'prod'] as const;
export type ConnectionEnv = (typeof CONNECTION_ENVS)[number];

/** Display label for an env (single source for the list page + the drawer). */
export const envLabel = (env: ConnectionEnv): string => env.toUpperCase();

/** Tag color per env — shared by every page that renders an env badge. */
export const ENV_COLORS: Record<ConnectionEnv, string> = {
  dev: 'blue',
  qa: 'gold',
  uat: 'purple',
  prod: 'red',
};

/** Mirrors the backend `ConnectionRead` schema (secret is never returned). */
export interface Connection {
  id: string;
  name: string;
  type: ConnectionType;
  env: ConnectionEnv;
  config: Record<string, unknown>;
  has_secret: boolean;
  created_by: string;
}

/** Human-readable labels for the connection types, for grouping + display. */
export const CONNECTION_TYPE_LABELS: Record<ConnectionType, string> = {
  snowflake: 'Snowflake',
  adls_gen2: 'ADLS Gen2',
  s3: 'AWS S3',
  unity_catalog: 'Unity Catalog',
  adf: 'Azure Data Factory',
  airflow: 'Airflow',
};

export async function listConnections(params?: {
  type?: ConnectionType;
  env?: ConnectionEnv;
}): Promise<Connection[]> {
  const { data } = await api.get<Connection[]>('/connections', { params });
  return data;
}

/** Live connectivity test — a green result means the credential authenticates. */
export async function testConnection(id: string): Promise<{ ok: boolean }> {
  const { data } = await api.post<{ ok: boolean }>(`/connections/${id}/test`);
  return data;
}

/** Mirrors the backend `ConnectionCreate` schema (secret is write-only). */
export interface ConnectionCreate {
  name: string;
  type: ConnectionType;
  env: ConnectionEnv;
  config: Record<string, unknown>;
  secret?: string;
}

export async function createConnection(payload: ConnectionCreate): Promise<Connection> {
  const { data } = await api.post<Connection>('/connections', payload);
  return data;
}

/** Mirrors the backend `ConnectionUpdate` schema — type/env are immutable. */
export interface ConnectionUpdate {
  name?: string;
  config?: Record<string, unknown>;
  secret?: string;
}

export async function updateConnection(id: string, payload: ConnectionUpdate): Promise<Connection> {
  const { data } = await api.patch<Connection>(`/connections/${id}`, payload);
  return data;
}

export async function deleteConnection(id: string): Promise<void> {
  await api.delete(`/connections/${id}`);
}

/** Rotate the credential and verify it in one step (bad credential → error). */
export async function reauthConnection(id: string, secret: string): Promise<{ ok: boolean }> {
  const { data } = await api.post<{ ok: boolean }>(`/connections/${id}/reauth`, { secret });
  return data;
}
