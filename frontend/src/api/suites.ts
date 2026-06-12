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
}

/** Mirrors `SuiteCreate` — connection_id is required and immutable. */
export interface SuiteCreate {
  name: string;
  description?: string | null;
  connection_id: string;
}

/** Mirrors `SuiteUpdate` — only name/description are mutable. */
export interface SuiteUpdate {
  name?: string;
  description?: string | null;
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
