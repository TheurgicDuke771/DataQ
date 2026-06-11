import { describe, expect, it } from 'vitest';

import {
  EXPECTATION_CATALOG,
  EXPECTATIONS_BY_CATEGORY,
} from '../../src/components/checks/expectationCatalog';

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
