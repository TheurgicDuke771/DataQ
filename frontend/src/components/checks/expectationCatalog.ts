/** Curated catalog of GX expectations the check editor exposes in v1. */

import {
  DATASOURCE_CATEGORY,
  isFileDatasource,
  isSqlQueryable,
  runsSqlBatch,
  supportsMonitors,
  type ConnectionType,
} from '../../api/connections';
import { CUSTOM_SQL_EXPECTATION_TYPE, CUSTOM_SQL_QUERY_KEY } from './customSql';

export type ConfigFieldType = 'string' | 'number' | 'list' | 'sql' | 'select' | 'boolean';

/** The check `kind` (ADR 0012). `expectation` (incl. custom-SQL) is GX; the
 *  monitor kinds run a scalar SQL aggregate instead. Sent to the backend. */
export type CheckKind =
  'expectation' | 'freshness' | 'volume' | 'schema_drift' | 'anomaly' | 'comparison';

/**
 * Expectation categories — the GX-Cloud-style classification the check editor groups by. v1 ships
 * value-level GX expectations + custom-SQL (ADR 0019) + the freshness/volume monitor kinds (ADR 00
 */
export type ExpectationCategory =
  | 'Column values'
  | 'Table shape'
  | 'Freshness'
  | 'Volume'
  | 'Schema'
  | 'Anomaly'
  | 'Custom SQL'
  | 'Comparison'
  | 'Snowflake DMF';

/** Check engine (ADR 0036). */
export type CheckEngine = 'gx' | 'dmf';

export const EXPECTATION_CATEGORIES: ExpectationCategory[] = [
  'Column values',
  'Table shape',
  'Freshness',
  'Volume',
  'Schema',
  'Snowflake DMF',
  'Anomaly',
  'Custom SQL',
  'Comparison',
];

/** The canonical comparison expectation type (ADR 0015; `comparison:columns` stays reserved). */
export const COMPARISON_EXPECTATION_TYPE = 'comparison:records';
export const COMPARISON_COLUMNS_EXPECTATION_TYPE = 'comparison:columns';

/**
 * Monitor categories (ADR 0012) — gated by `supportsMonitors` (below), which is BROADER than SQL-
 * queryable: Iceberg and flat files (adls_gen2/s3) also offer them.
 */
export const MONITOR_CATEGORIES: ExpectationCategory[] = ['Freshness', 'Volume'];

/** The seven canonical DQ dimensions (ADR 0038) — the *semantic quality aspect* a check measures. */
export const DQ_DIMENSIONS = [
  'accuracy',
  'completeness',
  'consistency',
  'integrity',
  'timeliness',
  'uniqueness',
  'validity',
] as const;

export type DqDimension = (typeof DQ_DIMENSIONS)[number];

/** One-line "what does this dimension mean" help for the editor's select. */
export const DQ_DIMENSION_HELP: Record<DqDimension, string> = {
  accuracy: 'Does the data match reality / a trusted source?',
  completeness: 'Is all the expected data present?',
  consistency: 'Do related datasets agree with each other?',
  integrity: 'Do relationships between datasets hold?',
  timeliness: 'Is the data recent enough?',
  uniqueness: 'Are there unexpected duplicates?',
  validity: 'Does the data conform to its rules and formats?',
};

export interface ConfigField {
  /** Key in the GX `config` kwargs object. */
  name: string;
  label: string;
  type: ConfigFieldType;
  optional?: boolean;
  help?: string;
  /**
   * Inline STATIC bounds for a `number` field (the backend is authoritative — e.g. anomaly's
   * `window` is 3-90; #593).
   */
  min?: number;
  max?: number;
  /** Increment for a `number` field's stepper; the antd default of 1 is unusable on a 0–1 range. */
  step?: number;
  /** Value/label options for a `select` field. */
  options?: { value: string; label: string }[];
  /**
   * For a `number` field: an ADDITIONAL dynamic ceiling — this field's max is also capped by a
   * SIBLING config field's live value (read off the same `configValues` `showWhen` reads).
   */
  maxFrom?: string;
  /** Friendly name of the `maxFrom` field, for the validation message. */
  maxFromLabel?: string;
  /**
   * CREATE-mode pre-filled value, mirroring the backend's own default (e.g. anomaly's `window`
   * defaults to 14 server-side) so an untouched field submits the same value the backend would
   */
  defaultValue?: unknown;
  /**
   * Show (and submit) this field only when a SIBLING config field equals a given value — a generic
   * conditional-field mechanism.
   */
  showWhen?: { field: string; equals: unknown };
}

/**
 * True when `field` should render/submit given the current sibling config values (see
 * `ConfigField.showWhen`).
 */
export function fieldVisible(
  field: ConfigField,
  configValues: Record<string, unknown> | undefined,
): boolean {
  if (!field.showWhen) return true;
  return (configValues ?? {})[field.showWhen.field] === field.showWhen.equals;
}

/** Severity-threshold semantics for a monitor kind (ADR 0012/0016). */
export interface MonitorThresholdSpec {
  /** What the warn/fail/critical numbers mean for this kind. */
  help: string;
  /** Upper bound on the inputs (omit = unbounded, e.g. freshness age-hours). */
  max?: number;
  /** Require a fail or critical threshold (freshness has no in-config bound, so
   *  without one it can never fail — the #426 silent-green guard). */
  requireFailOrCritical?: boolean;
}

export interface ExpectationSpec {
  /** snake_case GX expectation type (or `monitor:<kind>`) sent to the backend. */
  type: string;
  /** Check kind (ADR 0012); defaults to `expectation` when omitted. */
  kind?: CheckKind;
  label: string;
  description: string;
  category: ExpectationCategory;
  /** The DQ dimension this check type measures (ADR 0038) — the editor's derived default. */
  dimension?: DqDimension;
  fields: ConfigField[];
  /** Present for monitor kinds — drives the threshold block's help/bounds/required. */
  thresholds?: MonitorThresholdSpec;
  /** Engine (ADR 0036); omitted = `gx`. Fixed `dmf` for the `dmf:*` types below. */
  engine?: CheckEngine;
  /** `dmf:unique_count` degrades downward — the backend rejects any threshold on it. */
  noThresholds?: boolean;
  /**
   * GX has no SqlAlchemy metric provider for this type, so it errors on a SQL batch. Mirrors the
   * backend's `gx_runner.DATAFRAME_ONLY_EXPECTATION_TYPES`, which 422s it at author time — this
   * flag only keeps it out of the picker.
   */
  dataframeOnly?: boolean;
}

/** Freshness's type — same string for both engines, so it's a choice rather than a second entry. */
export const FRESHNESS_EXPECTATION_TYPE = 'monitor:freshness';

/** Mirrors `engines_for('snowflake')` (`backend/app/datasources/engines.py`). */
export function offersDmfEngine(connectionType: ConnectionType | undefined): boolean {
  return connectionType === 'snowflake';
}

/** True only for Freshness on a Snowflake connection — the one spec with an engine choice. */
export function showEngineChoiceFor(
  spec: ExpectationSpec | undefined,
  connectionType: ConnectionType | undefined,
): boolean {
  return spec?.type === FRESHNESS_EXPECTATION_TYPE && offersDmfEngine(connectionType);
}

/** The engine a check actually runs on. */
export function effectiveEngineFor(
  spec: ExpectationSpec | undefined,
  connectionType: ConnectionType | undefined,
  engineChoice: string | undefined,
): CheckEngine {
  if (spec?.engine) return spec.engine;
  if (showEngineChoiceFor(spec, connectionType)) return (engineChoice as CheckEngine) ?? 'gx';
  return 'gx';
}

const COLUMN: ConfigField = { name: 'column', label: 'Column', type: 'string' };

/** GX's row-wise tolerance kwarg — a fraction, not a percentage. */
export const MOSTLY_FIELD_NAME = 'mostly';

const MOSTLY_HELP =
  'Optional tolerance: the fraction of rows that must conform for the check to succeed — e.g. 0.95 = pass if ≥95% of rows conform. Leave blank to require every row. Severity thresholds still band the FULL unexpected-%, so a threshold below this tolerance can still warn/fail a run GX itself passed.';

/**
 * The `mostly` tolerance, offered on every row-wise expectation GX accepts it on. GX's own floor
 * is 0, which succeeds unconditionally forever — a check that can never fail is the #426
 * silent-green class, so the editor stops one step above it.
 */
const MOSTLY: ConfigField = {
  name: MOSTLY_FIELD_NAME,
  label: 'Tolerance',
  type: 'number',
  optional: true,
  min: 0.01,
  max: 1,
  step: 0.01,
  help: MOSTLY_HELP,
};

/**
 * The type checks reach `mostly` only down GX's row-wise fallback; the whole-column dtype compare
 * it prefers is all-or-nothing, so the tolerance is inert there.
 */
const MOSTLY_TYPE_CHECK: ConfigField = {
  ...MOSTLY,
  help: `${MOSTLY_HELP} On a type check it applies only when GX falls back to its row-wise compare (see the Type hint above), not to the whole-column dtype compare.`,
};

/**
 * `type_` config-field name for `expect_column_values_to_be_of_type` — GX's own kwarg (trailing
 * underscore to dodge shadowing the Python builtin).
 */
export const TYPE_FIELD_NAME = 'type_';

const TYPE_FIELD_DEFAULT_HELP =
  'The exact type string GX compares against — it depends on the datasource’s execution engine (SQL dialect type vs pandas dtype), not the connection’s advertised column type. Pick a suite with a known connection to see a tailored hint.';

// GX's `expect_column_values_to_be_of_type` validates against a *different* type vocabulary
// depending on which execution engine the runner builds its GX batch on.
const SQL_ENGINE_TYPE_HINT =
  'Use the engine’s fully-qualified type exactly as the dialect reports it — e.g. Snowflake NUMBER is `DECIMAL(38, 0)`. Run a dry-run: the failing result’s observed_value shows the exact expected string.';

const DATAFRAME_ENGINE_TYPE_HINT =
  'Compares pandas dtypes or Python value type names — numerics report `int64`/`float64` (integer columns containing NULLs report `float64`); string columns on Unity Catalog and CSV reads are `object` dtype, so `object` or `str` both pass, while Parquet/Iceberg reads are Arrow-backed and can report different names. Dry-run to calibrate: a failing result’s observed_value shows the expected dtype — but if Observed shows “—”, your guess fell to GX’s row-wise compare; use `object` or a Python value type name (full cheat-sheet in the check-authoring docs).';

/** `type_list` config-field name — `to_be_of_type`'s sibling, same type vocabulary (#1509). */
export const TYPE_LIST_FIELD_NAME = 'type_list';

/** The type-vocabulary fields, all of which take the datasource-tailored `typeFieldHint`. */
export const TYPE_FIELD_NAMES: string[] = [TYPE_FIELD_NAME, TYPE_LIST_FIELD_NAME];

/** Datasource-tailored help for the `type_` field (issue #768. */
export function typeFieldHint(connectionType: ConnectionType | undefined): string {
  if (!connectionType || !DATASOURCE_CATEGORY[connectionType]) return TYPE_FIELD_DEFAULT_HELP;
  return connectionType === 'snowflake' ? SQL_ENGINE_TYPE_HINT : DATAFRAME_ENGINE_TYPE_HINT;
}

/**
 * The distinct-value set relations compare a SET, so GX reports no `unexpected_percent` — the
 * scalar ADR 0016 bands (`services/severity.extract_metric`). Say so where the bands are entered
 * rather than leaving an input that silently does nothing.
 */
const SET_RELATION_THRESHOLDS: MonitorThresholdSpec = {
  help: 'This check compares the column’s distinct-value SET, so GX reports no unexpected-% for the bands to read — thresholds set here are ignored and the result stays a binary pass/fail.',
};

export const EXPECTATION_CATALOG: ExpectationSpec[] = [
  {
    type: 'expect_column_values_to_not_be_null',
    dimension: 'completeness',
    label: 'Column values not null',
    description: 'Every value in the column is non-null.',
    category: 'Column values',
    fields: [COLUMN, MOSTLY],
  },
  {
    type: 'expect_column_values_to_be_unique',
    dimension: 'uniqueness',
    label: 'Column values unique',
    description: 'Values in the column are distinct (no duplicates).',
    category: 'Column values',
    fields: [COLUMN, MOSTLY],
  },
  {
    type: 'expect_column_values_to_be_between',
    dimension: 'validity',
    label: 'Column values in range',
    description: 'Numeric values fall within [min, max].',
    category: 'Column values',
    fields: [
      COLUMN,
      { name: 'min_value', label: 'Minimum', type: 'number', optional: true },
      { name: 'max_value', label: 'Maximum', type: 'number', optional: true },
      MOSTLY,
    ],
  },
  {
    type: 'expect_column_values_to_be_in_set',
    dimension: 'validity',
    label: 'Column values in set',
    description: 'Every value is one of an allowed set.',
    category: 'Column values',
    fields: [
      COLUMN,
      {
        name: 'value_set',
        label: 'Allowed values',
        type: 'list',
        help: 'Comma-separated list of permitted values.',
      },
      MOSTLY,
    ],
  },
  {
    type: 'expect_column_value_lengths_to_be_between',
    dimension: 'validity',
    label: 'Column value lengths in range',
    description: 'String lengths fall within [min, max].',
    category: 'Column values',
    fields: [
      COLUMN,
      { name: 'min_value', label: 'Min length', type: 'number', optional: true },
      { name: 'max_value', label: 'Max length', type: 'number', optional: true },
      MOSTLY,
    ],
  },
  {
    type: 'expect_column_values_to_match_regex',
    dimension: 'validity',
    label: 'Column values match regex',
    description: 'Every value matches the given regular expression.',
    category: 'Column values',
    fields: [COLUMN, { name: 'regex', label: 'Regex', type: 'string' }, MOSTLY],
  },
  {
    type: 'expect_column_values_to_be_of_type',
    dimension: 'validity',
    label: 'Column values are of type',
    description: 'Every value in the column matches the given data type.',
    category: 'Column values',
    fields: [
      COLUMN,
      {
        name: TYPE_FIELD_NAME,
        label: 'Type',
        type: 'string',
        help: TYPE_FIELD_DEFAULT_HELP,
      },
      MOSTLY_TYPE_CHECK,
    ],
  },
  {
    type: 'expect_column_values_to_be_in_type_list',
    dimension: 'validity',
    label: 'Column values are of one of several types',
    description:
      'Every value in the column matches at least one of the given data types — the tolerant sibling of “Column values are of type”, for a column whose type legitimately varies by datasource or load.',
    category: 'Column values',
    fields: [
      COLUMN,
      {
        name: TYPE_LIST_FIELD_NAME,
        label: 'Types',
        type: 'list',
        help: TYPE_FIELD_DEFAULT_HELP,
      },
      MOSTLY_TYPE_CHECK,
    ],
  },
  {
    type: 'expect_compound_columns_to_be_unique',
    dimension: 'uniqueness',
    label: 'Compound columns unique',
    description:
      'The COMBINATION of values across the listed columns is distinct on every row — a multi-column primary or business key. Each column on its own may repeat freely.',
    category: 'Column values',
    fields: [
      {
        name: 'column_list',
        label: 'Columns',
        type: 'list',
        help: 'Comma-separated columns forming the compound key.',
      },
      MOSTLY,
    ],
  },
  {
    type: 'expect_column_pair_values_a_to_be_greater_than_b',
    dimension: 'validity',
    label: 'Column A greater than column B',
    description:
      'Row by row, column A is greater than column B — e.g. ended_at > started_at, or total >= discount.',
    category: 'Column values',
    fields: [
      { name: 'column_A', label: 'Column A', type: 'string' },
      { name: 'column_B', label: 'Column B', type: 'string' },
      {
        name: 'or_equal',
        label: 'Allow equal',
        type: 'boolean',
        optional: true,
        help: 'Accept rows where A equals B (>= instead of >).',
      },
      MOSTLY,
    ],
  },
  {
    type: 'expect_multicolumn_sum_to_equal',
    dimension: 'validity',
    label: 'Columns sum to a total',
    description:
      'Row by row, the listed columns add up to the given total — e.g. subtotal + tax + shipping = total.',
    category: 'Column values',
    fields: [
      {
        name: 'column_list',
        label: 'Columns',
        type: 'list',
        help: 'Comma-separated columns whose per-row values are summed.',
      },
      { name: 'sum_total', label: 'Expected sum', type: 'number' },
      MOSTLY,
    ],
  },
  {
    type: 'expect_column_distinct_values_to_be_in_set',
    dimension: 'validity',
    label: 'Column distinct values in set',
    description:
      'Every DISTINCT value present in the column is one of an allowed set — reports WHICH unexpected values exist rather than how many rows carry them. Use “Column values in set” when you care about the row count.',
    category: 'Column values',
    fields: [
      COLUMN,
      {
        name: 'value_set',
        label: 'Allowed values',
        type: 'list',
        help: 'Comma-separated list of permitted values.',
      },
    ],
    thresholds: SET_RELATION_THRESHOLDS,
  },
  {
    type: 'expect_column_distinct_values_to_contain_set',
    dimension: 'completeness',
    label: 'Column distinct values contain set',
    description:
      'Every value in the given set appears at least once in the column — catches a category that stopped arriving. The column may also contain other values.',
    category: 'Column values',
    fields: [
      COLUMN,
      {
        name: 'value_set',
        label: 'Required values',
        type: 'list',
        help: 'Comma-separated list of values that must each appear at least once.',
      },
    ],
    thresholds: SET_RELATION_THRESHOLDS,
  },
  {
    type: 'expect_column_values_to_match_strftime_format',
    dimension: 'validity',
    dataframeOnly: true,
    label: 'Column values match a date format',
    description:
      'Every value parses under the given strftime format — for a date or timestamp stored as text. Not offered on Snowflake: Great Expectations implements this one only for dataframe batches, so a SQL warehouse would error on every run. Use a custom-SQL check there.',
    category: 'Column values',
    fields: [
      COLUMN,
      {
        name: 'strftime_format',
        label: 'Date format',
        type: 'string',
        help: 'Python strftime format, e.g. %Y-%m-%d or %Y-%m-%dT%H:%M:%S.',
      },
      MOSTLY,
    ],
  },
  {
    type: 'expect_table_row_count_to_be_between',
    dimension: 'completeness',
    label: 'Table row count in range',
    description: 'The table’s row count falls within [min, max].',
    category: 'Table shape',
    fields: [
      { name: 'min_value', label: 'Minimum rows', type: 'number', optional: true },
      { name: 'max_value', label: 'Maximum rows', type: 'number', optional: true },
    ],
  },
  {
    type: 'monitor:freshness',
    dimension: 'timeliness',
    kind: 'freshness',
    label: 'Freshness',
    description:
      'How stale is the target? Measures hours since the latest timestamp in the data — or, on a flat file with no column set, since the file last landed.',
    category: 'Freshness',
    fields: [
      {
        name: 'column',
        label: 'Timestamp column',
        type: 'string',
        help: 'The load/updated timestamp column whose MAX() dates the table.',
      },
    ],
    thresholds: {
      help: 'Band the age in HOURS since the latest row (higher = staler). A fail or critical threshold is required — without one a freshness check can never fail.',
      requireFailOrCritical: true,
    },
  },
  {
    type: 'monitor:volume',
    dimension: 'completeness',
    kind: 'volume',
    label: 'Volume',
    description:
      'Did the load deliver the expected row count? Flags a count outside an allowed range.',
    category: 'Volume',
    fields: [
      { name: 'min_rows', label: 'Minimum rows', type: 'number' },
      { name: 'max_rows', label: 'Maximum rows', type: 'number' },
    ],
    thresholds: {
      // No max: a shortfall caps at 100% but a spike is unbounded (e.g. 10× the
      // ceiling = 900% deviation), so the band inputs must allow > 100.
      help: 'Band the % the row count falls outside [min, max] (either direction; higher = worse; a spike can exceed 100%). Leave blank for a binary in-range pass/fail.',
    },
  },
  {
    type: 'monitor:schema_drift',
    dimension: 'consistency',
    kind: 'schema_drift',
    label: 'Schema drift',
    description:
      'Did the table\u2019s column shape change? Diffs the live columns (names + types) against a baseline captured on the first run. Works on every datasource \u2014 warehouses via information_schema, flat files via the file header/footer, Iceberg from table metadata.',
    category: 'Schema',
    fields: [
      {
        name: 'ignore_columns',
        label: 'Ignored columns',
        type: 'list',
        optional: true,
        help: 'Columns excluded from the diff (housekeeping/audit columns that churn by design). Matched case-insensitively.',
      },
    ],
    thresholds: {
      help: 'Band the drifted-column count (added + removed + type-changed; higher = worse). Leave blank for a binary no-drift pass/fail.',
    },
  },
  {
    type: 'monitor:anomaly',
    // No `dimension` (mirrors backend `check_dimension._BY_KIND`, which has no `anomaly` entry):
    // the metric it watches — row count vs freshness age — is a per-check author choice.
    kind: 'anomaly',
    label: 'Anomaly',
    description:
      'Learns a rolling baseline (mean/stddev) from this check’s own metric history and flags how far this run deviates (a z-score). Reports skip, never a fake pass/fail, until enough history accrues.',
    category: 'Anomaly',
    fields: [
      {
        name: 'target_metric',
        label: 'Target metric',
        type: 'select',
        options: [
          { value: 'row_count', label: 'Row count' },
          { value: 'freshness_age_hours', label: 'Freshness age (hours)' },
        ],
        help: 'What this check measures every run and learns a baseline for.',
      },
      {
        name: 'column',
        label: 'Timestamp column',
        type: 'string',
        help: 'The load/updated timestamp column whose MAX() the freshness-age-hours metric measures.',
        // Backend `anomaly_params`: required when target_metric is
        // freshness_age_hours, and REJECTED (not just ignored) otherwise.
        showWhen: { field: 'target_metric', equals: 'freshness_age_hours' },
      },
      {
        name: 'window',
        label: 'Window (observations)',
        type: 'number',
        optional: true,
        min: 3,
        max: 90,
        defaultValue: 14,
        help: 'How many prior observations the baseline is scored against (3–90). Default 14.',
      },
      {
        name: 'min_points',
        label: 'Minimum points before scoring',
        type: 'number',
        optional: true,
        min: 3,
        // No static `max` — the ceiling is `window`'s live value (backend:
        // `3 <= min_points <= window`). See `ConfigField.maxFrom`.
        maxFrom: 'window',
        maxFromLabel: 'window',
        defaultValue: 7,
        help: 'Below this many observations the check reports skip, never a verdict. Must be ≤ window. Default 7.',
      },
      {
        name: 'seasonality',
        label: 'Day-of-week seasonality',
        type: 'boolean',
        optional: true,
        defaultValue: false,
        help: 'Score against history from the same weekday instead of the raw rolling window (retains window × 7 observations).',
      },
    ],
    thresholds: {
      help: 'Band the anomaly z-score — how many standard deviations this run is from the learned baseline (higher = worse). A fail or critical threshold is required. The check reports skip (not a verdict) until the minimum points of history accrue.',
      requireFailOrCritical: true,
    },
  },
  {
    type: CUSTOM_SQL_EXPECTATION_TYPE,
    label: 'Custom SQL',
    description: 'A SQL query that should return no rows — any rows it returns are failures.',
    category: 'Custom SQL',
    fields: [
      {
        name: CUSTOM_SQL_QUERY_KEY,
        label: 'SQL query',
        type: 'sql',
        help: 'Use {batch} for the suite’s target table. The check passes when the query returns no rows. Read-only (SELECT / WITH) only.',
      },
    ],
  },
  {
    type: COMPARISON_EXPECTATION_TYPE,
    dimension: 'consistency',
    kind: 'comparison',
    label: 'Records reconciliation',
    description:
      'Diff this suite’s dataset (the target under test) against a baseline on another connection, joined on key columns — matched / mismatched / additional-per-side ROW buckets (ADR 0015).',
    category: 'Comparison',
    // Authored via the dedicated side-by-side form (ComparisonCheckForm), not
    // the generic field list.
    fields: [],
    thresholds: {
      help: 'Band the mismatch-% (non-matching rows over all logical rows; higher = worse, 0–100). Leave blank for a binary reconciled pass/fail.',
      max: 100,
    },
  },
  {
    type: COMPARISON_COLUMNS_EXPECTATION_TYPE,
    dimension: 'consistency',
    kind: 'comparison',
    label: 'Column-level reconciliation',
    description:
      'Same key-joined diff, counted per VALUE: each column reports its own matched / mismatched / additional-per-side counts (#799 — FDC column grain). Pick this when you need to know WHICH columns drift, not just which rows.',
    category: 'Comparison',
    fields: [],
    thresholds: {
      help: 'Band the mismatch-% (non-matching value slots over all compared slots; higher = worse, 0–100). Leave blank for a binary reconciled pass/fail.',
      max: 100,
    },
  },
  // Snowflake DMF (ADR 0036 §6) — own expectation types, not a GX toggle.
  {
    type: 'dmf:null_count',
    engine: 'dmf',
    dimension: 'completeness',
    label: 'Null count (DMF)',
    description:
      'Snowflake’s system NULL_COUNT metric function, computed natively in the warehouse.',
    category: 'Snowflake DMF',
    fields: [COLUMN],
    thresholds: {
      help: 'Band the NULL count (higher = worse). A fail or critical threshold is required.',
      requireFailOrCritical: true,
    },
  },
  {
    type: 'dmf:null_percent',
    engine: 'dmf',
    dimension: 'completeness',
    label: 'Null percent (DMF)',
    description:
      'Snowflake’s system NULL_PERCENT metric function (0–100), computed natively in the warehouse.',
    category: 'Snowflake DMF',
    fields: [COLUMN],
    thresholds: {
      help: 'Band the NULL percent (0–100, higher = worse). A fail or critical threshold is required.',
      max: 100,
      requireFailOrCritical: true,
    },
  },
  {
    type: 'dmf:duplicate_count',
    engine: 'dmf',
    dimension: 'uniqueness',
    label: 'Duplicate count (DMF)',
    description:
      'Snowflake’s system DUPLICATE_COUNT metric function, computed natively in the warehouse.',
    category: 'Snowflake DMF',
    fields: [COLUMN],
    thresholds: {
      help: 'Band the duplicate count (higher = worse). A fail or critical threshold is required.',
      requireFailOrCritical: true,
    },
  },
  {
    type: 'dmf:unique_count',
    engine: 'dmf',
    noThresholds: true,
    dimension: 'uniqueness',
    label: 'Unique count (DMF)',
    description:
      'Snowflake’s system UNIQUE_COUNT metric function, computed natively in the warehouse. Degrades downward, so this type carries no thresholds — read the observed value directly.',
    category: 'Snowflake DMF',
    fields: [COLUMN],
  },
];

/** Lookup by expectation_type (for prefilling the editor in edit mode). */
export const EXPECTATION_BY_TYPE: Record<string, ExpectationSpec> = Object.fromEntries(
  EXPECTATION_CATALOG.map((e) => [e.type, e]),
);

/** Expectations grouped by category, in category order — drives the grouped
 *  expectation picker (antd Select optgroups / the dedicated check page). */
export const EXPECTATIONS_BY_CATEGORY: {
  category: ExpectationCategory;
  specs: ExpectationSpec[];
}[] = EXPECTATION_CATEGORIES.map((category) => ({
  category,
  specs: EXPECTATION_CATALOG.filter((e) => e.category === category),
}));

/**
 * Custom SQL (ADR 0019) is offered only on SQL-queryable connections — it runs a literal SQL
 * query.
 */
const CUSTOM_SQL_CATEGORY: ExpectationCategory = 'Custom SQL';

/**
 * Anomaly (#593) is gated the SAME as Custom SQL — `isSqlQueryable` and the backend's
 * `ANOMALY_CAPABLE_TYPES` are both exactly `{snowflake, unity_catalog}`.
 */
const ANOMALY_CATEGORY: ExpectationCategory = 'Anomaly';

/**
 * The freshness/volume monitor categories (ADR 0012) — offered on any monitor-capable datasource
 * (SQL datasources + Iceberg, `supportsMonitors`).
 */
const MONITOR_CATEGORY_SET = new Set<ExpectationCategory>(MONITOR_CATEGORIES);

const DMF_CATEGORY: ExpectationCategory = 'Snowflake DMF';

/** Grouped catalog filtered for a suite's datasource. */
export function expectationsByCategoryFor(
  connectionType: ConnectionType | undefined,
  alwaysIncludeType?: string,
): {
  category: ExpectationCategory;
  specs: ExpectationSpec[];
}[] {
  const sqlAllowed = connectionType !== undefined && isSqlQueryable(connectionType);
  const monitorAllowed = connectionType !== undefined && supportsMonitors(connectionType);
  const dmfAllowed = offersDmfEngine(connectionType);
  const selectedCategory = alwaysIncludeType
    ? EXPECTATION_BY_TYPE[alwaysIncludeType]?.category
    : undefined;
  const allowed = (category: ExpectationCategory): boolean => {
    if (category === selectedCategory) return true;
    if (category === CUSTOM_SQL_CATEGORY || category === ANOMALY_CATEGORY) return sqlAllowed;
    if (category === DMF_CATEGORY) return dmfAllowed;
    if (MONITOR_CATEGORY_SET.has(category)) return monitorAllowed;
    return true; // datasource-agnostic category
  };
  // A `dataframeOnly` spec errors on a SQL batch, so drop it per-SPEC rather than per-category —
  // it shares 'Column values' with a dozen types that run everywhere. The type being edited stays
  // visible, or the editor would silently switch the check to something else. Fails CLOSED on an
  // unknown connection type, like every gate above it: the connection load is best-effort, and
  // offering a type the backend then 422s wastes a whole filled-in form.
  const sqlBatch = connectionType === undefined || runsSqlBatch(connectionType);
  const specAllowed = (spec: ExpectationSpec): boolean =>
    spec.type === alwaysIncludeType || !spec.dataframeOnly || !sqlBatch;
  return EXPECTATIONS_BY_CATEGORY.filter((g) => allowed(g.category)).map((g) => ({
    category: g.category,
    specs: g.specs.filter(specAllowed),
  }));
}

/** A spec's config fields, adjusted for the suite's datasource (#520). */
export function configFieldsFor(
  spec: ExpectationSpec,
  connectionType: ConnectionType | undefined,
): ConfigField[] {
  if (
    spec.kind !== 'freshness' ||
    connectionType === undefined ||
    !isFileDatasource(connectionType)
  )
    return spec.fields;
  return spec.fields.map((field) =>
    field.name === 'column'
      ? {
          ...field,
          optional: true,
          help: 'Leave blank to measure when the FILE last landed instead — catches a producer that stopped sending files, which a timestamp inside the data cannot.',
        }
      : field,
  );
}
