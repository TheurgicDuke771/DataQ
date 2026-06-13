import { describe, expect, it } from 'vitest';

import type { ConnectionType } from '../../src/api/connections';
import { assembleTarget, targetKind } from '../../src/components/suites/suiteTarget';

describe('targetKind', () => {
  it('maps each datasource type to its input shape; orchestration → null', () => {
    const cases: [ConnectionType, ReturnType<typeof targetKind>][] = [
      ['snowflake', 'sql'],
      ['unity_catalog', 'uc'],
      ['adls_gen2', 'flatfile'],
      ['s3', 'flatfile'],
      ['adf', null],
      ['airflow', null],
    ];
    for (const [type, kind] of cases) expect(targetKind(type)).toBe(kind);
  });
});

describe('assembleTarget', () => {
  it('returns a null target when nothing is filled (valid targetless suite)', () => {
    expect(assembleTarget('sql', {}).target).toBeNull();
    expect(assembleTarget('uc', {}).target).toBeNull();
    expect(assembleTarget('flatfile', {}).target).toBeNull();
  });

  it('builds a SQL target, omitting an empty schema', () => {
    expect(assembleTarget('sql', { target_table: 'ANALYTICS.ORDERS' }).target).toEqual({
      table: 'ANALYTICS.ORDERS',
    });
    expect(
      assembleTarget('sql', { target_table: 'ORDERS', target_schema: 'PUBLIC' }).target,
    ).toEqual({ table: 'ORDERS', schema: 'PUBLIC' });
  });

  it('flags a SQL section started without the required table', () => {
    const { target, error } = assembleTarget('sql', { target_schema: 'PUBLIC' });
    expect(target).toBeNull();
    expect(error?.field).toBe('target_table');
  });

  it('builds a flat-file target with optional format', () => {
    expect(assembleTarget('flatfile', { target_path: 'c/data.csv' }).target).toEqual({
      path: 'c/data.csv',
    });
    expect(
      assembleTarget('flatfile', { target_path: 'c/d.parquet', target_format: 'parquet' }).target,
    ).toEqual({ path: 'c/d.parquet', file_format: 'parquet' });
  });

  it('flags a flat-file section started (format only) without a path', () => {
    const { target, error } = assembleTarget('flatfile', { target_format: 'csv' });
    expect(target).toBeNull();
    expect(error?.field).toBe('target_path');
  });

  it('builds a Unity Catalog target requiring catalog + table', () => {
    expect(
      assembleTarget('uc', {
        target_catalog: 'main',
        target_schema: 'default',
        target_table: 'orders',
      }).target,
    ).toEqual({ catalog: 'main', table: 'orders', schema: 'default' });
  });

  it('flags a UC section missing catalog, then table', () => {
    expect(assembleTarget('uc', { target_table: 'orders' }).error?.field).toBe('target_catalog');
    expect(assembleTarget('uc', { target_catalog: 'main' }).error?.field).toBe('target_table');
  });

  it('trims whitespace and treats blank-only input as absent', () => {
    expect(assembleTarget('sql', { target_table: '  ORDERS  ' }).target).toEqual({ table: 'ORDERS' });
    expect(assembleTarget('sql', { target_table: '   ' }).target).toBeNull();
  });
});
