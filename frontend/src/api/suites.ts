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
 * fill `table`/`schema`/`catalog`, flat-file targets fill `path`/`file_format`.
 * The wire shape is an untyped JSONB bag (`Record<string, unknown>`); read it
 * through `targetString` so the dry-run preview and column profiler don't each
 * re-hand-roll the `typeof x === 'string'` extraction.
 */
export interface RunTarget {
  table?: string;
  schema?: string;
  catalog?: string;
  path?: string;
  file_format?: 'csv' | 'parquet';
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
}

export async function listChecks(suiteId: string): Promise<Check[]> {
  const { data } = await api.get<Check[]>(`/suites/${suiteId}/checks`);
  return data;
}

/** Mirrors `CheckCreate` — v1 only authors `kind: 'expectation'` (service-enforced). */
export interface CheckCreate {
  name: string;
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
 *  `table`/`schema` come from the suite's run target (#215). v1: Snowflake only. */
export interface CheckDryRunRequest {
  expectation_type: string;
  config: Record<string, unknown>;
  warn_threshold?: number | null;
  fail_threshold?: number | null;
  critical_threshold?: number | null;
  table: string;
  schema?: string | null;
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
