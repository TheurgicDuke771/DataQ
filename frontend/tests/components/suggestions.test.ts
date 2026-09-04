import { describe, expect, it } from 'vitest';

import { suggestionToCheck } from '../../src/components/suites/suggestions';

describe('suggestionToCheck', () => {
  it('maps an expectation suggestion to the editor payload with no thresholds', () => {
    expect(
      suggestionToCheck({
        expectation_type: 'expect_column_values_to_not_be_null',
        name: 'order_id not null',
        rationale: 'r',
        config: { column: 'order_id' },
        dimension: 'completeness',
      }),
    ).toEqual({
      name: 'order_id not null',
      kind: 'expectation',
      expectation_type: 'expect_column_values_to_not_be_null',
      config: { column: 'order_id' },
      dimension: 'completeness',
      fail_threshold: null,
    });
  });

  it('maps a freshness suggestion to the monitor kind with its hour threshold', () => {
    const payload = suggestionToCheck({
      expectation_type: 'monitor:freshness',
      name: 'orders arrive daily',
      rationale: 'r',
      config: { timestamp_column: 'order_ts' },
      dimension: 'timeliness',
      fail_threshold_hours: 26,
    });
    expect(payload.kind).toBe('freshness');
    expect(payload.fail_threshold).toBe(26);
  });

  it('leaves the dimension to the backend default when the suggestion has none', () => {
    const payload = suggestionToCheck({
      expectation_type: 'expect_column_values_to_be_unique',
      name: 'n',
      rationale: 'r',
      config: { column: 'id' },
      dimension: null,
    });
    expect(payload.dimension).toBeUndefined();
  });
});
