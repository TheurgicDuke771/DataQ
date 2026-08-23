import type { ConnectionType } from '../../api/connections';
import {
  type RunTarget,
  SAMPLE_STRATEGIES,
  type SampleStrategy,
  targetString,
} from '../../api/suites';

/**
 * A suite's run target (#215) is datasource-shaped: SQL warehouses identify a `table` (+ optional
 * `schema`), Unity Catalog adds a required `catalog`.
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
 * Collapse a stored run target to a one-line summary for read-only display: flat files show their
 * `path` (or, for a batch selector, the configured prefix/pattern.
 */
export function summarizeTarget(target: Record<string, unknown> | null): string | null {
  const base = summarizeTargetBase(target);
  if (base === null) return null;
  const sampling = storedSampling(target);
  return sampling === undefined
    ? base
    : `${base} · sampled: ${sampling.strategy} ${compactRows(sampling.rows)}`;
}

/** `100k` / `1.5M` / `250` — a row cap belongs in a one-line summary at a glance,
 *  not as `100,000` competing with the target name for the reader's attention. */
function compactRows(rows: number): string {
  if (rows >= 1_000_000) return `${Number((rows / 1_000_000).toFixed(1))}M`;
  if (rows >= 1_000) return `${Number((rows / 1_000).toFixed(1))}k`;
  return String(rows);
}

function summarizeTargetBase(target: Record<string, unknown> | null): string | null {
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

/**
 * Whether a stored target is a batch flat-file selector (#1180) — i.e. the same
 * `pattern`-not-`path` signal `summarizeTarget` branches on above.
 */
export function isBatchTarget(target: Record<string, unknown> | null): boolean {
  return Boolean(targetString(target, 'pattern'));
}

/**
 * Datasource types that accept a `sampling` block on their run target (#595) — mirrors the backend
 * `registry.SAMPLING_CAPABLE_TYPES` exactly, and a canary test pins the two together.
 */
export const SAMPLING_CAPABLE_TYPES: ReadonlySet<ConnectionType> = new Set([
  'adls_gen2',
  's3',
  'unity_catalog',
]);

export function supportsSampling(type: ConnectionType | undefined): boolean {
  return type !== undefined && SAMPLING_CAPABLE_TYPES.has(type);
}

/** Bound on a declared sample, mirroring the backend `MAX_SAMPLE_ROWS`. */
export const MAX_SAMPLE_ROWS = 10_000_000;

/** The raw target inputs the drawer collects (all optional strings). */
export interface TargetFormValues {
  target_table?: string;
  target_schema?: string;
  target_catalog?: string;
  target_namespace?: string;
  target_path?: string;
  target_format?: 'csv' | 'parquet';
  /**
   * Flat-file target mode (#1180): `single` is a literal `target_path`; `batch` selects a file at
   * run time via `target_prefix`/`target_pattern`/ `target_strategy`(/`target_batch`).
   */
  target_mode?: 'single' | 'batch';
  target_prefix?: string;
  target_pattern?: string;
  target_strategy?: 'latest' | 'specific';
  target_batch?: string;
  /** Scale-aware execution (#595). Off by default: sampling changes what a
   *  verdict *means*, so it is opt-in per suite and never inherited. */
  sampling_enabled?: boolean;
  sampling_strategy?: SampleStrategy;
  sampling_rows?: number | null;
  /** `random` only — the backend 422s a seed on `head`, since a head sample
   *  always reads the first rows in storage order and cannot be seeded. */
  sampling_seed?: number | null;
}

/** Narrow an untyped value to one of a closed set, else `undefined`. */
function asOneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return allowed.includes(value as T) ? (value as T) : undefined;
}

/** The stored `sampling` block, narrowed field by field for prefill. */
export function targetSampling(
  target: Record<string, unknown> | null | undefined,
): { strategy?: SampleStrategy; rows?: number; seed?: number } | undefined {
  const raw = target?.sampling;
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return undefined;
  const bag = raw as Record<string, unknown>;
  return {
    strategy: asOneOf(bag.strategy, SAMPLE_STRATEGIES),
    rows: typeof bag.rows === 'number' ? bag.rows : undefined,
    seed: typeof bag.seed === 'number' ? bag.seed : undefined,
  };
}

/**
 * Narrow an untyped stored `file_format` to the supported set, else `undefined` — the suite target
 * is an untyped JSONB bag, so a stray value (e.g.
 */
export function asFileFormat(value: unknown): 'csv' | 'parquet' | undefined {
  return asOneOf(value, FILE_FORMATS);
}

/**
 * Narrow an untyped stored `strategy` to the supported set, else `undefined` — same reasoning as
 * `asFileFormat`: the suite target is an untyped JSONB bag.
 */
export function asBatchStrategy(value: unknown): 'latest' | 'specific' | undefined {
  return asOneOf(value, BATCH_STRATEGIES);
}

const FILE_FORMATS = ['csv', 'parquet'] as const;
const BATCH_STRATEGIES = ['latest', 'specific'] as const;

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

/** Light "does this pattern look like it has a capture group" heuristic — NOT a full regex parse. */
export function hasCaptureGroup(pattern: string): boolean {
  const withoutEscapedParens = pattern.replace(/\\[()]/g, '');
  return /\((?!\?(?:[:=!]|<[=!]))/.test(withoutEscapedParens);
}

/**
 * Assemble a flat-file *batch* target (#1180): `target_pattern` is required, `target_strategy`
 * defaults to `latest` (mirroring the backend's own `target.get("strategy", "latest")`).
 */
function assembleBatchTarget(v: TargetFormValues): AssembledTarget {
  const prefix = trimmed(v.target_prefix);
  const pattern = trimmed(v.target_pattern);
  const strategy = v.target_strategy ?? 'latest';
  const batch = trimmed(v.target_batch);
  // An explicit non-default strategy pick counts as "the section was started" too, not just a
  // filled prefix/pattern/batch.
  if (!prefix && !pattern && !batch && strategy === 'latest') return { target: null };
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

/** Fold the optional `sampling` block (#595) onto an assembled target. */
function withSampling(
  assembled: AssembledTarget,
  v: TargetFormValues,
  { connType, stored }: AssembleOptions,
): AssembledTarget {
  if (assembled.error) return assembled;
  if (v.sampling_enabled === undefined) {
    // The section never mounted — preserve whatever the suite already had.
    return stored && assembled.target
      ? { target: { ...assembled.target, sampling: stored } }
      : assembled;
  }
  if (!v.sampling_enabled) return assembled;
  if (!supportsSampling(connType)) {
    return {
      target: null,
      error: {
        field: 'sampling_enabled',
        message:
          'This datasource runs checks by pushdown and never loads rows into the worker, ' +
          'so sampling would change nothing.',
      },
    };
  }
  if (assembled.target === null) {
    return {
      target: null,
      error: {
        field: 'sampling_enabled',
        message: 'Sampling bounds a run target — set the target above first.',
      },
    };
  }
  const rows = v.sampling_rows;
  if (typeof rows !== 'number' || !Number.isInteger(rows) || rows < 1 || rows > MAX_SAMPLE_ROWS) {
    return {
      target: null,
      error: {
        field: 'sampling_rows',
        message: `A whole number of rows between 1 and ${MAX_SAMPLE_ROWS.toLocaleString()} is required.`,
      },
    };
  }
  const strategy: SampleStrategy = v.sampling_strategy ?? 'head';
  // A seed only means something for `random` — the backend 422s it on `head` rather than let an
  // author believe a head sample is seeded-random.
  const seed =
    strategy === 'random' && typeof v.sampling_seed === 'number' ? v.sampling_seed : null;
  return {
    target: {
      ...assembled.target,
      sampling: { strategy, rows, ...(seed !== null ? { seed } : {}) },
    },
  };
}

/**
 * Turn the raw inputs into a `RunTarget` for the connection's datasource, mirroring the backend
 * `run_target.resolve_target` rules so a saved target is always runnable.
 */
export function assembleTarget(
  kind: TargetKind,
  v: TargetFormValues,
  opts: AssembleOptions = {},
): AssembledTarget {
  return withSampling(assembleBaseTarget(kind, v), v, opts);
}

/**
 * The stored block in the shape the API round-trips, or `undefined` when the suite has none *or*
 * what it has could not be saved anyway.
 */
export function storedSampling(
  target: Record<string, unknown> | null | undefined,
): RunTarget['sampling'] | undefined {
  const sampling = targetSampling(target);
  if (sampling?.strategy === undefined || sampling.rows === undefined) return undefined;
  return {
    strategy: sampling.strategy,
    rows: sampling.rows,
    ...(sampling.seed !== undefined ? { seed: sampling.seed } : {}),
  };
}

export interface AssembleOptions {
  /** The active connection's datasource type — gates the `sampling` block. */
  connType?: ConnectionType;
  /** The suite's stored `sampling` block, if it has one. Preserved when the
   *  sampling section was not rendered, so an unrelated edit cannot delete it. */
  stored?: RunTarget['sampling'];
}

function assembleBaseTarget(kind: TargetKind, v: TargetFormValues): AssembledTarget {
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
