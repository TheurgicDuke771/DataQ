import { describe, expect, it } from 'vitest';

import {
  formatDuration,
  formatScalar,
  formatTimestamp,
  pipelineStatusColor,
  RESULT_STATUS_COLORS,
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

describe('status colour maps', () => {
  it('maps every run status and result status to a colour', () => {
    expect(RUN_STATUS_COLORS.succeeded).toBe('success');
    expect(RUN_STATUS_COLORS.failed).toBe('error');
    expect(RESULT_STATUS_COLORS.warn).toBe('warning');
    expect(RESULT_STATUS_COLORS.critical).toBe('magenta');
  });

  it('maps pipeline statuses with a default fallback', () => {
    expect(pipelineStatusColor('succeeded')).toBe('success');
    expect(pipelineStatusColor('failed')).toBe('error');
    expect(pipelineStatusColor('something-new')).toBe('default');
  });
});
