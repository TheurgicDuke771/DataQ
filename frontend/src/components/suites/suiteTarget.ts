import type { ConnectionType } from '../../api/connections';
import {
  type RunTarget,
  SAMPLE_STRATEGIES,
  type SampleStrategy,
  targetString,
} from '../../api/suites';

/**
 * A suite's run target (#215) is datasource-shaped: SQL warehouses identify a
 * `table` (+ optional `schema`), Unity Catalog adds a required `catalog`,
 * flat-file stores (ADLS / S3) identify a `path` (+ optional `file_format`),
 * and Iceberg identifies a `namespace.table` pair. `targetKind` collapses the
 * five datasource types to the four input shapes the editor renders;
 * orchestration types never reach here (they can't back a suite).
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
 * prefix/pattern — NOT a resolved file: resolving one means listing the store,
 * which is `GET /suites/{id}/batch-preview` (#1193, `BatchPreviewHint`), far too
 * expensive for a read-only summary rendered per row); SQL / Unity Catalog show
 * the dotted `catalog.schema.table` (only the parts present).
 * Returns `null` for a targetless (not-yet-runnable) suite. Lives here next to
 * the other datasource-target-shape logic so a new target field has one owner.
 *
 * A **sampled** target is annotated (`… · sampled: head 100k`), following the
 * batch selector's precedent of saying what the summary is rather than showing a
 * bare name. Without it the Run Now confirmation and the Suites list render a
 * sampled and an unsampled suite identically — and "run this" is exactly the
 * moment to know the run will read 100k rows of 5M.
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
 * Whether a stored target is a batch flat-file selector (#1180) — i.e. the
 * same `pattern`-not-`path` signal `summarizeTarget` branches on above.
 * Exported so callers that need to flag "this is configured, not resolved"
 * (#1205) share the one signal instead of re-deriving it independently,
 * which would let the two drift out of sync if the batch-detection rule
 * ever changes.
 */
export function isBatchTarget(target: Record<string, unknown> | null): boolean {
  return Boolean(targetString(target, 'pattern'));
}

/**
 * Datasource types that accept a `sampling` block on their run target (#595) —
 * mirrors the backend `registry.SAMPLING_CAPABLE_TYPES` exactly, and a canary
 * test pins the two together.
 *
 * The absences are deliberate, not gaps. Snowflake pushes every expectation down
 * as SQL and never materialises rows in the worker, so a sample there would
 * change nothing while stamping "sampled" on every result; Iceberg's sampled read
 * is not built. The backend refuses the block on both with a **422 at save time**
 * rather than ignoring it, so hiding the control here is the same decision made
 * one layer earlier — the editor must not offer a knob whose only effect is a
 * save error.
 */
export const SAMPLING_CAPABLE_TYPES: ReadonlySet<ConnectionType> = new Set([
  'adls_gen2',
  's3',
  'unity_catalog',
]);

export function supportsSampling(type: ConnectionType | undefined): boolean {
  return type !== undefined && SAMPLING_CAPABLE_TYPES.has(type);
}

/** Bound on a declared sample, mirroring the backend `MAX_SAMPLE_ROWS`. Not a
 *  memory guardrail (that is `RUN_MAX_SCAN_ROWS`, applied per datasource) — this
 *  only keeps an obviously-nonsensical spec out of the stored target. */
export const MAX_SAMPLE_ROWS = 10_000_000;

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
  /** Scale-aware execution (#595). Off by default: sampling changes what a
   *  verdict *means*, so it is opt-in per suite and never inherited. */
  sampling_enabled?: boolean;
  sampling_strategy?: SampleStrategy;
  sampling_rows?: number | null;
  /** `random` only — the backend 422s a seed on `head`, since a head sample
   *  always reads the first rows in storage order and cannot be seeded. */
  sampling_seed?: number | null;
}

/**
 * Narrow an untyped value to one of a closed set, else `undefined`.
 *
 * The suite target is a JSONB bag, so every stored value reaching a Select has to
 * be narrowed or a stray one (a hand-edited row, an older schema) prefills an
 * option that does not exist. There were three hand-rolled copies of this, and
 * they had already drifted in signature (`string | undefined` vs `unknown`) — the
 * kind of drift that decides whether a non-string value is narrowed or crashes.
 */
function asOneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return allowed.includes(value as T) ? (value as T) : undefined;
}

/** The stored `sampling` block, narrowed field by field for prefill.
 *
 * Returns the typed shape rather than a bag, so callers read `.strategy` and
 * `.rows` directly instead of each re-narrowing an `unknown` — which is what the
 * separate `asSampleStrategy` / `samplingNumber` helpers made them do, and what
 * put two more narrowers on the public surface.
 */
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

/** Narrow an untyped stored `file_format` to the supported set, else `undefined`
 *  — the suite target is an untyped JSONB bag, so a stray value (e.g. `json`)
 *  must not prefill the Select with an option that doesn't exist. */
export function asFileFormat(value: unknown): 'csv' | 'parquet' | undefined {
  return asOneOf(value, FILE_FORMATS);
}

/** Narrow an untyped stored `strategy` to the supported set, else `undefined`
 *  — same reasoning as `asFileFormat`: the suite target is an untyped JSONB
 *  bag, so a stray value must not prefill the Strategy Select with an option
 *  that doesn't exist. */
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
  // An explicit non-default strategy pick counts as "the section was started"
  // too, not just a filled prefix/pattern/batch — otherwise picking 'specific'
  // and leaving pattern/batch blank silently discards to a targetless suite
  // instead of flagging the missing pattern, unlike single-file mode (which
  // already treats a format-only fill the same way).
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

/**
 * Fold the optional `sampling` block (#595) onto an assembled target.
 *
 * **The carry-forward is the important part.** A suite's target is replaced
 * wholesale on save, and `validateFields()` returns only *registered* fields — so
 * when the sampling section is not mounted, `sampling_enabled` is `undefined` and
 * this function has no way to distinguish "the author turned sampling off" from
 * "the author never saw the control". Treating the second as the first deletes a
 * stored row cap on a save that only touched the description: no error, no
 * warning, and the nightly suite quietly reverts to a full scan — the OOM this
 * whole feature exists to prevent.
 *
 * So `undefined` carries `stored` forward and only an explicit `false` clears the
 * block. That kills the data-loss class **independently of list drift**, which
 * matters because nothing mechanically pins `SAMPLING_CAPABLE_TYPES` to the
 * backend's copy — a canary test can only assert the frontend list against
 * itself.
 *
 * Otherwise it refuses rather than drops, mirroring the backend's own
 * refuse-don't-ignore stance:
 *
 * * a spec on a datasource that cannot sample → error. **Not reachable from
 *   `SuiteForm`**, which derives both the control's visibility and the `connType`
 *   it passes from the same `supportsSampling` call, so the two cannot disagree.
 *   It guards the *exported* function against a second caller that collects
 *   sampling input without that coupling, and is exercised directly by its unit
 *   test — an honest description of a defensive branch, rather than the claim it
 *   carried before, which implied `SuiteForm` could trip it.
 * * a spec with no target to apply it to → error (`{sampling: …}` alone is not a
 *   runnable target and the backend would 422 on save);
 * * a spec with no row cap → error (`rows` is the whole declaration).
 */
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
  // A seed only means something for `random` — the backend 422s it on `head`
  // rather than let an author believe a head sample is seeded-random, so the
  // form must not send one just because the field still holds a stale value
  // from before the strategy was switched.
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
 * Turn the raw inputs into a `RunTarget` for the connection's datasource, mirroring
 * the backend `run_target.resolve_target` rules so a saved target is always
 * runnable. All-blank → `null` (a valid targetless suite). Partially filled but
 * missing the datasource's required field → an `error` naming that field, so the
 * UI flags it inline rather than letting the backend 422 on save.
 *
 * `opts.connType` gates the optional `sampling` block (accepted on some
 * datasources, refused on others) and `opts.stored` is the suite's existing
 * block, carried forward when the author was never shown the control — see
 * `withSampling` for both, and for why the carry-forward is the load-bearing one.
 */
export function assembleTarget(
  kind: TargetKind,
  v: TargetFormValues,
  opts: AssembleOptions = {},
): AssembledTarget {
  return withSampling(assembleBaseTarget(kind, v), v, opts);
}

/**
 * The stored block in the shape the API round-trips, or `undefined` when the
 * suite has none *or* what it has could not be saved anyway.
 *
 * Separate from `targetSampling` (which narrows field by field, for prefilling
 * controls) because the carry-forward has a different job: it re-sends a block
 * the backend already accepted. Reconstructing it from partially-narrowed fields
 * could turn a malformed stored block into a *differently* malformed one; here a
 * block missing either required field is simply not carried, so the save proceeds
 * without it rather than with a guess.
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
