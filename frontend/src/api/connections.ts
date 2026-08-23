import { api } from './client';

/** Connections API — the eight configurable connection types (CLAUDE.md §4). */

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
 * Datasource vs orchestration is the load-bearing distinction in DataQ (CLAUDE.md §4): datasources
 * are stores you write checks against.
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
 * Coarser datasource grouping for the Results datasource-type filter (ADR 0022): the two flat-file
 * types (ADLS Gen2 + S3) share a runner shape and read as one "Flat file" choice.
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
 * Datasources GX can run a custom-SQL (`UnexpectedRowsExpectation`) query against — mirrors the
 * backend `custom_sql.SQL_QUERYABLE_TYPES` (ADR 0019).
 */
export const SQL_QUERYABLE_TYPES: ConnectionType[] = ['snowflake', 'unity_catalog'];

export const isSqlQueryable = (type: ConnectionType): boolean => SQL_QUERYABLE_TYPES.includes(type);

/** The flat-file datasources — the only ones with a native per-object arrival
 *  time, so the only ones that can measure freshness without a column (#520). */
export const FILE_TYPES: ConnectionType[] = ['adls_gen2', 's3'];

export const isFileDatasource = (type: ConnectionType): boolean => FILE_TYPES.includes(type);

/**
 * Datasources whose runner can evaluate freshness/volume **monitors** — the SQL datasources (in-
 * warehouse aggregate), Iceberg (native `scan().count()` / a column MAX, ADR 0030).
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
  /**
   * `null` once the creating user is erased — the row outlives its author (`ondelete=SET NULL`,
   * #1319).
   */
  created_by: string | null;
  /** Poll health (#828) — orchestration connections only. */
  last_polled_at?: string | null;
  last_poll_error?: string | null;
  consecutive_poll_failures?: number;
  /** Run-derived health (#954) — DATASOURCE connections. */
  last_run_at?: string | null;
  last_run_error?: string | null;
  consecutive_run_failures?: number;
  /** When the credential itself says it stops working (#838) — a SAS prints `se=`. */
  credential_expires_at?: string | null;
  /** When the expiry was last READ (#1024). */
  credential_expiry_checked_at?: string | null;
  /**
   * Inventory-sync outcome (#1104) — opted-in snowflake/unity_catalog connections only
   * (config.inventory_sync, ADR 0040).
   */
  inventory_sync_last_attempted_at?: string | null;
  inventory_sync_last_error?: string | null;
  inventory_sync_failing_since?: string | null;
  /**
   * Zero-table enumeration state (#1242) — a SUCCESSFUL sync that enumerates zero tables is not an
   * error (Snowflake's INFORMATION_SCHEMA is privilege-filtered, not access-denied.
   */
  inventory_sync_last_table_count?: number | null;
  inventory_sync_zero_since?: string | null;
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
 * The `name · type · ENV` label used by the connection-picker `Select` in the suite create +
 * import drawers.
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

/** Mirrors the backend `ConnectionCreate` schema (secrets are write-only). */
export interface ConnectionCreate {
  name: string;
  type: ConnectionType;
  env: ConnectionEnv;
  config: Record<string, unknown>;
  secret?: string;
  catalog_secret?: string;
}

export async function createConnection(payload: ConnectionCreate): Promise<Connection> {
  const { data } = await api.post<Connection>('/connections', payload);
  return data;
}

/**
 * A draft-test payload is exactly a create payload minus `name` — a draft has no row and needs
 * none (#351).
 */
export type ConnectionDraftTest = Omit<ConnectionCreate, 'name'>;

/**
 * Live connectivity test for an UNSAVED draft — the config/secret the user just typed on
 * `/connections/new`, probed before Create is pressed.
 */
export async function testDraftConnection(payload: ConnectionDraftTest): Promise<{ ok: boolean }> {
  const { data } = await api.post<{ ok: boolean }>('/connections/test', payload);
  return data;
}

/** Mirrors the backend `ConnectionUpdate` schema — type/env are immutable. */
export interface ConnectionUpdate {
  name?: string;
  config?: Record<string, unknown>;
  secret?: string;
  catalog_secret?: string;
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
 * Mirrors the backend `ConnectionVersionRead` — one immutable snapshot in a connection's edit
 * history (#654).
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
