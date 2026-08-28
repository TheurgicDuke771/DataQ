import { describe, expect, it } from 'vitest';

import type { SuiteDeletionImpact } from '../../src/api/suites';
import { describeDeletionImpact } from '../../src/components/suites/deletionImpact';

function impact(overrides: Partial<SuiteDeletionImpact> = {}): SuiteDeletionImpact {
  return { checks: 0, runs: 0, results: 0, trigger_bindings: 0, schedules: 0, ...overrides };
}

describe('describeDeletionImpact', () => {
  it('states the core counts with thousands separators', () => {
    expect(describeDeletionImpact(impact({ checks: 12, runs: 431, results: 5180 }))).toBe(
      'Deletes 12 checks, 431 runs and 5,180 results. This cannot be undone.',
    );
  });

  it('renders zeros plainly for a never-run suite, not a special scary warning', () => {
    expect(describeDeletionImpact(impact())).toBe(
      'Deletes 0 checks, 0 runs and 0 results. This cannot be undone.',
    );
  });

  it('singularizes a lone check/run/result', () => {
    expect(describeDeletionImpact(impact({ checks: 1, runs: 1, results: 1 }))).toBe(
      'Deletes 1 check, 1 run and 1 result. This cannot be undone.',
    );
  });

  it('mentions both trigger bindings and schedules with a plural verb (compound subject)', () => {
    expect(describeDeletionImpact(impact({ trigger_bindings: 1, schedules: 2 }))).toBe(
      'Deletes 0 checks, 0 runs and 0 results. 1 trigger binding and 2 schedules point ' +
        'at this suite and will be removed. This cannot be undone.',
    );
  });

  it('agrees the verb with a single linked kind (singular)', () => {
    expect(describeDeletionImpact(impact({ schedules: 1 }))).toBe(
      'Deletes 0 checks, 0 runs and 0 results. 1 schedule points at this suite ' +
        'and will be removed. This cannot be undone.',
    );
  });

  it('agrees the verb with a single linked kind (plural)', () => {
    expect(describeDeletionImpact(impact({ trigger_bindings: 3 }))).toBe(
      'Deletes 0 checks, 0 runs and 0 results. 3 trigger bindings point at this suite ' +
        'and will be removed. This cannot be undone.',
    );
  });
});
