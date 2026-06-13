import type { ConnectionType } from '../../api/connections';
import type { RunTarget } from '../../api/suites';

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

/** The raw target inputs the drawer collects (all optional strings). */
export interface TargetFormValues {
  target_table?: string;
  target_schema?: string;
  target_catalog?: string;
  target_path?: string;
  target_format?: 'csv' | 'parquet';
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
