/**
 * Curated catalog of GX expectations the check editor exposes in v1.
 *
 * The backend treats `expectation_type` as a snake_case string (title-cased to a
 * GX expectation class) and `config` as free-form GX kwargs — there is no server
 * catalog. This file is the frontend's single source of truth for which
 * expectations are offered and what typed config each needs, the same spec-driven
 * idiom as `connectionFormSpec.ts`.
 *
 * GX column/table expectations are datasource-agnostic in v1 (all four
 * datasources run them through the shared `gx_runner`), so one catalog serves
 * every suite regardless of its connection type.
 */

import { isSqlQueryable, supportsMonitors, type ConnectionType } from '../../api/connections';
import { CUSTOM_SQL_EXPECTATION_TYPE, CUSTOM_SQL_QUERY_KEY } from './customSql';

export type ConfigFieldType = 'string' | 'number' | 'list' | 'sql';

/** The check `kind` (ADR 0012). `expectation` (incl. custom-SQL) is GX; the
 *  monitor kinds run a scalar SQL aggregate instead. Sent to the backend. */
export type CheckKind = 'expectation' | 'freshness' | 'volume';

/**
 * Expectation categories — the GX-Cloud-style classification the check editor
 * groups by. v1 ships value-level GX expectations + custom-SQL (ADR 0019) + the
 * freshness/volume monitor kinds (ADR 0012, pulled into v1). `Schema drift` stays
 * a reserved-only category (surfaced disabled on the dedicated page).
 */
export type ExpectationCategory =
  'Column values' | 'Table shape' | 'Freshness' | 'Volume' | 'Custom SQL';

export const EXPECTATION_CATEGORIES: ExpectationCategory[] = [
  'Column values',
  'Table shape',
  'Freshness',
  'Volume',
  'Custom SQL',
];

/** Monitor categories (ADR 0012) — like Custom SQL, they run a scalar SQL
 *  aggregate, so they're offered only on SQL-queryable datasources. */
export const MONITOR_CATEGORIES: ExpectationCategory[] = ['Freshness', 'Volume'];

export interface ConfigField {
  /** Key in the GX `config` kwargs object. */
  name: string;
  label: string;
  type: ConfigFieldType;
  optional?: boolean;
  help?: string;
}

/** Severity-threshold semantics for a monitor kind (ADR 0012/0016). Monitors band
 *  their own metric (age-hours / deviation-%), not GX unexpected-%, so the threshold
 *  block needs kind-specific help/bounds/requiredness. Absent → the default GX %. */
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
  fields: ConfigField[];
  /** Present for monitor kinds — drives the threshold block's help/bounds/required. */
  thresholds?: MonitorThresholdSpec;
}

const COLUMN: ConfigField = { name: 'column', label: 'Column', type: 'string' };

export const EXPECTATION_CATALOG: ExpectationSpec[] = [
  {
    type: 'expect_column_values_to_not_be_null',
    label: 'Column values not null',
    description: 'Every value in the column is non-null.',
    category: 'Column values',
    fields: [COLUMN],
  },
  {
    type: 'expect_column_values_to_be_unique',
    label: 'Column values unique',
    description: 'Values in the column are distinct (no duplicates).',
    category: 'Column values',
    fields: [COLUMN],
  },
  {
    type: 'expect_column_values_to_be_between',
    label: 'Column values in range',
    description: 'Numeric values fall within [min, max].',
    category: 'Column values',
    fields: [
      COLUMN,
      { name: 'min_value', label: 'Minimum', type: 'number', optional: true },
      { name: 'max_value', label: 'Maximum', type: 'number', optional: true },
    ],
  },
  {
    type: 'expect_column_values_to_be_in_set',
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
    ],
  },
  {
    type: 'expect_column_value_lengths_to_be_between',
    label: 'Column value lengths in range',
    description: 'String lengths fall within [min, max].',
    category: 'Column values',
    fields: [
      COLUMN,
      { name: 'min_value', label: 'Min length', type: 'number', optional: true },
      { name: 'max_value', label: 'Max length', type: 'number', optional: true },
    ],
  },
  {
    type: 'expect_column_values_to_match_regex',
    label: 'Column values match regex',
    description: 'Every value matches the given regular expression.',
    category: 'Column values',
    fields: [COLUMN, { name: 'regex', label: 'Regex', type: 'string' }],
  },
  {
    type: 'expect_table_row_count_to_be_between',
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
    kind: 'freshness',
    label: 'Freshness',
    description:
      'How stale is the table? Measures hours since the latest value in a timestamp column.',
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

/** Custom SQL (ADR 0019) is offered only on SQL-queryable connections — it runs a
 *  literal SQL query. Distinct from the monitor categories (below), which Iceberg
 *  also supports natively despite not being SQL-queryable. */
const CUSTOM_SQL_CATEGORY: ExpectationCategory = 'Custom SQL';

/** The freshness/volume monitor categories (ADR 0012) — offered on any
 *  monitor-capable datasource (SQL datasources + Iceberg, `supportsMonitors`),
 *  since the aggregate need not be SQL (Iceberg computes it natively). */
const MONITOR_CATEGORY_SET = new Set<ExpectationCategory>(MONITOR_CATEGORIES);

/**
 * Grouped catalog filtered for a suite's datasource. Custom SQL is hidden unless
 * the connection is SQL-queryable; the monitor categories are hidden unless it's
 * monitor-capable — both also hidden while the connection type is still loading
 * (`undefined`) — so we never offer a category the backend would 422. Every other
 * category is datasource-agnostic.
 *
 * `alwaysIncludeType` keeps the group of an already-selected expectation visible
 * regardless of gating — the edit drawer passes the check's current type so a
 * custom-SQL / monitor check stays editable even before its connection type is
 * known (else the Select would have no option matching the prefilled value).
 */
export function expectationsByCategoryFor(
  connectionType: ConnectionType | undefined,
  alwaysIncludeType?: string,
): {
  category: ExpectationCategory;
  specs: ExpectationSpec[];
}[] {
  const sqlAllowed = connectionType !== undefined && isSqlQueryable(connectionType);
  const monitorAllowed = connectionType !== undefined && supportsMonitors(connectionType);
  const selectedCategory = alwaysIncludeType
    ? EXPECTATION_BY_TYPE[alwaysIncludeType]?.category
    : undefined;
  const allowed = (category: ExpectationCategory): boolean => {
    if (category === selectedCategory) return true;
    if (category === CUSTOM_SQL_CATEGORY) return sqlAllowed;
    if (MONITOR_CATEGORY_SET.has(category)) return monitorAllowed;
    return true; // datasource-agnostic category
  };
  return EXPECTATIONS_BY_CATEGORY.filter((g) => allowed(g.category));
}
