import { describe, expect, it } from 'vitest';

import { targetString } from '../../src/api/suites';

describe('targetString', () => {
  it('returns the string value when the key holds a string', () => {
    expect(targetString({ table: 'ORDERS', schema: 'PUBLIC' }, 'table')).toBe('ORDERS');
    expect(targetString({ table: 'ORDERS', schema: 'PUBLIC' }, 'schema')).toBe('PUBLIC');
  });

  it('returns undefined for a null target', () => {
    expect(targetString(null, 'table')).toBeUndefined();
  });

  it('returns undefined for a missing key', () => {
    expect(targetString({ table: 'ORDERS' }, 'path')).toBeUndefined();
  });

  it('returns undefined when the value is present but not a string', () => {
    expect(
      targetString({ table: 42 } as unknown as Record<string, unknown>, 'table'),
    ).toBeUndefined();
  });
});
