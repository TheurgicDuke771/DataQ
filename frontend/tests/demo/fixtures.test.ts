import { describe, expect, it } from 'vitest';

import {
  daysSince,
  fixtureKey,
  lookup,
  shiftDates,
  type FixtureBundle,
} from '../../src/demo/fixtures';

const bundle = (keys: string[]): FixtureBundle => ({
  capturedAt: '2026-10-01T00:00:00Z',
  routes: [],
  entries: Object.fromEntries(keys.map((k) => [k, { status: 200, data: k }])),
});

describe('fixtureKey', () => {
  it('drops the API prefix and sorts the query', () => {
    expect(fixtureKey('get', '/api/v1/runs?offset=0&limit=50')).toBe('GET /runs?limit=50&offset=0');
  });

  it('is the same key whether or not the URL carries the prefix or a trailing slash', () => {
    expect(fixtureKey('GET', '/suites/')).toBe(fixtureKey('GET', '/api/v1/suites'));
  });

  it('keys a timestamp by its distance from now, so it matches at replay', () => {
    const recordedAt = Date.parse('2026-10-08T10:00:03Z');
    const replayedAt = Date.parse('2026-11-27T08:30:00Z');
    const recorded = fixtureKey('GET', '/runs?since=2026-10-01T10:00:00.000Z', recordedAt);
    const replayed = fixtureKey('GET', '/runs?since=2026-11-20T08:30:00.000Z', replayedAt);
    expect(recorded).toBe('GET /runs?since=<now-168h>');
    expect(replayed).toBe(recorded);
  });

  it('keeps two time windows apart', () => {
    const now = Date.parse('2026-10-08T10:00:00Z');
    expect(fixtureKey('GET', '/runs?since=2026-10-07T10:00:00Z', now)).not.toBe(
      fixtureKey('GET', '/runs?since=2026-10-01T10:00:00Z', now),
    );
  });

  it('keeps a plain value distinct', () => {
    expect(fixtureKey('GET', '/runs?status=failed')).not.toBe(
      fixtureKey('GET', '/runs?status=passed'),
    );
  });
});

describe('lookup', () => {
  it('returns the exact recording', () => {
    const found = lookup(
      bundle(['GET /runs?limit=8', 'GET /runs?limit=200']),
      'GET',
      '/runs?limit=8',
    );
    expect(found).toEqual({
      response: { status: 200, data: 'GET /runs?limit=8' },
      approximate: false,
    });
  });

  it('falls back to the same path sharing the most parameters, and says so', () => {
    const b = bundle(['GET /runs?limit=200', 'GET /runs?limit=200&suite_id=a']);
    const found = lookup(b, 'GET', '/runs?limit=200&suite_id=a&status=failed');
    expect(found?.approximate).toBe(true);
    expect(found?.response.data).toBe('GET /runs?limit=200&suite_id=a');
  });

  it('prefers the recording with fewer unrelated parameters on a tie', () => {
    const b = bundle(['GET /runs?suite_id=a', 'GET /runs']);
    expect(lookup(b, 'GET', '/runs?status=failed')?.response.data).toBe('GET /runs');
  });

  it('does not match a longer path that merely starts the same', () => {
    expect(lookup(bundle(['GET /runs/abc']), 'GET', '/runs')).toBeNull();
    expect(lookup(bundle(['GET /runs-archive']), 'GET', '/runs')).toBeNull();
  });

  it('does not answer one method with another', () => {
    expect(lookup(bundle(['GET /suites']), 'POST', '/suites')).toBeNull();
  });
});

describe('shiftDates', () => {
  const CAPTURED = Date.parse('2026-10-01T00:00:00Z');

  it('moves timestamps and dates by whole days, at any depth, keeping the time of day', () => {
    const shifted = shiftDates(
      {
        at: '2026-09-30T23:15:00.123456+00:00',
        day: '2026-12-31',
        rows: [{ ts: '2026-08-30T01:00:00Z' }],
      },
      2,
      CAPTURED,
    );
    expect(shifted).toEqual({
      at: '2026-10-02T23:15:00.123456+00:00',
      day: '2027-01-02',
      rows: [{ ts: '2026-09-01T01:00:00Z' }],
    });
  });

  it('leaves a date far from the recording as recorded: a threshold or a row value', () => {
    const value = { min_value: '2024-01-01', born: '1990-05-17T00:00:00Z', expires: '2029-01-01' };
    expect(shiftDates(value, 9, CAPTURED)).toEqual(value);
  });

  it('leaves everything that is not a date alone', () => {
    const value = { cron: '0 2 * * *', id: '2026-10', n: 20261001, ok: true, none: null };
    expect(shiftDates(value, 5, CAPTURED)).toEqual(value);
  });

  it('returns the same object when there is nothing to shift', () => {
    const value = { at: '2026-10-01T00:00:00Z' };
    expect(shiftDates(value, 0, CAPTURED)).toBe(value);
  });
});

describe('daysSince', () => {
  const at = '2026-10-01T12:00:00Z';
  it('counts whole days', () => {
    expect(daysSince(at, Date.parse('2026-10-04T11:59:00Z'))).toBe(2);
    expect(daysSince(at, Date.parse('2026-10-04T12:00:00Z'))).toBe(3);
  });
  it('is never negative and survives a bad timestamp', () => {
    expect(daysSince(at, Date.parse('2026-09-01T00:00:00Z'))).toBe(0);
    expect(daysSince('not a date')).toBe(0);
  });
});
