import { describe, expect, it } from 'vitest';

import { buildCheckPayload } from '../../src/components/checks/checkForm';

/**
 * `buildCheckPayload` (checkForm.ts) — the conditional-field (`ConfigField.showWhen`)
 * half of #593's generic mechanism. `expectationCatalog.test.ts` covers
 * `fieldVisible` itself and the anomaly catalog entry that declares it; this file
 * covers the payload builder that must honor the SAME condition, because antd's
 * `Form` preserves an unmounted field's last value by default — a naive
 * `formToConfig` would happily resubmit a stale `column` after the author
 * switched `target_metric` back to `row_count`, and the backend actively REJECTS
 * that combination (`anomaly_params`: "known key, inapplicable metric").
 */
describe('buildCheckPayload — anomaly conditional column field (#593)', () => {
  it('omits column from the submitted config when target_metric is row_count, even with a stale value present', () => {
    const payload = buildCheckPayload({
      name: 'orders row-count anomaly',
      expectation_type: 'monitor:anomaly',
      config: {
        target_metric: 'row_count',
        // A value antd's Form preserved from before the author switched away
        // from freshness_age_hours — must never reach the backend.
        column: 'loaded_at',
        window: 14,
        min_points: 7,
        seasonality: false,
      },
      fail_threshold: 3,
    });
    expect(payload.config).toEqual({
      target_metric: 'row_count',
      window: 14,
      min_points: 7,
      seasonality: false,
    });
  });

  it('includes column when target_metric is freshness_age_hours', () => {
    const payload = buildCheckPayload({
      name: 'orders freshness anomaly',
      expectation_type: 'monitor:anomaly',
      config: {
        target_metric: 'freshness_age_hours',
        column: 'loaded_at',
        window: 14,
        min_points: 7,
        seasonality: true,
      },
      fail_threshold: 3,
    });
    expect(payload.config).toEqual({
      target_metric: 'freshness_age_hours',
      column: 'loaded_at',
      window: 14,
      min_points: 7,
      seasonality: true,
    });
  });

  it('carries kind=anomaly and the anomaly expectation_type', () => {
    const payload = buildCheckPayload({
      name: 'orders anomaly',
      expectation_type: 'monitor:anomaly',
      config: { target_metric: 'row_count' },
      fail_threshold: 3,
    });
    expect(payload.kind).toBe('anomaly');
    expect(payload.expectation_type).toBe('monitor:anomaly');
  });
});
