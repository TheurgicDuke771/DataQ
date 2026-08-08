import { describe, expect, it } from 'vitest';

import type { ConnectionType } from '../../src/api/connections';
import {
  asFileFormat,
  assembleTarget,
  hasCaptureGroup,
  summarizeTarget,
  targetKind,
} from '../../src/components/suites/suiteTarget';

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

  describe('flat-file batch mode (#1180)', () => {
    it('returns a null target AND no error when the batch section is untouched', () => {
      const { target, error } = assembleTarget('flatfile', { target_mode: 'batch' });
      expect(target).toBeNull();
      expect(error).toBeUndefined();
    });

    it('builds a "latest" batch target, defaulting the strategy and omitting an empty prefix', () => {
      expect(
        assembleTarget('flatfile', {
          target_mode: 'batch',
          target_pattern: 'tracking_events_([a-z_]+)\\.csv',
        }).target,
      ).toEqual({ pattern: 'tracking_events_([a-z_]+)\\.csv', strategy: 'latest' });

      expect(
        assembleTarget('flatfile', {
          target_mode: 'batch',
          target_prefix: 'adls_flatfile/logistics_tracking/',
          target_pattern: 'tracking_events_([a-z_]+)\\.csv',
          target_strategy: 'latest',
        }).target,
      ).toEqual({
        prefix: 'adls_flatfile/logistics_tracking/',
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'latest',
      });
    });

    it('builds a "specific" batch target carrying the batch key', () => {
      expect(
        assembleTarget('flatfile', {
          target_mode: 'batch',
          target_pattern: 'tracking_events_([a-z_]+)\\.csv',
          target_strategy: 'specific',
          target_batch: 'ready',
        }).target,
      ).toEqual({
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'specific',
        batch: 'ready',
      });
    });

    it('flags a batch section started without the required pattern', () => {
      const { target, error } = assembleTarget('flatfile', {
        target_mode: 'batch',
        target_prefix: 'adls_flatfile/logistics_tracking/',
      });
      expect(target).toBeNull();
      expect(error?.field).toBe('target_pattern');
    });

    it('flags a "specific" strategy with no batch key', () => {
      const { target, error } = assembleTarget('flatfile', {
        target_mode: 'batch',
        target_pattern: 'tracking_events_([a-z_]+)\\.csv',
        target_strategy: 'specific',
      });
      expect(target).toBeNull();
      expect(error?.field).toBe('target_batch');
    });

    it('flags a "specific" strategy whose pattern has no capture group', () => {
      const { target, error } = assembleTarget('flatfile', {
        target_mode: 'batch',
        target_pattern: 'tracking_events_ready.csv',
        target_strategy: 'specific',
        target_batch: 'ready',
      });
      expect(target).toBeNull();
      expect(error?.field).toBe('target_pattern');
    });

    it('does not require a capture group for the default "latest" strategy', () => {
      // latest picks the greatest key lexically over the whole match — no
      // capture group needed, unlike 'specific' which must extract one.
      expect(
        assembleTarget('flatfile', {
          target_mode: 'batch',
          target_pattern: 'tracking_events_ready.csv',
        }).target,
      ).toEqual({ pattern: 'tracking_events_ready.csv', strategy: 'latest' });
    });
  });
});

describe('hasCaptureGroup', () => {
  it('detects a plain capturing group', () => {
    expect(hasCaptureGroup('tracking_events_([a-z_]+)\\.csv')).toBe(true);
  });

  it('detects Python and JS named capturing groups', () => {
    expect(hasCaptureGroup('tracking_events_(?P<key>[a-z_]+)\\.csv')).toBe(true);
    expect(hasCaptureGroup('tracking_events_(?<key>[a-z_]+)\\.csv')).toBe(true);
  });

  it('rejects a pattern with no parentheses at all', () => {
    expect(hasCaptureGroup('tracking_events_ready.csv')).toBe(false);
  });

  it('does not count a non-capturing group or a lookaround as a capture group', () => {
    expect(hasCaptureGroup('tracking_events_(?:[a-z_]+)\\.csv')).toBe(false);
    expect(hasCaptureGroup('tracking_events_(?=ready)\\.csv')).toBe(false);
    expect(hasCaptureGroup('tracking_events_(?!ready)\\.csv')).toBe(false);
    expect(hasCaptureGroup('tracking_events_(?<=ready)\\.csv')).toBe(false);
    expect(hasCaptureGroup('tracking_events_(?<!ready)\\.csv')).toBe(false);
  });

  it('does not count an escaped literal paren as a capture group', () => {
    expect(hasCaptureGroup('tracking_events_\\(ready\\).csv')).toBe(false);
  });
});

describe('summarizeTarget (#1180)', () => {
  it('summarizes a batch target by prefix + pattern + strategy, not a resolved file', () => {
    expect(
      summarizeTarget({
        prefix: 'adls_flatfile/logistics_tracking/',
        pattern: 'tracking_events_([a-z_]+)\\.csv',
        strategy: 'latest',
      }),
    ).toBe('adls_flatfile/logistics_tracking/tracking_events_([a-z_]+)\\.csv (latest)');
  });

  it('summarizes a batch target with no prefix', () => {
    expect(
      summarizeTarget({ pattern: 'tracking_events_([a-z_]+)\\.csv', strategy: 'specific' }),
    ).toBe('tracking_events_([a-z_]+)\\.csv (specific)');
  });

  it('still prefers a literal path over a pattern (mutually exclusive, but path wins if both present)', () => {
    expect(summarizeTarget({ path: 'c/data.csv', pattern: 'ignored_(x)' })).toBe('c/data.csv');
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
