import { describe, expect, it } from 'vitest';

import type { ConnectionType } from '../../src/api/connections';
import {
  asBatchStrategy,
  asFileFormat,
  assembleTarget,
  hasCaptureGroup,
  MAX_SAMPLE_ROWS,
  SAMPLING_CAPABLE_TYPES,
  summarizeTarget,
  supportsSampling,
  targetKind,
  targetSampling,
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

    it('flags a section started by picking "specific" alone, rather than silently discarding it', () => {
      // Picking 'specific' with everything else blank is a deliberate action —
      // it must not silently fall through to the all-blank targetless case the
      // way an untouched 'latest' default does.
      const { target, error } = assembleTarget('flatfile', {
        target_mode: 'batch',
        target_strategy: 'specific',
      });
      expect(target).toBeNull();
      expect(error?.field).toBe('target_pattern');
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

  it('annotates a sampled target (#1333 m1)', () => {
    // The Run Now confirmation and the Suites list would otherwise render a
    // sampled and an unsampled suite identically — and "run this" is exactly the
    // moment to know the run will read 100k rows of 5M.
    expect(
      summarizeTarget({ table: 'ORDERS', sampling: { strategy: 'head', rows: 100_000 } }),
    ).toBe('ORDERS · sampled: head 100k');
    expect(
      summarizeTarget({ path: 'raw/o.csv', sampling: { strategy: 'random', rows: 1_500_000 } }),
    ).toBe('raw/o.csv · sampled: random 1.5M');
    expect(summarizeTarget({ table: 'T', sampling: { strategy: 'head', rows: 250 } })).toBe(
      'T · sampled: head 250',
    );
  });

  it('leaves an unsampled or unusable target unannotated', () => {
    expect(summarizeTarget({ table: 'ORDERS' })).toBe('ORDERS');
    // A malformed stored block is not carried by `storedSampling`, so the summary
    // says nothing rather than inventing a cap it can't state.
    expect(summarizeTarget({ table: 'ORDERS', sampling: { strategy: 'head' } })).toBe('ORDERS');
    expect(summarizeTarget({ sampling: { strategy: 'head', rows: 10 } })).toBeNull();
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

describe('asBatchStrategy (#1180)', () => {
  it('passes the two supported strategies through unchanged', () => {
    expect(asBatchStrategy('latest')).toBe('latest');
    expect(asBatchStrategy('specific')).toBe('specific');
  });

  it('narrows anything unsupported or absent to undefined, mirroring asFileFormat', () => {
    // Same reasoning as asFileFormat: strategy is read out of an untyped
    // JSONB bag, so a stray/malformed stored value (hand-edited row, an old
    // schema) must not prefill the Strategy Select with a non-existent
    // option — SuiteForm falls back to 'latest' when this returns undefined.
    expect(asBatchStrategy('weekly')).toBeUndefined();
    expect(asBatchStrategy('LATEST')).toBeUndefined();
    expect(asBatchStrategy('')).toBeUndefined();
    expect(asBatchStrategy(undefined)).toBeUndefined();
  });
});

// ── sampling (#595/#1325) ────────────────────────────────────────────

describe('SAMPLING_CAPABLE_TYPES', () => {
  it('names exactly the datasources the backend accepts a sampling block on', () => {
    // A canary against the backend `registry.SAMPLING_CAPABLE_TYPES`. The
    // absences are the point: Snowflake pushes every expectation down and never
    // materialises rows (a sample there would change nothing while stamping
    // "sampled" on every result), and Iceberg's sampled read is not built. Both
    // are a 422 at save time server-side, so adding one here without adding it
    // there would put a control in the editor whose only outcome is a save error.
    expect([...SAMPLING_CAPABLE_TYPES].sort()).toEqual(['adls_gen2', 's3', 'unity_catalog']);
    const cases: [ConnectionType, boolean][] = [
      ['adls_gen2', true],
      ['s3', true],
      ['unity_catalog', true],
      ['snowflake', false],
      ['iceberg', false],
      ['adf', false],
    ];
    for (const [type, capable] of cases) expect(supportsSampling(type)).toBe(capable);
    expect(supportsSampling(undefined)).toBe(false);
  });
});

describe('assembleTarget — sampling', () => {
  const base = { target_path: 'raw/orders.csv' };

  it('leaves the target untouched when sampling is off', () => {
    expect(assembleTarget('flatfile', base, { connType: 's3' }).target).toEqual({
      path: 'raw/orders.csv',
    });
  });

  it('grafts a head sample onto the datasource target', () => {
    const { target, error } = assembleTarget(
      'flatfile',
      { ...base, sampling_enabled: true, sampling_strategy: 'head', sampling_rows: 100_000 },
      { connType: 's3' },
    );
    expect(error).toBeUndefined();
    expect(target).toEqual({
      path: 'raw/orders.csv',
      sampling: { strategy: 'head', rows: 100_000 },
    });
  });

  it('carries a seed for random', () => {
    expect(
      assembleTarget(
        'uc',
        {
          target_catalog: 'main',
          target_table: 'orders',
          sampling_enabled: true,
          sampling_strategy: 'random',
          sampling_rows: 5_000,
          sampling_seed: 7,
        },
        { connType: 'unity_catalog' },
      ).target,
    ).toEqual({
      catalog: 'main',
      table: 'orders',
      sampling: { strategy: 'random', rows: 5_000, seed: 7 },
    });
  });

  it('drops a stale seed when the strategy is head', () => {
    // Switching random to head leaves the seed field populated. Sending it would
    // be a 422 (the backend refuses a seed on head rather than let an author
    // believe a head sample is seeded-random) — and a stored seed on a head spec
    // would read as a reproducibility guarantee of a different kind than the one
    // head actually gives.
    const { target } = assembleTarget(
      'flatfile',
      {
        ...base,
        sampling_enabled: true,
        sampling_strategy: 'head',
        sampling_rows: 10,
        sampling_seed: 7,
      },
      { connType: 's3' },
    );
    expect(target).toEqual({ path: 'raw/orders.csv', sampling: { strategy: 'head', rows: 10 } });
  });

  it('REFUSES rather than drops a sampling block on a pushdown datasource', () => {
    // The silently-dropped block is the failure mode this whole feature is
    // shaped against: an author would believe a nightly 100M-row suite is
    // bounded when it is not, and the first evidence would be an OOM.
    const { target, error } = assembleTarget(
      'sql',
      { target_table: 'ORDERS', sampling_enabled: true, sampling_rows: 100 },
      { connType: 'snowflake' },
    );
    expect(target).toBeNull();
    expect(error?.field).toBe('sampling_enabled');
    expect(error?.message).toMatch(/pushdown/);
  });

  it('REFUSES a sampling block with no target to bound', () => {
    // A lone `sampling` key is not a runnable target and the backend 422s it.
    const { target, error } = assembleTarget(
      'flatfile',
      { sampling_enabled: true, sampling_rows: 100 },
      { connType: 's3' },
    );
    expect(target).toBeNull();
    expect(error?.field).toBe('sampling_enabled');
  });

  it.each([
    ['missing', undefined],
    ['zero', 0],
    ['negative', -1],
    ['fractional', 1.5],
    ['over the cap', MAX_SAMPLE_ROWS + 1],
  ])('REFUSES a %s row count', (_label, rows) => {
    const { target, error } = assembleTarget(
      'flatfile',
      { ...base, sampling_enabled: true, sampling_rows: rows as number | undefined },
      { connType: 's3' },
    );
    expect(target).toBeNull();
    expect(error?.field).toBe('sampling_rows');
  });

  it('accepts exactly the cap', () => {
    expect(
      assembleTarget(
        'flatfile',
        { ...base, sampling_enabled: true, sampling_rows: MAX_SAMPLE_ROWS },
        { connType: 's3' },
      ).error,
    ).toBeUndefined();
  });

  it('reports the datasource error first when both halves are wrong', () => {
    // A missing path is the more fundamental problem and names the field the
    // author must fix; stacking a sampling error on top would point at the wrong
    // input.
    const { error } = assembleTarget(
      'flatfile',
      { target_format: 'csv', sampling_enabled: true, sampling_rows: 100 },
      { connType: 's3' },
    );
    expect(error?.field).toBe('target_path');
  });
});

describe('targetSampling', () => {
  it('reads a stored block back for prefill, already typed', () => {
    const stored = { path: 'p', sampling: { strategy: 'random', rows: 1000, seed: 3 } };
    expect(targetSampling(stored)).toEqual({ strategy: 'random', rows: 1000, seed: 3 });
  });

  it('returns undefined when there is no block to read', () => {
    expect(targetSampling(null)).toBeUndefined();
    expect(targetSampling(undefined)).toBeUndefined();
    expect(targetSampling({ path: 'p' })).toBeUndefined();
    // Not an object → not a block. A hand-edited JSONB row can hold anything.
    expect(targetSampling({ sampling: 'head' })).toBeUndefined();
    expect(targetSampling({ sampling: ['head'] })).toBeUndefined();
  });

  it('narrows each field, so junk never prefills a control', () => {
    // A present-but-malformed block still returns an object (there IS a block),
    // with the unusable fields dropped — the Select falls back to its default
    // rather than showing an option that does not exist.
    expect(targetSampling({ sampling: { strategy: 'HEAD', rows: '100', seed: null } })).toEqual({
      strategy: undefined,
      rows: undefined,
      seed: undefined,
    });
  });
});

describe('assembleTarget — sampling carry-forward (#1333 F3)', () => {
  const stored = { strategy: 'head', rows: 100_000 } as const;

  it('PRESERVES a stored block when the sampling section never mounted', () => {
    // `validateFields()` returns registered fields only, so an unmounted section
    // reports no `sampling_enabled` at all. Treating that as "turned off" deletes
    // the row cap on a save that only touched the description — silently, and the
    // nightly suite reverts to the full scan the feature exists to prevent.
    const { target, error } = assembleTarget(
      'flatfile',
      { target_path: 'raw/orders.csv' }, // no sampling_* keys — section not rendered
      { connType: 's3', stored },
    );

    expect(error).toBeUndefined();
    expect(target).toEqual({ path: 'raw/orders.csv', sampling: stored });
  });

  it('preserves it even on a datasource the frontend list calls incapable', () => {
    // The drift case: nothing mechanically pins the frontend capability list to
    // the backend's, so the carry-forward must not depend on agreeing with it.
    const { target } = assembleTarget(
      'sql',
      { target_table: 'ORDERS' },
      { connType: 'snowflake', stored },
    );

    expect(target).toEqual({ table: 'ORDERS', sampling: stored });
  });

  it('still clears the block on an EXPLICIT "whole dataset" choice', () => {
    // The counter-case: carrying forward must not make sampling impossible to
    // turn off. `false` is the author speaking; `undefined` is the form's silence.
    const { target } = assembleTarget(
      'flatfile',
      { target_path: 'raw/orders.csv', sampling_enabled: false },
      { connType: 's3', stored },
    );

    expect(target).toEqual({ path: 'raw/orders.csv' });
  });

  it('carries nothing forward for a suite that had no block', () => {
    const { target } = assembleTarget(
      'flatfile',
      { target_path: 'raw/orders.csv' },
      { connType: 's3' },
    );

    expect(target).toEqual({ path: 'raw/orders.csv' });
  });
});
