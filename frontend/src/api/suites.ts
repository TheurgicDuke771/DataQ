import { api } from './client';

/**
 * Suites API — a suite is a named bundle of DQ checks bound to one connection.
 * `connection_id` is set at create and immutable thereafter (re-pointing would
 * orphan the child checks), mirroring the backend `suite_service` contract.
 */

/** Mirrors the backend `SuiteRead` schema. */
export interface Suite {
  id: string;
  name: string;
  description: string | null;
  connection_id: string;
  /** Datasource-shaped run target (#215); null = not yet runnable. */
  target: Record<string, unknown> | null;
  /** The asset this suite's target resolves to (ADR 0034, #760); null for a
   *  targetless/unresolvable suite. Links the suite back to its asset (#773). */
  asset_id?: string | null;
  created_by: string;
  /** The caller's effective level — gates per-suite actions (share, delete).
   *  `owner`/`admin`/`edit`/`view`; absent on older payloads. */
  my_permission?: 'owner' | 'admin' | 'edit' | 'view' | null;
}

/**
 * Owner/admin — may manage shares + delete the suite. The single source for the
 * "manage" gate so the suite-detail actions and any other surface never drift.
 */
export function canManageSuite(suite: Suite): boolean {
  return suite.my_permission === 'owner' || suite.my_permission === 'admin';
}

/**
 * The `edit` capability ladder (owner/admin/edit) — may trigger/cancel runs and
 * manage triggers/schedules, mirroring the backend `POST /suites/{id}/run` gate.
 * Shared by the suite-detail Run button and the cross-suite Run-now panel so the
 * runnable-permission policy lives in one place.
 */
export function canRunSuite(suite: Suite): boolean {
  return canManageSuite(suite) || suite.my_permission === 'edit';
}

/**
 * The datasource-shaped identity carried in `Suite.target` (#215): SQL targets
 * fill `table`/`schema`/`catalog`, Iceberg fills `namespace`/`table`, flat-file
 * targets fill `path`/`file_format`. The wire shape is an untyped JSONB bag
 * (`Record<string, unknown>`); read it through `targetString` so the dry-run
 * preview and column profiler don't each re-hand-roll the `typeof x === 'string'`
 * extraction.
 */
export interface RunTarget {
  table?: string;
  schema?: string;
  catalog?: string;
  /** Iceberg namespace (folded to `namespace.table` by the backend resolver). */
  namespace?: string;
  path?: string;
  file_format?: 'csv' | 'parquet';
  /** Flat-file *batch* selector (a literal `path` and `pattern` are mutually exclusive). */
  pattern?: string;
}

/** Read one string field out of the untyped run-target bag, or `undefined`. */
export function targetString(
  target: Record<string, unknown> | null,
  key: keyof RunTarget,
): string | undefined {
  const value = target?.[key];
  return typeof value === 'string' ? value : undefined;
}

/** Mirrors `SuiteCreate` — connection_id is required and immutable. `target` is
 *  optional (a suite may be created targetless = not-yet-runnable). */
export interface SuiteCreate {
  name: string;
  description?: string | null;
  connection_id: string;
  target?: RunTarget | null;
}

/** Mirrors `SuiteUpdate` — name/description/target are mutable (connection isn't).
 *  A `null`/omitted `target` leaves the existing one unchanged (backend semantics:
 *  it never clears a target back to NULL). */
export interface SuiteUpdate {
  name?: string;
  description?: string | null;
  target?: RunTarget | null;
}

export async function listSuites(params?: { connection_id?: string }): Promise<Suite[]> {
  const { data } = await api.get<Suite[]>('/suites', { params });
  return data;
}

export async function getSuite(id: string): Promise<Suite> {
  const { data } = await api.get<Suite>(`/suites/${id}`);
  return data;
}

export async function createSuite(payload: SuiteCreate): Promise<Suite> {
  const { data } = await api.post<Suite>('/suites', payload);
  return data;
}

export async function updateSuite(id: string, payload: SuiteUpdate): Promise<Suite> {
  const { data } = await api.patch<Suite>(`/suites/${id}`, payload);
  return data;
}

export async function deleteSuite(id: string): Promise<void> {
  await api.delete(`/suites/${id}`);
}

/** Mirrors the backend `CheckRead` schema (read-only here — editor is a later slice). */
export interface Check {
  id: string;
  suite_id: string;
  name: string;
  kind: string;
  expectation_type: string;
  config: Record<string, unknown>;
  warn_threshold: number | null;
  fail_threshold: number | null;
  critical_threshold: number | null;
  /** Alert suppression (#370): in the future = alerts muted until then; null /
   *  past = active. Set via the snooze endpoints, never PATCH. */
  alert_snoozed_until: string | null;
}

export async function listChecks(suiteId: string): Promise<Check[]> {
  const { data } = await api.get<Check[]>(`/suites/${suiteId}/checks`);
  return data;
}

/** Fetch one check by id — backs the deep-linkable `/checks/:id/edit` page. */
export async function getCheck(suiteId: string, checkId: string): Promise<Check> {
  const { data } = await api.get<Check>(`/suites/${suiteId}/checks/${checkId}`);
  return data;
}

/** Mirrors `CheckCreate` — `kind` is `expectation` (incl. custom-SQL) or a monitor
 *  kind (`freshness`/`volume`, ADR 0012); omitted defaults to `expectation`. */
export interface CheckCreate {
  name: string;
  kind?: string;
  expectation_type: string;
  config: Record<string, unknown>;
  warn_threshold?: number | null;
  fail_threshold?: number | null;
  critical_threshold?: number | null;
}

/** Mirrors `CheckUpdate` — all fields optional; kind is immutable. */
export interface CheckUpdate {
  name?: string;
  expectation_type?: string;
  config?: Record<string, unknown>;
  warn_threshold?: number | null;
  fail_threshold?: number | null;
  critical_threshold?: number | null;
}

export async function createCheck(suiteId: string, payload: CheckCreate): Promise<Check> {
  const { data } = await api.post<Check>(`/suites/${suiteId}/checks`, payload);
  return data;
}

export async function updateCheck(
  suiteId: string,
  checkId: string,
  payload: CheckUpdate,
): Promise<Check> {
  const { data } = await api.patch<Check>(`/suites/${suiteId}/checks/${checkId}`, payload);
  return data;
}

export async function deleteCheck(suiteId: string, checkId: string): Promise<void> {
  await api.delete(`/suites/${suiteId}/checks/${checkId}`);
}

/** Mute a noisy check's alerts for N hours (edit-gated; backend caps at 720h). */
export async function snoozeCheck(suiteId: string, checkId: string, hours: number): Promise<Check> {
  const { data } = await api.post<Check>(`/suites/${suiteId}/checks/${checkId}/snooze`, { hours });
  return data;
}

/** Clear a check's alert snooze — alerts fire again immediately (edit-gated). */
export async function clearCheckSnooze(suiteId: string, checkId: string): Promise<Check> {
  const { data } = await api.delete<Check>(`/suites/${suiteId}/checks/${checkId}/snooze`);
  return data;
}

/**
 * Mirrors the backend `CheckVersionRead` — one immutable snapshot in a check's
 * history (#280). `changed_by_name` is the author's display name (or email),
 * resolved server-side; null for a system actor or a removed user.
 */
export interface CheckVersion {
  version_no: number;
  name: string;
  kind: string;
  expectation_type: string;
  config: Record<string, unknown>;
  warn_threshold: number | null;
  fail_threshold: number | null;
  critical_threshold: number | null;
  changed_by: string | null;
  changed_by_name: string | null;
  created_at: string;
}

/** A check's version history, newest first. Requires `view` on the suite. */
export async function listCheckVersions(suiteId: string, checkId: string): Promise<CheckVersion[]> {
  const { data } = await api.get<CheckVersion[]>(`/suites/${suiteId}/checks/${checkId}/versions`);
  return data;
}

/**
 * Mirrors the backend `CheckResultPointRead` — one past result of a check
 * (status + `metric_value` + run time), the datum behind the per-check trend
 * chart (ADR 0022). `metric_value` is null for checks that record no metric.
 */
export interface CheckResultPoint {
  run_id: string;
  status: string;
  metric_value: number | null;
  created_at: string;
}

/**
 * A check's recent results in chronological order (oldest→newest) for the trend
 * chart. Requires `view` on the suite; `limit` caps the window (1–180, default 30).
 */
export async function listCheckHistory(
  suiteId: string,
  checkId: string,
  limit?: number,
): Promise<CheckResultPoint[]> {
  const { data } = await api.get<CheckResultPoint[]>(
    `/suites/${suiteId}/checks/${checkId}/history`,
    { params: limit ? { limit } : undefined },
  );
  return data;
}

// ───────────────────────── export / import (portable documents) ─────

/**
 * Mirrors the backend `CheckDocument` — a check's authoring fields only, no DB
 * identity. Thresholds may arrive as a number or a string (the backend's
 * `Decimal` JSON encoding is not pinned); kept as-is so the document round-trips
 * byte-for-faithful on re-import — never coerce or re-format them.
 */
export interface CheckDocument {
  name: string;
  kind: string;
  expectation_type: string;
  config: Record<string, unknown>;
  warn_threshold: number | string | null;
  fail_threshold: number | string | null;
  critical_threshold: number | string | null;
}

/** Mirrors the backend `SuiteDocument` — a portable, connection-agnostic suite
 *  (the export response and the import payload are the same shape). */
export interface SuiteDocument {
  version: number;
  name: string;
  description: string | null;
  checks: CheckDocument[];
}

export async function exportSuite(suiteId: string): Promise<SuiteDocument> {
  const { data } = await api.get<SuiteDocument>(`/suites/${suiteId}/export`);
  return data;
}

/** Mirrors `SuiteImportRequest` — import a document onto a target connection
 *  (the new suite is owned by the importing user, like create). */
export interface SuiteImportRequest {
  connection_id: string;
  document: SuiteDocument;
}

export async function importSuite(payload: SuiteImportRequest): Promise<Suite> {
  const { data } = await api.post<Suite>('/suites/import', payload);
  return data;
}

/** Mirrors `CheckDryRunRequest` — preview one check against live data, no persist.
 *  The target is resolved server-side from the suite's own run target (#215/#532),
 *  so no target fields are sent; works on Snowflake, Unity Catalog, and flat files. */
export interface CheckDryRunRequest {
  expectation_type: string;
  config: Record<string, unknown>;
  warn_threshold?: number | null;
  fail_threshold?: number | null;
  critical_threshold?: number | null;
}

/** Mirrors `CheckDryRunResult` — the preview outcome (severity tier + metric). */
export interface CheckDryRunResult {
  status: string; // pass | warn | fail | critical
  metric_value: number | null;
  observed_value: Record<string, unknown> | null;
  expected_value: Record<string, unknown> | null;
}

export async function dryRunCheck(
  suiteId: string,
  payload: CheckDryRunRequest,
): Promise<CheckDryRunResult> {
  const { data } = await api.post<CheckDryRunResult>(`/suites/${suiteId}/checks/dryrun`, payload);
  return data;
}

/** Mirrors the backend `ColumnProfileRequest` — profile columns of the suite's
 *  table/file (no persistence). The target identity (`table`/`schema`/`catalog`
 *  for SQL, `path`/`file_format` for flat files) comes from the suite's run
 *  target (#215); `columns` is the subset to profile. */
export interface ColumnProfileRequest {
  columns: string[];
  top_n?: number;
  table?: string | null;
  schema?: string | null;
  catalog?: string | null;
  /** Iceberg: the table's optional namespace (addressed as `namespace.table`). */
  namespace?: string | null;
  path?: string | null;
  file_format?: 'csv' | 'parquet' | null;
}

/** Mirrors the backend `TopValue` — a value and how often it occurs. */
export interface TopValue {
  value: unknown;
  count: number;
}

/** Mirrors the backend `ColumnProfileRead` — per-column stats. */
export interface ColumnProfile {
  column: string;
  null_count: number;
  null_fraction: number;
  distinct_count: number | null;
  min_value: unknown;
  max_value: unknown;
  top_values: TopValue[];
}

/** Mirrors the backend `ProfileRead` — row count + per-column stats. Identity
 *  fields are type-specific (SQL fills `table`/`schema`/`catalog`, flat files
 *  fill `path`/`file_format`). */
export interface ProfileResult {
  row_count: number;
  columns: ColumnProfile[];
  table?: string | null;
  schema?: string | null;
  catalog?: string | null;
  path?: string | null;
  file_format?: string | null;
}

export async function profileColumns(
  suiteId: string,
  payload: ColumnProfileRequest,
): Promise<ProfileResult> {
  const { data } = await api.post<ProfileResult>(`/suites/${suiteId}/profile`, payload);
  return data;
}

/** The target-identity subset that names a table/file (no `columns`/`top_n`). */
export type ColumnTarget = Pick<
  ColumnProfileRequest,
  'table' | 'schema' | 'catalog' | 'namespace' | 'path' | 'file_format'
>;

/** Mirrors the backend `GET /suites/{id}/columns` — the column names of the
 *  suite's table/file target, so the check editor can offer a dropdown instead
 *  of free-text (#474). Target identity is passed as query params. */
export async function listColumns(suiteId: string, target: ColumnTarget): Promise<string[]> {
  const params: Record<string, string> = {};
  if (target.table) params.table = target.table;
  if (target.schema) params.schema = target.schema;
  if (target.catalog) params.catalog = target.catalog;
  if (target.namespace) params.namespace = target.namespace;
  if (target.path) params.path = target.path;
  if (target.file_format) params.file_format = target.file_format;
  const { data } = await api.get<{ columns: string[] }>(`/suites/${suiteId}/columns`, { params });
  return data.columns;
}
