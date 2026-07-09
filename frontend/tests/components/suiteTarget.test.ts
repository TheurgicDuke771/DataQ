import { describe, expect, it } from 'vitest';

import type { ConnectionType } from '../../src/api/connections';
import { asFileFormat, assembleTarget, targetKind } from '../../src/components/suites/suiteTarget';

describe('targetKind', () => {
  it('maps each datasource type to its input shape; orchestration → null', () => {
    const cases: [ConnectionType, ReturnType<typeof targetKind>][] = [
      ['snowflake', 'sql'],
      ['unity_catalog', 'uc'],
      ['iceberg', 'iceberg'],
      ['adls_gen2', 'flatfile'],
      ['s3', 'flatfile'],
      ['adf', null],
      ['airflow', null],
      ['dbt', null],
    ];
    for (const [type, kind] of cases) expect(targetKind(type)).toBe(kind);
  });
});

describe('assembleTarget', () => {
  it('returns a null target AND no error when nothing is filled (valid targetless suite)', () => {
    // The all-blank short-circuit must yield a clean targetless suite, not a
    // missing-field error — asserting error===undefined here pins that each
    // kind's `if (all blank) return null` guard runs before the required-field
    // checks (a dropped guard would still leave target=null but set an error).
    for (const kind of ['sql', 'uc', 'flatfile', 'iceberg'] as const) {
      const { target, error } = assembleTarget(kind, {});
      expect(target).toBeNull();
      expect(error).toBeUndefined();
    }
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

  it('builds an Iceberg target requiring table, folding an optional namespace', () => {
    expect(assembleTarget('iceberg', { target_table: 'orders' }).target).toEqual({
      table: 'orders',
    });
    expect(
      assembleTarget('iceberg', { target_namespace: 'sales', target_table: 'orders' }).target,
    ).toEqual({ table: 'orders', namespace: 'sales' });
  });

  it('flags an Iceberg section started (namespace only) without a table', () => {
    const { target, error } = assembleTarget('iceberg', { target_namespace: 'sales' });
    expect(target).toBeNull();
    expect(error?.field).toBe('target_table');
  });

  it('trims whitespace and treats blank-only input as absent', () => {
    expect(assembleTarget('sql', { target_table: '  ORDERS  ' }).target).toEqual({
      table: 'ORDERS',
    });
    expect(assembleTarget('sql', { target_table: '   ' }).target).toBeNull();
  });
});

describe('asFileFormat', () => {
  it('passes the two supported formats through unchanged', () => {
    expect(asFileFormat('csv')).toBe('csv');
    expect(asFileFormat('parquet')).toBe('parquet');
  });

  it('narrows anything unsupported or absent to undefined', () => {
    // The guard exists so a stray stored value can't prefill the format Select
    // with a non-existent option — case-sensitive, exact match only.
    expect(asFileFormat('json')).toBeUndefined();
    expect(asFileFormat('CSV')).toBeUndefined();
    expect(asFileFormat('')).toBeUndefined();
    expect(asFileFormat(undefined)).toBeUndefined();
  });
});
