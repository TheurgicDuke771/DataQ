import type { ConnectionType } from '../../api/connections';
import { type RunTarget, targetString } from '../../api/suites';

/**
 * A suite's run target (#215) is datasource-shaped: SQL warehouses identify a
 * `table` (+ optional `schema`), Unity Catalog adds a required `catalog`, and
 * flat-file stores (ADLS / S3) identify a `path` (+ optional `file_format`).
 * `targetKind` collapses the six datasource types to the three input shapes the
 * editor renders; orchestration types never reach here (they can't back a suite).
 */
export type TargetKind = 'sql' | 'uc' | 'flatfile';

export function targetKind(type: ConnectionType): TargetKind | null {
  switch (type) {
    case 'snowflake':
      return 'sql';
    case 'unity_catalog':
      return 'uc';
    case 'adls_gen2':
    case 's3':
      return 'flatfile';
    default:
      return null; // adf / airflow — not a datasource
  }
}

/**
 * Collapse a stored run target to a one-line summary for read-only display:
 * flat files show their `path`; SQL / Unity Catalog show the dotted
 * `catalog.schema.table` (only the parts present). Returns `null` for a
 * targetless (not-yet-runnable) suite. Lives here next to the other
 * datasource-target-shape logic so a new target field has one owner.
 */
export function summarizeTarget(target: Record<string, unknown> | null): string | null {
  if (!target) return null;
  const path = targetString(target, 'path');
  if (path) return path;
  const parts = [
    targetString(target, 'catalog'),
    targetString(target, 'schema'),
    targetString(target, 'table'),
  ].filter((p): p is string => Boolean(p));
  return parts.length > 0 ? parts.join('.') : null;
}

/** The raw target inputs the drawer collects (all optional strings). */
export interface TargetFormValues {
  target_table?: string;
  target_schema?: string;
  target_catalog?: string;
  target_path?: string;
  target_format?: 'csv' | 'parquet';
}

/** Narrow an untyped stored `file_format` to the supported set, else `undefined`
 *  — the suite target is an untyped JSONB bag, so a stray value (e.g. `json`)
 *  must not prefill the Select with an option that doesn't exist. */
export function asFileFormat(value: string | undefined): 'csv' | 'parquet' | undefined {
  return value === 'csv' || value === 'parquet' ? value : undefined;
}

export interface AssembledTarget {
  /** The target to send: `null` = leave targetless (no field was filled). */
  target: RunTarget | null;
  /** Set when the section was started but a required field is missing. */
  error?: { field: keyof TargetFormValues; message: string };
}

const trimmed = (v?: string): string | undefined => {
  const t = v?.trim();
  return t ? t : undefined;
};

/**
 * Turn the raw inputs into a `RunTarget` for the connection's datasource, mirroring
 * the backend `run_target.resolve_target` rules so a saved target is always
 * runnable. All-blank → `null` (a valid targetless suite). Partially filled but
 * missing the datasource's required field → an `error` naming that field, so the
 * UI flags it inline rather than letting the backend 422 on save.
 */
export function assembleTarget(kind: TargetKind, v: TargetFormValues): AssembledTarget {
  if (kind === 'flatfile') {
    const path = trimmed(v.target_path);
    if (!path && !v.target_format) return { target: null };
    if (!path) {
      return {
        target: null,
        error: { field: 'target_path', message: 'Path is required to run this suite.' },
      };
    }
    return { target: { path, ...(v.target_format ? { file_format: v.target_format } : {}) } };
  }

  if (kind === 'sql') {
    const table = trimmed(v.target_table);
    const schema = trimmed(v.target_schema);
    if (!table && !schema) return { target: null };
    if (!table) {
      return {
        target: null,
        error: { field: 'target_table', message: 'Table is required to run this suite.' },
      };
    }
    return { target: { table, ...(schema ? { schema } : {}) } };
  }

  // Unity Catalog: catalog + table required, schema optional.
  const catalog = trimmed(v.target_catalog);
  const table = trimmed(v.target_table);
  const schema = trimmed(v.target_schema);
  if (!catalog && !table && !schema) return { target: null };
  if (!catalog) {
    return {
      target: null,
      error: { field: 'target_catalog', message: 'Catalog is required to run this suite.' },
    };
  }
  if (!table) {
    return {
      target: null,
      error: { field: 'target_table', message: 'Table is required to run this suite.' },
    };
  }
  return { target: { catalog, table, ...(schema ? { schema } : {}) } };
}
