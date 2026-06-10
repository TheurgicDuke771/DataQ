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

export type ConfigFieldType = 'string' | 'number' | 'list';

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
  fields: ConfigField[];
}

const COLUMN: ConfigField = { name: 'column', label: 'Column', type: 'string' };

export const EXPECTATION_CATALOG: ExpectationSpec[] = [
  {
    type: 'expect_column_values_to_not_be_null',
    label: 'Column values not null',
    description: 'Every value in the column is non-null.',
    fields: [COLUMN],
  },
  {
    type: 'expect_column_values_to_be_unique',
    label: 'Column values unique',
    description: 'Values in the column are distinct (no duplicates).',
    fields: [COLUMN],
  },
  {
    type: 'expect_column_values_to_be_between',
    label: 'Column values in range',
    description: 'Numeric values fall within [min, max].',
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
    fields: [COLUMN, { name: 'regex', label: 'Regex', type: 'string' }],
  },
  {
    type: 'expect_table_row_count_to_be_between',
    label: 'Table row count in range',
    description: 'The table’s row count falls within [min, max].',
    fields: [
      { name: 'min_value', label: 'Minimum rows', type: 'number', optional: true },
      { name: 'max_value', label: 'Maximum rows', type: 'number', optional: true },
    ],
  },
];

/** Lookup by expectation_type (for prefilling the editor in edit mode). */
export const EXPECTATION_BY_TYPE: Record<string, ExpectationSpec> = Object.fromEntries(
  EXPECTATION_CATALOG.map((e) => [e.type, e]),
);
