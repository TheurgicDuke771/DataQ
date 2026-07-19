import { api } from './client';

/**
 * Connections API — the eight configurable connection types (CLAUDE.md §4).
 * ADF, Airflow + dbt are orchestration providers, not datasources, but they are
 * still `connections` rows and managed through the same CRUD surface.
 */

export const CONNECTION_TYPES = [
  'snowflake',
  'adls_gen2',
  's3',
  'unity_catalog',
  'iceberg',
  'adf',
  'airflow',
  'dbt',
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
  iceberg: 'datasource',
  adf: 'orchestration',
  airflow: 'orchestration',
  dbt: 'orchestration',
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
 * Coarser datasource grouping for the Results datasource-type filter (ADR 0022):
 * the two flat-file types (ADLS Gen2 + S3) share a runner shape and read as one
 * "Flat file" choice, while Snowflake and Unity Catalog stand alone. Orchestration
 * types map to `null` — they're never queryable, so they never back a suite/run.
 */
export const DATASOURCE_CATEGORIES = ['snowflake', 'flatfile', 'unity_catalog', 'iceberg'] as const;
export type DatasourceCategory = (typeof DATASOURCE_CATEGORIES)[number];

export const DATASOURCE_CATEGORY: Record<ConnectionType, DatasourceCategory | null> = {
  snowflake: 'snowflake',
  adls_gen2: 'flatfile',
  s3: 'flatfile',
  unity_catalog: 'unity_catalog',
  iceberg: 'iceberg',
  adf: null,
  airflow: null,
  dbt: null,
};

export const DATASOURCE_CATEGORY_LABELS: Record<DatasourceCategory, string> = {
  snowflake: 'Snowflake',
  flatfile: 'Flat file',
  unity_catalog: 'Unity Catalog',
  iceberg: 'Apache Iceberg',
};

/**
 * Datasources GX can run a custom-SQL (`UnexpectedRowsExpectation`) query against
 * — mirrors the backend `custom_sql.SQL_QUERYABLE_TYPES` (ADR 0019). The custom-SQL
 * check category is offered only for these SQL-queryable types; flat files (ADLS /
 * S3) are DataFrame assets, not SQL, and the backend 422s custom-SQL on any other.
 */
export const SQL_QUERYABLE_TYPES: ConnectionType[] = ['snowflake', 'unity_catalog'];

export const isSqlQueryable = (type: ConnectionType): boolean => SQL_QUERYABLE_TYPES.includes(type);

/** The flat-file datasources — the only ones with a native per-object arrival
 *  time, so the only ones that can measure freshness without a column (#520). */
export const FILE_TYPES: ConnectionType[] = ['adls_gen2', 's3'];

export const isFileDatasource = (type: ConnectionType): boolean => FILE_TYPES.includes(type);

/**
 * Datasources whose runner can evaluate freshness/volume **monitors** — the SQL
 * datasources (in-warehouse aggregate), Iceberg (native `scan().count()` / a
 * column MAX, ADR 0030), and flat files (over the resolved batch, #520). Broader
 * than `SQL_QUERYABLE_TYPES`: neither Iceberg nor a flat file is SQL-queryable
 * (no custom-SQL). Mirrors the backend `check_service.MONITOR_CAPABLE_TYPES`
 * author gate.
 */
export const MONITOR_CAPABLE_TYPES: ConnectionType[] = [
  'snowflake',
  'unity_catalog',
  'iceberg',
  ...FILE_TYPES,
];

export const supportsMonitors = (type: ConnectionType): boolean =>
  MONITOR_CAPABLE_TYPES.includes(type);

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
  /** Poll health (#828) — orchestration connections only. A failing poll used to be
   *  visible ONLY in the logs, so a dead integration looked exactly like a healthy
   *  quiet one. `last_poll_error` is a classified reason, never raw exception text. */
  last_polled_at?: string | null;
  last_poll_error?: string | null;
  consecutive_poll_failures?: number;
}

/** Human-readable labels for the connection types, for grouping + display. */
export const CONNECTION_TYPE_LABELS: Record<ConnectionType, string> = {
  snowflake: 'Snowflake',
  adls_gen2: 'ADLS Gen2',
  s3: 'AWS S3',
  unity_catalog: 'Unity Catalog',
  iceberg: 'Apache Iceberg',
  adf: 'Azure Data Factory',
  airflow: 'Airflow',
  dbt: 'dbt',
};

/**
 * The `name · type · ENV` label used by the connection-picker `Select` in the
 * suite create + import drawers. One definition so the format can't drift
 * between the two pickers.
 */
export const connectionOptionLabel = (c: Connection): string =>
  `${c.name} · ${CONNECTION_TYPE_LABELS[c.type]} · ${envLabel(c.env)}`;

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

/**
 * Mirrors the backend `ConnectionVersionRead` — one immutable snapshot in a
 * connection's edit history (#654). Only the editable, non-secret fields are
 * versioned; no credential is ever present. `changed_by_name` is resolved
 * server-side (null for a system actor / removed user).
 */
export interface ConnectionVersion {
  version_no: number;
  name: string;
  type: ConnectionType;
  env: ConnectionEnv;
  config: Record<string, unknown>;
  changed_by: string | null;
  changed_by_name: string | null;
  created_at: string;
}

/** A connection's version history, newest first. */
export async function listConnectionVersions(id: string): Promise<ConnectionVersion[]> {
  const { data } = await api.get<ConnectionVersion[]>(`/connections/${id}/versions`);
  return data;
}
