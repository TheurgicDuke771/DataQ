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

import { isSqlQueryable, type ConnectionType } from '../../api/connections';
import { CUSTOM_SQL_EXPECTATION_TYPE, CUSTOM_SQL_QUERY_KEY } from './customSql';

export type ConfigFieldType = 'string' | 'number' | 'list' | 'sql';

/**
 * Expectation categories — the GX-Cloud-style classification the check editor
 * groups by. v1 ships value-level GX expectations + custom-SQL (ADR 0019); the
 * monitor-kind seam (ADR 0012) reserves Freshness / Volume / Schema-drift
 * categories for v1.x auto-monitors, surfaced (disabled) on the dedicated page.
 */
export type ExpectationCategory = 'Column values' | 'Table shape' | 'Custom SQL';

export const EXPECTATION_CATEGORIES: ExpectationCategory[] = [
  'Column values',
  'Table shape',
  'Custom SQL',
];

export interface ConfigField {
  /** Key in the GX `config` kwargs object. */
  name: string;
  label: string;
  type: ConfigFieldType;
  optional?: boolean;
  help?: string;
}

export interface ExpectationSpec {
  /** snake_case GX expectation type sent to the backend. */
  type: string;
  label: string;
  description: string;
  category: ExpectationCategory;
  fields: ConfigField[];
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

/**
 * Grouped catalog filtered for a suite's datasource. Custom SQL (ADR 0019) runs
 * only on SQL-queryable connections (Snowflake / Unity Catalog), so it's hidden
 * for flat-file suites — and while the connection type is still loading
 * (`undefined`), so we never offer a category the backend would 422. Every other
 * category is datasource-agnostic.
 *
 * `alwaysIncludeType` keeps the group of an already-selected expectation visible
 * regardless of gating — the edit drawer passes the check's current type so a
 * custom-SQL check stays editable even before its connection type is known (else
 * the Select would have no option matching the prefilled value).
 */
export function expectationsByCategoryFor(
  connectionType: ConnectionType | undefined,
  alwaysIncludeType?: string,
): {
  category: ExpectationCategory;
  specs: ExpectationSpec[];
}[] {
  const allowCustomSql =
    (connectionType !== undefined && isSqlQueryable(connectionType)) ||
    alwaysIncludeType === CUSTOM_SQL_EXPECTATION_TYPE;
  return EXPECTATIONS_BY_CATEGORY.filter((g) => g.category !== 'Custom SQL' || allowCustomSql);
}
