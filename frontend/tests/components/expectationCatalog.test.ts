import { describe, expect, it } from 'vitest';

import type { ConnectionType } from '../../src/api/connections';
import {
  configFieldsFor,
  EXPECTATION_BY_TYPE,
  EXPECTATION_CATALOG,
  EXPECTATIONS_BY_CATEGORY,
  expectationsByCategoryFor,
  typeFieldHint,
} from '../../src/components/checks/expectationCatalog';

const categoryNames = (groups: { category: string; specs: unknown[] }[]): string[] =>
  groups.map((g) => g.category);

describe('expectationCatalog', () => {
  // EXPECTATIONS_BY_CATEGORY filters the catalog by the categories listed in
  // EXPECTATION_CATEGORIES — so an expectation whose category string isn't in
  // that list would silently vanish from the grouped picker. Guard against it:
  // every catalog entry must appear in exactly one group.
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

describe('configFieldsFor (flat-file arrival-time freshness, #520)', () => {
  const freshness = () => EXPECTATION_BY_TYPE['monitor:freshness'];
  const columnField = (type: ConnectionType | undefined) =>
    configFieldsFor(freshness(), type).find((f) => f.name === 'column');

  it.each<ConnectionType>(['s3', 'adls_gen2'])(
    'makes the timestamp column optional on flat-file datasource %s',
    (type) => {
      // Blank doesn't mean "skip the check" — it selects a DIFFERENT measurement
      // (when the file landed), so the help text must say so rather than reading
      // as an omission.
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
    expect(spec.fields.map((f) => f.name)).toEqual(['column', 'type_']);
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
