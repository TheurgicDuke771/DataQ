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

/**
 * Datasource vs orchestration is the load-bearing distinction in DataQ
 * (CLAUDE.md §4): datasources are stores you write checks against; ADF/Airflow
 * are orchestration providers we monitor + trigger from, never queryable. This
 * map is the single source for that split — the add-connection picker and the
 * sectioned list both derive their groups from it (no hardcoded lists elsewhere).
 */
export const CONNECTION_KINDS = ['datasource', 'orchestration'] as const;
export type ConnectionKind = (typeof CONNECTION_KINDS)[number];

export const CONNECTION_KIND: Record<ConnectionType, ConnectionKind> = {
  snowflake: 'datasource',
  adls_gen2: 'datasource',
  s3: 'datasource',
  unity_catalog: 'datasource',
  adf: 'orchestration',
  airflow: 'orchestration',
};

export const CONNECTION_KIND_LABELS: Record<ConnectionKind, string> = {
  datasource: 'Data sources',
  orchestration: 'Orchestration',
};

/** Types of a given kind, in canonical CONNECTION_TYPES order. */
export const typesOfKind = (kind: ConnectionKind): ConnectionType[] =>
  CONNECTION_TYPES.filter((t) => CONNECTION_KIND[t] === kind);

export const DATASOURCE_TYPES = typesOfKind('datasource');
export const ORCHESTRATION_TYPES = typesOfKind('orchestration');

/**
 * Datasources GX can run a custom-SQL (`UnexpectedRowsExpectation`) query against
 * — mirrors the backend `custom_sql.SQL_QUERYABLE_TYPES` (ADR 0019). Flat files
 * (ADLS / S3) are DataFrame assets, not SQL, so the custom-SQL check category is
 * offered only for these types; the backend rejects it (422) for any other.
 */
export const SQL_QUERYABLE_TYPES: ConnectionType[] = ['snowflake', 'unity_catalog'];

export const isSqlQueryable = (type: ConnectionType): boolean => SQL_QUERYABLE_TYPES.includes(type);

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

/** Fetch one connection by id (e.g. to learn a suite's datasource type). */
export async function getConnection(id: string): Promise<Connection> {
  const { data } = await api.get<Connection>(`/connections/${id}`);
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
