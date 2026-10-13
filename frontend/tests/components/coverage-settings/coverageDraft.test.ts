import { describe, expect, it } from 'vitest';

import type { EffectiveCoverage } from '../../../src/api/coverage';
import {
  documentOf,
  draftOf,
  invalidReason,
  isEmpty,
  shortensWindow,
} from '../../../src/components/coverage-settings/coverageDraft';

const D = { warn: 3, fail: 4, critical: 6, source: 'default' as const };
const EFFECTIVE: EffectiveCoverage = {
  sensitivity: { volume: D, freshness: D, profile: D },
  baseline: { window: 8, min_points: 4, seasonality: true, source: 'default' },
};
const LOOSE = { warn: 5, fail: 7, critical: 9 };

describe('coverageDraft', () => {
  it('round-trips a document and leaves unset groups out of it', () => {
    const doc = { sensitivity: { profile: LOOSE } };
    const draft = draftOf(doc);
    expect(draft.sensitivity.volume).toBeNull();
    expect(draft.baseline).toBeNull();
    expect(documentOf(draft)).toEqual(doc);
  });

  it('a draft that sets nothing is empty and saves as {}', () => {
    expect(isEmpty(draftOf(null))).toBe(true);
    expect(documentOf(draftOf({}))).toEqual({});
    expect(isEmpty(draftOf({ baseline: { window: 8, min_points: 4, seasonality: false } }))).toBe(
      false,
    );
  });

  it.each([
    [{ ...LOOSE, warn: null }, 'Column profile: every threshold needs a value.'],
    [{ ...LOOSE, warn: 8 }, 'Column profile: keep the order warn ≤ fail ≤ critical.'],
    [{ ...LOOSE, warn: 0 }, 'Column profile: thresholds must be above 0 and at most 99.'],
    [{ ...LOOSE, critical: 100 }, 'Column profile: thresholds must be above 0 and at most 99.'],
  ])('refuses an invalid threshold group', (profile, reason) => {
    const draft = draftOf(null);
    expect(invalidReason({ ...draft, sensitivity: { ...draft.sensitivity, profile } })).toBe(
      reason,
    );
  });

  it.each([
    [{ window: null, min_points: 4 }, 'History: the window and the minimum both need a value.'],
    [{ window: 2, min_points: 3 }, 'History: the window must be 3 to 90 runs.'],
    [{ window: 91, min_points: 3 }, 'History: the window must be 3 to 90 runs.'],
    [
      { window: 8, min_points: 9 },
      'History: the minimum must be at least 3 and no more than the window.',
    ],
    [
      { window: 8, min_points: 2 },
      'History: the minimum must be at least 3 and no more than the window.',
    ],
  ])('refuses an invalid history group', (shape, reason) => {
    expect(invalidReason({ ...draftOf(null), baseline: { ...shape, seasonality: true } })).toBe(
      reason,
    );
  });

  it('accepts the boundaries and a draft that sets nothing', () => {
    expect(invalidReason(draftOf(null))).toBeNull();
    expect(
      invalidReason(
        draftOf({
          sensitivity: { volume: { warn: 0.1, fail: 0.1, critical: 99 } },
          baseline: { window: 3, min_points: 3, seasonality: false },
        }),
      ),
    ).toBeNull();
  });

  it('flags only a window shorter than the one in effect', () => {
    const withWindow = (window: number) =>
      draftOf({ baseline: { window, min_points: 3, seasonality: true } });
    expect(shortensWindow(withWindow(7), EFFECTIVE, EFFECTIVE)).toBe(true);
    expect(shortensWindow(withWindow(8), EFFECTIVE, EFFECTIVE)).toBe(false);
    expect(shortensWindow(withWindow(9), EFFECTIVE, EFFECTIVE)).toBe(false);
    expect(shortensWindow(draftOf(null), EFFECTIVE, EFFECTIVE)).toBe(false);
  });

  it('flags a level that stops setting a window longer than the one below', () => {
    const longer: EffectiveCoverage = {
      ...EFFECTIVE,
      baseline: { window: 30, min_points: 4, seasonality: true, source: 'workspace' },
    };
    // Switching History off falls from 30 to the built-in 8.
    expect(shortensWindow(draftOf(null), EFFECTIVE, longer)).toBe(true);
  });
});
