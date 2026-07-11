import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { RunOutcome } from '../../src/api/assets';
import { AssetHealthTag } from '../../src/components/assets/AssetHealthTag';
import { assetHealth, healthOf, runHealth } from '../../src/components/assets/health';

describe('asset health derivation', () => {
  it('maps each failing severity to its tier', () => {
    expect(healthOf('warn', true).label).toBe('Warning');
    expect(healthOf('fail', true).label).toBe('Failing');
    expect(healthOf('critical', true).label).toBe('Critical');
  });

  it('is Passing when a run exists and nothing failed', () => {
    expect(healthOf(null, true)).toEqual({ label: 'Passing', color: 'success' });
  });

  it('is No runs when nothing has run', () => {
    expect(healthOf(null, false)).toEqual({ label: 'No runs', color: 'default' });
  });

  it('derives asset health from the summary aggregation', () => {
    expect(assetHealth({ worst_severity: 'fail', last_run_at: '2026-01-01T00:00:00Z' }).label).toBe(
      'Failing',
    );
    expect(assetHealth({ worst_severity: null, last_run_at: null }).label).toBe('No runs');
  });

  it('derives per-suite health from its latest run', () => {
    const run: RunOutcome = {
      run_id: 'r1',
      status: 'succeeded',
      worst_severity: null,
      checks_total: 3,
      checks_passed: 3,
      finished_at: null,
      created_at: null,
    };
    expect(runHealth(run).label).toBe('Passing');
    expect(runHealth({ ...run, run_id: null }).label).toBe('No runs');
  });

  it('renders the health tag', () => {
    render(<AssetHealthTag summary={{ worst_severity: 'critical', last_run_at: '2026-01-01' }} />);
    expect(screen.getByText('Critical')).toBeInTheDocument();
  });
});
