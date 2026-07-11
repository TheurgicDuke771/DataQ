import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { AssetSummary, RunOutcome } from '../../src/api/assets';
import { AssetHealthTag } from '../../src/components/assets/AssetHealthTag';
import { assetHealth, runHealth } from '../../src/components/assets/health';

type SummaryHealthInput = Pick<
  AssetSummary,
  'worst_severity' | 'last_run_at' | 'has_failed_run' | 'has_active_run'
>;

const CLEAN: SummaryHealthInput = {
  worst_severity: null,
  last_run_at: '2026-01-01T00:00:00Z',
  has_failed_run: false,
  has_active_run: false,
};

const PASSING_RUN: RunOutcome = {
  run_id: 'r1',
  status: 'succeeded',
  worst_severity: null,
  checks_total: 3,
  checks_passed: 3,
  finished_at: '2026-01-01T00:00:00Z',
  created_at: '2026-01-01T00:00:00Z',
};

describe('assetHealth', () => {
  it('maps each failing severity to its tier', () => {
    expect(assetHealth({ ...CLEAN, worst_severity: 'warn' }).label).toBe('Warning');
    expect(assetHealth({ ...CLEAN, worst_severity: 'fail' }).label).toBe('Failing');
    expect(assetHealth({ ...CLEAN, worst_severity: 'critical' }).label).toBe('Critical');
  });

  it('surfaces an operationally-failed run as an error, never green', () => {
    expect(assetHealth({ ...CLEAN, has_failed_run: true })).toEqual({
      label: 'Run failed',
      color: 'error',
    });
  });

  it('check severity outranks a run failure (worse signal wins)', () => {
    expect(assetHealth({ ...CLEAN, worst_severity: 'critical', has_failed_run: true }).label).toBe(
      'Critical',
    );
  });

  it('shows an active run as in-progress, not green', () => {
    expect(assetHealth({ ...CLEAN, has_active_run: true })).toEqual({
      label: 'Running',
      color: 'processing',
    });
  });

  it('is Passing only for a finished, clean run', () => {
    expect(assetHealth(CLEAN)).toEqual({ label: 'Passing', color: 'success' });
  });

  it('is No runs when nothing has run', () => {
    expect(assetHealth({ ...CLEAN, last_run_at: null })).toEqual({
      label: 'No runs',
      color: 'default',
    });
  });
});

describe('runHealth', () => {
  it('is No runs without a run', () => {
    expect(runHealth({ ...PASSING_RUN, run_id: null }).label).toBe('No runs');
  });

  it('maps a clean succeeded run to Passing and severities to their tiers', () => {
    expect(runHealth(PASSING_RUN)).toEqual({ label: 'Passing', color: 'success' });
    expect(runHealth({ ...PASSING_RUN, worst_severity: 'fail' }).label).toBe('Failing');
  });

  it('maps run execution statuses (failed/queued/running/cancelled) — never green', () => {
    expect(runHealth({ ...PASSING_RUN, status: 'failed' })).toEqual({
      label: 'Run failed',
      color: 'error',
    });
    expect(runHealth({ ...PASSING_RUN, status: 'queued' }).label).toBe('Queued');
    expect(runHealth({ ...PASSING_RUN, status: 'running' })).toEqual({
      label: 'Running',
      color: 'processing',
    });
    expect(runHealth({ ...PASSING_RUN, status: 'cancelled' }).label).toBe('Cancelled');
  });
});

describe('AssetHealthTag', () => {
  it('renders the derived health', () => {
    render(<AssetHealthTag summary={{ ...CLEAN, worst_severity: 'critical' }} />);
    expect(screen.getByText('Critical')).toBeInTheDocument();
  });
});
