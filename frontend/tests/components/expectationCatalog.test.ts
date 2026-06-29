import { describe, expect, it } from 'vitest';

import type { ConnectionType } from '../../src/api/connections';
import {
  EXPECTATION_CATALOG,
  EXPECTATIONS_BY_CATEGORY,
  expectationsByCategoryFor,
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

  it.each<ConnectionType>(['s3', 'adls_gen2', 'adf', 'airflow'])(
    'hides Custom SQL for non-SQL datasource %s',
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
  it.each<ConnectionType>(['snowflake', 'unity_catalog'])(
    'offers Freshness + Volume for SQL datasource %s',
    (type) => {
      const names = categoryNames(expectationsByCategoryFor(type));
      expect(names).toContain('Freshness');
      expect(names).toContain('Volume');
    },
  );

  it.each<ConnectionType>(['s3', 'adls_gen2', 'adf', 'airflow'])(
    'hides monitor categories for non-SQL datasource %s',
    (type) => {
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
