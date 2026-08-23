import { describe, expect, it } from 'vitest';

import { RUN_STATUSES, type ResultStatus } from '../../src/api/runs';
import {
  RESULT_STATUS_CHART_COLORS,
  RUN_STATUS_CHART_COLORS,
  runStatusColor,
  severityColor,
} from '../../src/components/charts/chartTheme';

// Mirrors the `ResultStatus` union (type-only, no runtime array in api/runs).
const RESULT_STATUSES: ResultStatus[] = ['pass', 'warn', 'fail', 'critical', 'skip', 'error'];

describe('chart status colours', () => {
  it('maps every result severity to a theme-reactive CSS var', () => {
    for (const status of RESULT_STATUSES) {
      expect(RESULT_STATUS_CHART_COLORS[status]).toMatch(/^var\(--dq-[a-z-]+\)$/);
    }
  });

  it('maps every run status to a theme-reactive CSS var', () => {
    for (const status of RUN_STATUSES) {
      expect(RUN_STATUS_CHART_COLORS[status]).toMatch(/^var\(--dq-[a-z-]+\)$/);
    }
  });

  it('keeps the severity semantics (pass green · fail red · critical magenta)', () => {
    expect(severityColor('pass')).toBe('var(--dq-severity-good)');
    expect(severityColor('fail')).toBe('var(--dq-severity-bad)');
    expect(severityColor('critical')).toBe('var(--dq-critical)');
  });

  it('maps run-status accessor to the matching token', () => {
    expect(runStatusColor('succeeded')).toBe('var(--dq-severity-good)');
    expect(runStatusColor('failed')).toBe('var(--dq-severity-bad)');
  });
});
