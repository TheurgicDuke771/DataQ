import { describe, expect, it } from 'vitest';

import type { ConnectionType } from '../../src/api/connections';
import {
  type ConfigField,
  configFieldsFor,
  EXPECTATION_BY_TYPE,
  EXPECTATION_CATALOG,
  EXPECTATIONS_BY_CATEGORY,
  expectationsByCategoryFor,
  fieldVisible,
  MOSTLY_FIELD_NAME,
  TYPE_FIELD_NAMES,
  typeFieldHint,
} from '../../src/components/checks/expectationCatalog';
import { buildCheckPayload } from '../../src/components/checks/checkForm';

const categoryNames = (groups: { category: string; specs: unknown[] }[]): string[] =>
  groups.map((g) => g.category);

/** Look up a catalog spec's config field by name, failing loudly (not via a
 *  non-null assertion) if it's missing. */
function requiredField(specType: string, fieldName: string): ConfigField {
  const field = EXPECTATION_BY_TYPE[specType]?.fields.find((f) => f.name === fieldName);
  if (!field) throw new Error(`no ${fieldName} field on ${specType}`);
  return field;
}

describe('expectationCatalog', () => {
  // EXPECTATIONS_BY_CATEGORY filters the catalog by the categories listed in
  // EXPECTATION_CATEGORIES.
  it('groups every catalog expectation (none dropped from the picker)', () => {
    const grouped = EXPECTATIONS_BY_CATEGORY.flatMap((g) => g.specs);
    expect(grouped).toHaveLength(EXPECTATION_CATALOG.length);
    expect(new Set(grouped.map((e) => e.type))).toEqual(
      new Set(EXPECTATION_CATALOG.map((e) => e.type)),
    );
  });
});

describe('expectationsByCategoryFor (custom-SQL datasource gating, ADR 0019)', () => {
  it.each<ConnectionType>(['snowflake', 'unity_catalog'])(
    'offers Custom SQL for SQL datasource %s',
    (type) => {
      expect(categoryNames(expectationsByCategoryFor(type))).toContain('Custom SQL');
    },
  );

  it.each<ConnectionType>(['s3', 'adls_gen2', 'iceberg', 'adf', 'airflow'])(
    'hides Custom SQL for non-SQL datasource %s (Iceberg is a native read, not SQL)',
    (type) => {
      expect(categoryNames(expectationsByCategoryFor(type))).not.toContain('Custom SQL');
    },
  );

  it('hides Custom SQL while the connection type is still unknown', () => {
    expect(categoryNames(expectationsByCategoryFor(undefined))).not.toContain('Custom SQL');
  });

  it('keeps Custom SQL when editing one even if the connection type is unknown', () => {
    // Edit-drawer fallback: the prefilled custom-SQL type must stay selectable
    // before the connection loads (and on a non-SQL type it stays hidden).
    const editing = 'unexpected_rows_expectation';
    expect(categoryNames(expectationsByCategoryFor(undefined, editing))).toContain('Custom SQL');
    expect(categoryNames(expectationsByCategoryFor('s3', editing))).toContain('Custom SQL');
    expect(
      categoryNames(expectationsByCategoryFor('s3', 'expect_column_values_to_not_be_null')),
    ).not.toContain('Custom SQL');
  });

  it('keeps the datasource-agnostic categories regardless of type', () => {
    for (const type of ['snowflake', 's3', undefined] as const) {
      const names = categoryNames(expectationsByCategoryFor(type));
      expect(names).toContain('Column values');
      expect(names).toContain('Table shape');
    }
  });
});

describe('expectationsByCategoryFor (freshness/volume monitor gating, ADR 0012)', () => {
  it.each<ConnectionType>(['snowflake', 'unity_catalog', 'iceberg', 's3', 'adls_gen2'])(
    'offers Freshness + Volume for monitor-capable datasource %s (Iceberg computes them natively; flat files over the resolved batch, #520)',
    (type) => {
      const names = categoryNames(expectationsByCategoryFor(type));
      expect(names).toContain('Freshness');
      expect(names).toContain('Volume');
    },
  );

  it.each<ConnectionType>(['adf', 'airflow'])(
    'hides monitor categories for non-monitor-capable datasource %s',
    (type) => {
      // Orchestration providers are not datasources at all (CLAUDE.md §4) — there
      // is nothing to aggregate over.
      const names = categoryNames(expectationsByCategoryFor(type));
      expect(names).not.toContain('Freshness');
      expect(names).not.toContain('Volume');
    },
  );

  it('hides monitor categories while the connection type is still unknown', () => {
    const names = categoryNames(expectationsByCategoryFor(undefined));
    expect(names).not.toContain('Freshness');
    expect(names).not.toContain('Volume');
  });

  it('keeps a monitor category when editing one even if the connection type is unknown', () => {
    // Edit fallback: a freshness check stays selectable before its connection loads.
    expect(categoryNames(expectationsByCategoryFor(undefined, 'monitor:freshness'))).toContain(
      'Freshness',
    );
  });

  it('models freshness as kind=freshness requiring a threshold, volume as kind=volume', () => {
    const byType = Object.fromEntries(EXPECTATION_CATALOG.map((e) => [e.type, e]));
    expect(byType['monitor:freshness'].kind).toBe('freshness');
    expect(byType['monitor:freshness'].thresholds?.requireFailOrCritical).toBe(true);
    expect(byType['monitor:volume'].kind).toBe('volume');
    // No max bound — a volume spike's deviation-% is unbounded (can exceed 100).
    expect(byType['monitor:volume'].thresholds?.max).toBeUndefined();
  });
});

describe('expectationsByCategoryFor (anomaly monitor gating, #593 — SQL-only, stricter than freshness/volume)', () => {
  it.each<ConnectionType>(['snowflake', 'unity_catalog'])(
    'offers Anomaly for SQL-queryable datasource %s',
    (type) => {
      expect(categoryNames(expectationsByCategoryFor(type))).toContain('Anomaly');
    },
  );

  it.each<ConnectionType>(['iceberg', 's3', 'adls_gen2', 'adf', 'airflow'])(
    'hides Anomaly for non-SQL-queryable datasource %s (Iceberg/flat-files DO get Freshness/Volume, but not Anomaly — it measures over a live SQL connection)',
    (type) => {
      const names = categoryNames(expectationsByCategoryFor(type));
      expect(names).not.toContain('Anomaly');
    },
  );

  it('hides Anomaly while the connection type is still unknown', () => {
    expect(categoryNames(expectationsByCategoryFor(undefined))).not.toContain('Anomaly');
  });

  it('keeps Anomaly when editing one even if the connection type is unknown', () => {
    expect(categoryNames(expectationsByCategoryFor(undefined, 'monitor:anomaly'))).toContain(
      'Anomaly',
    );
  });

  it('models anomaly as kind=anomaly requiring a threshold, with NO derived dimension', () => {
    const spec = EXPECTATION_BY_TYPE['monitor:anomaly'];
    expect(spec.kind).toBe('anomaly');
    expect(spec.thresholds?.requireFailOrCritical).toBe(true);
    // Mirrors the backend `check_dimension._BY_KIND`, which has no 'anomaly' entry (pinned by the
    // backend catalog-contract test).
    expect(spec.dimension).toBeUndefined();
  });

  it('offers exactly the two documented target metrics', () => {
    const spec = EXPECTATION_BY_TYPE['monitor:anomaly'];
    const targetMetric = spec.fields.find((f) => f.name === 'target_metric');
    expect(targetMetric?.type).toBe('select');
    expect(targetMetric?.options?.map((o) => o.value)).toEqual([
      'row_count',
      'freshness_age_hours',
    ]);
  });

  it("bounds min_points by window's live value, not a static max (backend: 3 <= min_points <= window)", () => {
    const minPoints = requiredField('monitor:anomaly', 'min_points');
    expect(minPoints.min).toBe(3);
    expect(minPoints.max).toBeUndefined();
    expect(minPoints.maxFrom).toBe('window');
  });
});

describe('fieldVisible / anomaly conditional column field (#593)', () => {
  const columnField = () => requiredField('monitor:anomaly', 'column');

  it('the column field declares showWhen target_metric=freshness_age_hours', () => {
    expect(columnField().showWhen).toEqual({
      field: 'target_metric',
      equals: 'freshness_age_hours',
    });
  });

  it('is hidden for row_count and shown for freshness_age_hours', () => {
    expect(fieldVisible(columnField(), { target_metric: 'row_count' })).toBe(false);
    expect(fieldVisible(columnField(), { target_metric: 'freshness_age_hours' })).toBe(true);
  });

  it('is hidden when no target_metric has been chosen yet', () => {
    expect(fieldVisible(columnField(), undefined)).toBe(false);
    expect(fieldVisible(columnField(), {})).toBe(false);
  });

  it('an unconditional field (no showWhen) is always visible', () => {
    const windowField = requiredField('monitor:anomaly', 'window');
    expect(fieldVisible(windowField, undefined)).toBe(true);
    expect(fieldVisible(windowField, { target_metric: 'row_count' })).toBe(true);
  });
});

describe('configFieldsFor (flat-file arrival-time freshness, #520)', () => {
  const freshness = () => EXPECTATION_BY_TYPE['monitor:freshness'];
  const columnField = (type: ConnectionType | undefined) =>
    configFieldsFor(freshness(), type).find((f) => f.name === 'column');

  it.each<ConnectionType>(['s3', 'adls_gen2'])(
    'makes the timestamp column optional on flat-file datasource %s',
    (type) => {
      // Blank doesn't mean "skip the check" — it selects a DIFFERENT measurement (when the file
      // landed), so the help text must say so rather than reading as an omission.
      const field = columnField(type);
      expect(field?.optional).toBe(true);
      expect(field?.help).toMatch(/landed/i);
    },
  );

  it.each<ConnectionType>(['snowflake', 'unity_catalog', 'iceberg'])(
    'keeps the timestamp column required on %s (a table has no arrival time)',
    (type) => {
      // Mirrors the backend gate: a column-less freshness check on these 422s, so
      // offering it as optional would produce a form that cannot be submitted.
      expect(columnField(type)?.optional).toBeFalsy();
    },
  );

  it('keeps the column required while the connection type is unknown', () => {
    expect(columnField(undefined)?.optional).toBeFalsy();
  });

  it('leaves non-freshness specs untouched', () => {
    const volume = EXPECTATION_BY_TYPE['monitor:volume'];
    expect(configFieldsFor(volume, 's3')).toBe(volume.fields);
  });
});

describe('expect_column_values_to_be_of_type catalog entry (issue #768)', () => {
  it('is offered as a datasource-agnostic Column values expectation with a type_ field', () => {
    const spec = EXPECTATION_BY_TYPE['expect_column_values_to_be_of_type'];
    expect(spec).toBeDefined();
    expect(spec.category).toBe('Column values');
    expect(spec.fields.map((f) => f.name)).toEqual(['column', 'type_', 'mostly']);
  });
});

describe('typeFieldHint (issue #768 — Snowflake NUMBER ≠ "NUMBER")', () => {
  it('tells Snowflake authors to use the fully-qualified dialect type', () => {
    const hint = typeFieldHint('snowflake');
    expect(hint).toMatch(/DECIMAL\(38, 0\)/);
    expect(hint).toMatch(/dry-run/i);
  });

  it.each<ConnectionType>(['unity_catalog', 's3', 'adls_gen2', 'iceberg'])(
    'tells %s authors about pandas dtypes, the object-dtype string case, and the NULL upcast',
    (type) => {
      const hint = typeFieldHint(type);
      expect(hint).toMatch(/int64/);
      // UC/CSV string columns are plain pandas object dtype — object or str both
      // pass (verified live on GX 1.17.2; PR-#781 review finding 1).
      expect(hint).toMatch(/`object` or `str` both pass/);
      // Nullable-integer upcast caveat: any NULL → float64 (finding 2).
      expect(hint).toMatch(/NULLs report `float64`/);
      // Row-wise dead-end: a wrong value-type guess fails with no observed_value.
      expect(hint).toMatch(/row-wise/);
      expect(hint).not.toMatch(/dialect/i); // sanity: not the Snowflake/SQL wording
    },
  );

  it('falls back to the generic help while the connection type is unknown', () => {
    const hint = typeFieldHint(undefined);
    expect(hint).toMatch(/execution engine/i);
  });

  it('falls back to the generic help for a non-datasource (orchestration) connection', () => {
    for (const type of ['adf', 'airflow', 'dbt'] as ConnectionType[]) {
      expect(typeFieldHint(type)).toMatch(/execution engine/i);
    }
  });
});

describe('`mostly` tolerance field (#1509)', () => {
  const WITH_MOSTLY = [
    'expect_column_values_to_not_be_null',
    'expect_column_values_to_be_unique',
    'expect_column_values_to_be_between',
    'expect_column_values_to_be_in_set',
    'expect_column_value_lengths_to_be_between',
    'expect_column_values_to_match_regex',
    'expect_column_values_to_be_of_type',
  ];

  it.each(WITH_MOSTLY)('offers an optional, 0-1 bounded tolerance on %s', (type) => {
    const field = requiredField(type, MOSTLY_FIELD_NAME);
    expect(field.type).toBe('number');
    expect(field.optional).toBe(true);
    // NOT 0, which GX accepts and which succeeds unconditionally forever (#426 silent green).
    expect(field.min).toBeGreaterThan(0);
    expect(field.max).toBe(1);
    // The antd default step of 1 makes a 0-1 range unusable.
    expect(field.step).toBeLessThan(1);
  });

  // GX rejects `mostly` on a table-shape expectation; the backend contract test proves that
  // against the pinned GX, this one keeps the catalog from offering it.
  it('does not offer a tolerance on expect_table_row_count_to_be_between', () => {
    const spec = EXPECTATION_BY_TYPE.expect_table_row_count_to_be_between;
    expect(spec.fields.map((f) => f.name)).not.toContain(MOSTLY_FIELD_NAME);
  });

  it('states the fraction unit and that severity bands still read the full unexpected-%', () => {
    const help = requiredField('expect_column_values_to_not_be_null', MOSTLY_FIELD_NAME).help ?? '';
    expect(help).toMatch(/0\.95/);
    expect(help).toMatch(/fraction/i);
    expect(help).toMatch(/threshold/i);
  });

  it('warns on the type checks that the tolerance only reaches the row-wise compare', () => {
    const help = requiredField('expect_column_values_to_be_of_type', MOSTLY_FIELD_NAME).help ?? '';
    expect(help).toMatch(/row-wise compare/);
  });
});

describe('cross-column, type-list and set-relation entries (#1509)', () => {
  /** type → the exact config-field names, in render order. */
  const NEW_ENTRIES: [string, string[]][] = [
    ['expect_column_values_to_be_in_type_list', ['column', 'type_list', 'mostly']],
    ['expect_compound_columns_to_be_unique', ['column_list', 'mostly']],
    [
      'expect_column_pair_values_a_to_be_greater_than_b',
      ['column_A', 'column_B', 'or_equal', 'mostly'],
    ],
    ['expect_multicolumn_sum_to_equal', ['column_list', 'sum_total', 'mostly']],
    ['expect_column_distinct_values_to_be_in_set', ['column', 'value_set']],
    ['expect_column_distinct_values_to_contain_set', ['column', 'value_set']],
  ];

  it.each(NEW_ENTRIES)('%s renders exactly its GX kwargs as Column values', (type, fields) => {
    const spec = EXPECTATION_BY_TYPE[type];
    expect(spec, `${type} missing from the catalog`).toBeDefined();
    expect(spec.category).toBe('Column values');
    expect(spec.fields.map((f) => f.name)).toEqual(fields);
    // No `kind`/`engine` overrides: these are ordinary GX expectations.
    expect(spec.kind).toBeUndefined();
    expect(spec.engine).toBeUndefined();
  });

  it.each(NEW_ENTRIES)('%s is offered on every datasource', (type) => {
    for (const connectionType of ['snowflake', 'unity_catalog', 's3', 'iceberg'] as const) {
      const offered = expectationsByCategoryFor(connectionType).flatMap((g) => g.specs);
      expect(offered.map((s) => s.type)).toContain(type);
    }
  });

  it('serializes the multi-column list fields as arrays', () => {
    const payload = buildCheckPayload({
      name: 'pk',
      expectation_type: 'expect_compound_columns_to_be_unique',
      config: { column_list: ' order_id , line_no ' },
    });
    expect(payload.config).toEqual({ column_list: ['order_id', 'line_no'] });
  });

  it('gives type_list the same datasource-tailored hint as the single-type field', () => {
    expect(TYPE_FIELD_NAMES).toEqual(['type_', 'type_list']);
  });

  it('tells the author the severity bands are inert on the distinct-value set relations', () => {
    for (const type of [
      'expect_column_distinct_values_to_be_in_set',
      'expect_column_distinct_values_to_contain_set',
    ]) {
      const help = EXPECTATION_BY_TYPE[type].thresholds?.help ?? '';
      expect(help, `${type} has no threshold caveat`).toMatch(/no unexpected-%/);
      expect(help).toMatch(/ignored/);
    }
  });

  it('offers the strftime-format entry with its date-format field', () => {
    const spec = EXPECTATION_BY_TYPE.expect_column_values_to_match_strftime_format;
    expect(spec.category).toBe('Column values');
    expect(spec.fields.map((f) => f.name)).toEqual(['column', 'strftime_format', 'mostly']);
    expect(spec.dataframeOnly).toBe(true);
  });

  it('leaves the row-wise cross-column entries on the default (bandable) threshold help', () => {
    for (const type of [
      'expect_compound_columns_to_be_unique',
      'expect_column_pair_values_a_to_be_greater_than_b',
      'expect_multicolumn_sum_to_equal',
    ]) {
      expect(EXPECTATION_BY_TYPE[type].thresholds).toBeUndefined();
    }
  });
});

describe('expectationsByCategoryFor (dataframeOnly per-spec gating, #1509)', () => {
  const STRFTIME = 'expect_column_values_to_match_strftime_format';
  const offeredTypes = (connectionType: ConnectionType | undefined, alwaysInclude?: string) =>
    expectationsByCategoryFor(connectionType, alwaysInclude).flatMap((g) =>
      g.specs.map((s) => s.type),
    );

  it('hides it on a SQL-batch datasource where GX has no provider for it', () => {
    expect(offeredTypes('snowflake')).not.toContain(STRFTIME);
  });

  it.each<ConnectionType>(['s3', 'adls_gen2', 'iceberg', 'unity_catalog'])(
    'offers it on %s, whose runner builds a dataframe batch',
    (connectionType) => {
      expect(offeredTypes(connectionType)).toContain(STRFTIME);
    },
  );

  it('hides it while the connection type is still unknown, like every sibling gate', () => {
    // The connection load is best-effort (`CheckNew` catches), so an author with a suite share
    // and no connection read access must not fill a whole form for a type the backend 422s.
    expect(offeredTypes(undefined)).not.toContain(STRFTIME);
  });

  it('keeps it visible when editing one, or the editor would silently retype the check', () => {
    expect(offeredTypes('snowflake', STRFTIME)).toContain(STRFTIME);
    expect(offeredTypes(undefined, STRFTIME)).toContain(STRFTIME);
  });

  it('drops only the gated spec, not its whole category', () => {
    const onSnowflake = offeredTypes('snowflake');
    expect(onSnowflake).toContain('expect_column_values_to_not_be_null');
    expect(onSnowflake).toContain('expect_compound_columns_to_be_unique');
  });
});
