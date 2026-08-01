import { describe, expect, it } from 'vitest';

import {
  anomalyColdStartHint,
  formatDuration,
  formatDurationMs,
  formatScalar,
  formatTimestamp,
  isWithinWindowDays,
  pipelineRunMarker,
  pipelineStatusColor,
  RESULT_STATUS_COLORS,
  RUN_BAR_STATUS,
  runReportTitle,
  RUN_STATUS_COLORS,
} from '../../src/components/results/resultsFormat';

describe('formatScalar', () => {
  it('returns an em dash for null or undefined', () => {
    expect(formatScalar(null)).toBe('—');
    expect(formatScalar(undefined)).toBe('—');
  });

  it('renders falsy scalars as themselves, not the em dash', () => {
    expect(formatScalar(0)).toBe('0');
    expect(formatScalar(false)).toBe('false');
    expect(formatScalar('')).toBe('');
  });

  it('JSON-stringifies objects and arrays', () => {
    expect(formatScalar({ a: 1 })).toBe('{"a":1}');
    expect(formatScalar([1, 2])).toBe('[1,2]');
  });

  it('stringifies plain scalars', () => {
    expect(formatScalar('PUBLIC')).toBe('PUBLIC');
    expect(formatScalar(9999)).toBe('9999');
  });
});

describe('formatDuration', () => {
  it('returns an em dash when either bound is missing', () => {
    expect(formatDuration(null, '2026-06-11T00:00:10Z')).toBe('—');
    expect(formatDuration('2026-06-11T00:00:00Z', null)).toBe('—');
    expect(formatDuration(null, null)).toBe('—');
  });

  it('returns an em dash for a negative interval (clock skew)', () => {
    expect(formatDuration('2026-06-11T00:00:10Z', '2026-06-11T00:00:00Z')).toBe('—');
  });

  it('formats sub-second, seconds, and minute+second spans', () => {
    expect(formatDuration('2026-06-11T00:00:00.000Z', '2026-06-11T00:00:00.850Z')).toBe('850ms');
    expect(formatDuration('2026-06-11T00:00:00Z', '2026-06-11T00:00:12Z')).toBe('12s');
    expect(formatDuration('2026-06-11T00:00:00Z', '2026-06-11T00:01:03Z')).toBe('1m 3s');
  });
});

describe('formatTimestamp', () => {
  it('returns an em dash for null or unparseable input', () => {
    expect(formatTimestamp(null)).toBe('—');
    expect(formatTimestamp('not-a-date')).toBe('—');
  });

  it('returns a non-empty locale string for a valid ISO timestamp', () => {
    const out = formatTimestamp('2026-06-11T00:00:00Z');
    expect(out).not.toBe('—');
    expect(out.length).toBeGreaterThan(0);
  });
});

describe('isWithinWindowDays', () => {
  it('treats null or unparseable timestamps as out of window', () => {
    expect(isWithinWindowDays(null, 7)).toBe(false);
    expect(isWithinWindowDays('not-a-date', 7)).toBe(false);
  });

  it('includes a just-now timestamp and excludes one past the window', () => {
    const now = new Date().toISOString();
    const tenDaysAgo = new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString();
    expect(isWithinWindowDays(now, 7)).toBe(true);
    expect(isWithinWindowDays(tenDaysAgo, 7)).toBe(false);
    expect(isWithinWindowDays(tenDaysAgo, 30)).toBe(true);
  });
});

describe('status colour maps', () => {
  // Asserted as WHOLE objects, not key-by-key (#563). The previous version claimed
  // "every run status and result status" while asserting 4 of 11, so mutating any
  // of the other 7 colours survived — the test's name was true of its intent and
  // false of its assertions. Comparing the entire map also makes an added status a
  // failing test here rather than a silently uncovered key.
  it('maps every run status to a tag colour', () => {
    expect(RUN_STATUS_COLORS).toEqual({
      queued: 'default',
      running: 'processing',
      succeeded: 'success',
      failed: 'error',
      cancelled: 'warning',
    });
  });

  it('maps every run status to a progress-bar status', () => {
    expect(RUN_BAR_STATUS).toEqual({
      queued: 'normal',
      running: 'active',
      succeeded: 'success',
      failed: 'exception',
      cancelled: 'exception',
    });
  });

  it('maps every result status to a tag colour', () => {
    // Severity tiers (ADR 0005) plus the two operational statuses (#122). `skip`
    // and `error` are the ones an operator most needs to tell apart from a pass,
    // so their colours are as load-bearing as the severity ones.
    expect(RESULT_STATUS_COLORS).toEqual({
      pass: 'success',
      warn: 'warning',
      fail: 'error',
      critical: 'magenta',
      skip: 'default',
      error: 'volcano',
    });
  });

  it('maps pipeline statuses with a default fallback', () => {
    expect(pipelineStatusColor('succeeded')).toBe('success');
    expect(pipelineStatusColor('failed')).toBe('error');
    expect(pipelineStatusColor('something-new')).toBe('default');
  });

  it('builds the provider:dag:run_id correlation marker', () => {
    expect(
      pipelineRunMarker({
        id: 'p1',
        provider: 'adf',
        connection_id: 'c1',
        provider_run_id: 'seed-adf-0001',
        pipeline_or_dag_id: 'daily_orders_load',
        env: 'prod',
        status: 'succeeded',
        started_at: null,
        finished_at: null,
        failure_reason: null,
        created_at: '2026-06-11T00:00:00Z',
      }),
    ).toBe('adf:daily_orders_load:seed-adf-0001');
  });
});

describe('anomalyColdStartHint (#593 cold start)', () => {
  it('renders a friendly "collecting history" hint for a cold-start payload', () => {
    expect(
      anomalyColdStartHint({
        target_metric: 'row_count',
        value: 32840,
        points: 3,
        window: 14,
        min_points: 7,
        seasonality: false,
        insufficient_history: true,
        reason: 'insufficient_history',
      }),
    ).toBe('Collecting history: 3 of 7 points');
  });

  it('returns null for a scored (non-cold-start) anomaly payload', () => {
    expect(
      anomalyColdStartHint({
        target_metric: 'row_count',
        value: 32840,
        points: 14,
        z_score: 4.2,
        mean: 30000,
        stddev: 600,
        deviation: 2840,
        degenerate_stddev: false,
      }),
    ).toBeNull();
  });

  it('returns null for null/undefined/non-object/malformed payloads', () => {
    expect(anomalyColdStartHint(null)).toBeNull();
    expect(anomalyColdStartHint(undefined)).toBeNull();
    expect(anomalyColdStartHint('insufficient_history')).toBeNull();
    // insufficient_history flagged but the counts are missing/wrong-typed —
    // render nothing rather than a guessed "undefined of undefined points".
    expect(anomalyColdStartHint({ insufficient_history: true })).toBeNull();
    expect(
      anomalyColdStartHint({ insufficient_history: true, points: '3', min_points: 7 }),
    ).toBeNull();
  });

  it('is not fooled by a truthy-but-not-true insufficient_history', () => {
    expect(anomalyColdStartHint({ insufficient_history: 1, points: 3, min_points: 7 })).toBeNull();
  });
});

describe('runReportTitle (#345 — PDF report title / Save-as-PDF filename)', () => {
  it('leads with the suite name and includes a short run id', () => {
    const title = runReportTitle('Orders quality', { id: 'r1234567890', suite_id: 's1' });
    expect(title.startsWith('Orders quality')).toBe(true);
    expect(title).toContain('r1234567');
    expect(title).toContain('DataQ');
  });

  it('falls back to a suite-id stub when the suite name is unknown', () => {
    const title = runReportTitle(null, { id: 'r1', suite_id: 's1234567890' });
    expect(title).toContain('s1234567');
  });
});

describe('formatDurationMs', () => {
  it('formats sub-second, seconds, and minutes', () => {
    expect(formatDurationMs(850)).toBe('850ms');
    expect(formatDurationMs(12_000)).toBe('12s');
    expect(formatDurationMs(63_000)).toBe('1m 3s');
  });

  it('rounds fractional milliseconds', () => {
    expect(formatDurationMs(850.6)).toBe('851ms');
  });

  it('em-dashes negative and NaN intervals', () => {
    expect(formatDurationMs(-1)).toBe('—');
    expect(formatDurationMs(Number.NaN)).toBe('—');
  });
});
