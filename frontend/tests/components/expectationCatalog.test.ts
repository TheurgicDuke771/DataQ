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

  it('keeps the datasource-agnostic categories regardless of type', () => {
    for (const type of ['snowflake', 's3', undefined] as const) {
      const names = categoryNames(expectationsByCategoryFor(type));
      expect(names).toContain('Column values');
      expect(names).toContain('Table shape');
    }
  });
});
