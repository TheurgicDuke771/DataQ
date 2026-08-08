import type { ConnectionType } from '../../api/connections';
import { type RunTarget, targetString } from '../../api/suites';

/**
 * A suite's run target (#215) is datasource-shaped: SQL warehouses identify a
 * `table` (+ optional `schema`), Unity Catalog adds a required `catalog`, and
 * flat-file stores (ADLS / S3) identify a `path` (+ optional `file_format`).
 * `targetKind` collapses the six datasource types to the three input shapes the
 * editor renders; orchestration types never reach here (they can't back a suite).
 */
export type TargetKind = 'sql' | 'uc' | 'flatfile' | 'iceberg';

export function targetKind(type: ConnectionType): TargetKind | null {
  switch (type) {
    case 'snowflake':
      return 'sql';
    case 'unity_catalog':
      return 'uc';
    case 'iceberg':
      return 'iceberg';
    case 'adls_gen2':
    case 's3':
      return 'flatfile';
    default:
      return null; // adf / airflow / dbt — not a datasource
  }
}

/**
 * Collapse a stored run target to a one-line summary for read-only display:
 * flat files show their `path` (or, for a batch selector, the configured
 * prefix/pattern — NOT a resolved file; there's no cheap way to preview the
 * actual match without a dedicated backend endpoint, #1180); SQL / Unity
 * Catalog show the dotted `catalog.schema.table` (only the parts present).
 * Returns `null` for a targetless (not-yet-runnable) suite. Lives here next to
 * the other datasource-target-shape logic so a new target field has one owner.
 */
export function summarizeTarget(target: Record<string, unknown> | null): string | null {
  if (!target) return null;
  const path = targetString(target, 'path');
  if (path) return path;
  const pattern = targetString(target, 'pattern');
  if (pattern) {
    const prefix = targetString(target, 'prefix');
    return `${prefix ?? ''}${pattern} (${targetString(target, 'strategy') ?? 'latest'})`;
  }
  const parts = [
    targetString(target, 'catalog'),
    // Iceberg addresses `namespace.table`; namespace sits where catalog/schema do.
    targetString(target, 'namespace'),
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
  target_namespace?: string;
  target_path?: string;
  target_format?: 'csv' | 'parquet';
  /** Flat-file target mode (#1180): `single` is a literal `target_path`; `batch`
   *  selects a file at run time via `target_prefix`/`target_pattern`/
   *  `target_strategy`(/`target_batch`) — mirrors the backend `BatchSpec`. */
  target_mode?: 'single' | 'batch';
  target_prefix?: string;
  target_pattern?: string;
  target_strategy?: 'latest' | 'specific';
  target_batch?: string;
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
 * Light "does this pattern look like it has a capture group" heuristic — NOT a
 * full regex parse. Python and JS regex syntax diverge (e.g. lookbehind
 * variants, possessive quantifiers), so `new RegExp(pattern)` compiling
 * cleanly is neither necessary nor sufficient proof it's valid Python regex;
 * the backend's own `re.compile` + `.groups` count (`_batch_spec` in
 * registry.py) is authoritative and is what actually runs at save time. This
 * only catches the common authoring mistake — a `specific`-strategy pattern
 * with no parenthesised group at all — before it round-trips as a 422.
 * Treats a leading `(?` as non-capturing UNLESS it's a named group
 * (`(?P<name>` Python-style or `(?<name>` JS-style, but not the `(?<=`/`(?<!`
 * lookbehind forms), matching Python's own capture-counting rules.
 */
export function hasCaptureGroup(pattern: string): boolean {
  const withoutEscapedParens = pattern.replace(/\\[()]/g, '');
  return /\((?!\?(?:[:=!]|<[=!]))/.test(withoutEscapedParens);
}

/**
 * Assemble a flat-file *batch* target (#1180): `target_pattern` is required,
 * `target_strategy` defaults to `latest` (mirroring the backend's own
 * `target.get("strategy", "latest")`), and `specific` additionally requires a
 * non-empty `target_batch` plus a pattern that looks like it captures a batch
 * key — the same two checks `_batch_spec` makes server-side.
 */
function assembleBatchTarget(v: TargetFormValues): AssembledTarget {
  const prefix = trimmed(v.target_prefix);
  const pattern = trimmed(v.target_pattern);
  const strategy = v.target_strategy ?? 'latest';
  const batch = trimmed(v.target_batch);
  if (!prefix && !pattern && !batch) return { target: null };
  if (!pattern) {
    return {
      target: null,
      error: { field: 'target_pattern', message: 'Pattern is required to run this suite.' },
    };
  }
  if (strategy === 'specific') {
    if (!batch) {
      return {
        target: null,
        error: {
          field: 'target_batch',
          message: "Strategy 'specific' requires a batch key to select.",
        },
      };
    }
    if (!hasCaptureGroup(pattern)) {
      return {
        target: null,
        error: {
          field: 'target_pattern',
          message:
            "Strategy 'specific' needs a capture group in the pattern to extract the batch " +
            'key, e.g. `orders_([a-z_]+)\\.csv`.',
        },
      };
    }
  }
  return {
    target: {
      pattern,
      strategy,
      ...(prefix ? { prefix } : {}),
      ...(strategy === 'specific' ? { batch } : {}),
    },
  };
}

/**
 * Turn the raw inputs into a `RunTarget` for the connection's datasource, mirroring
 * the backend `run_target.resolve_target` rules so a saved target is always
 * runnable. All-blank → `null` (a valid targetless suite). Partially filled but
 * missing the datasource's required field → an `error` naming that field, so the
 * UI flags it inline rather than letting the backend 422 on save.
 */
export function assembleTarget(kind: TargetKind, v: TargetFormValues): AssembledTarget {
  if (kind === 'flatfile') {
    if (v.target_mode === 'batch') return assembleBatchTarget(v);
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

  if (kind === 'iceberg') {
    // Iceberg: table required, namespace optional (folded to `namespace.table`
    // by the backend run-target resolver, mirroring resolve_target).
    const table = trimmed(v.target_table);
    const namespace = trimmed(v.target_namespace);
    if (!table && !namespace) return { target: null };
    if (!table) {
      return {
        target: null,
        error: { field: 'target_table', message: 'Table is required to run this suite.' },
      };
    }
    return { target: { table, ...(namespace ? { namespace } : {}) } };
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
