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
  it('maps every result severity to a hex series colour', () => {
    for (const status of RESULT_STATUSES) {
      expect(RESULT_STATUS_CHART_COLORS[status]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('maps every run status to a hex series colour', () => {
    for (const status of RUN_STATUSES) {
      expect(RUN_STATUS_CHART_COLORS[status]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('keeps the severity semantics (pass green · fail red · critical magenta)', () => {
    expect(severityColor('pass')).toBe('#52c41a');
    expect(severityColor('fail')).toBe('#ff4d4f');
    expect(severityColor('critical')).toBe('#eb2f96');
  });

  it('maps run-status accessor to the matching token', () => {
    expect(runStatusColor('succeeded')).toBe('#52c41a');
    expect(runStatusColor('failed')).toBe('#ff4d4f');
  });
});
