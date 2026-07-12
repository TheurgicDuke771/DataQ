import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { AssetSummary, RunOutcome } from '../../src/api/assets';
import { AssetHealthTag } from '../../src/components/assets/AssetHealthTag';
import {
  assetHealth,
  connectionHealth,
  runHealth,
  suiteHealth,
} from '../../src/components/assets/health';

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

// ── the two split axes (#803) ────────────────────────────────────────────────
// The point of the split: "could we reach it?" and "is the data good?" are
// different questions, and neither may answer for the other.

type ConnInput = Pick<
  AssetSummary,
  'has_operational_error' | 'has_skip' | 'has_active_run' | 'last_run_at'
>;
type SuiteInput = Pick<
  AssetSummary,
  'worst_severity' | 'checks_total' | 'has_active_run' | 'last_run_at'
>;

const CONN_OK: ConnInput = {
  has_operational_error: false,
  has_skip: false,
  has_active_run: false,
  last_run_at: '2026-01-01T00:00:00Z',
};
const SUITE_OK: SuiteInput = {
  worst_severity: null,
  checks_total: 3,
  has_active_run: false,
  last_run_at: '2026-01-01T00:00:00Z',
};

describe('connectionHealth (#803)', () => {
  it('is Reachable for a clean concluded run', () => {
    expect(connectionHealth(CONN_OK)).toEqual({ label: 'Reachable', color: 'success' });
  });

  it('is Errors when DataQ could not execute (failed run or a check that threw)', () => {
    expect(connectionHealth({ ...CONN_OK, has_operational_error: true })).toEqual({
      label: 'Errors',
      color: 'error',
    });
  });

  it('is Degraded on a skip — it executed, a precondition just was not met', () => {
    expect(connectionHealth({ ...CONN_OK, has_skip: true })).toEqual({
      label: 'Degraded',
      color: 'warning',
    });
  });

  it('an error outranks a skip', () => {
    expect(
      connectionHealth({ ...CONN_OK, has_operational_error: true, has_skip: true }).label,
    ).toBe('Errors');
  });

  it('is No runs when nothing has run (unknown, not healthy)', () => {
    expect(connectionHealth({ ...CONN_OK, last_run_at: null }).label).toBe('No runs');
  });

  it('IGNORES data-quality severity entirely — bad data is not a bad connection', () => {
    // The same summary that reads Critical on the suite axis must read Reachable
    // here: we connected fine, the data is what is wrong.
    expect(connectionHealth(CONN_OK).label).toBe('Reachable');
    expect(suiteHealth({ ...SUITE_OK, worst_severity: 'critical' }).label).toBe('Critical');
  });
});

describe('suiteHealth (#803)', () => {
  it('maps each failing severity to its tier', () => {
    expect(suiteHealth({ ...SUITE_OK, worst_severity: 'warn' }).label).toBe('Warning');
    expect(suiteHealth({ ...SUITE_OK, worst_severity: 'fail' }).label).toBe('Failing');
    expect(suiteHealth({ ...SUITE_OK, worst_severity: 'critical' }).label).toBe('Critical');
  });

  it('is Passing only when checks were actually evaluated and none failed', () => {
    expect(suiteHealth(SUITE_OK)).toEqual({ label: 'Passing', color: 'success' });
  });

  it('is No data — never a green Passing — when a run evaluated nothing', () => {
    // The operational case: the run happened but every check errored/skipped, so
    // checks_total is 0. Keying "Passing" off "a run happened" would paint this
    // green; keying it off evaluated checks correctly says we know nothing.
    expect(suiteHealth({ ...SUITE_OK, checks_total: 0 })).toEqual({
      label: 'No data',
      color: 'default',
    });
  });

  it('is No runs when nothing has run', () => {
    expect(suiteHealth({ ...SUITE_OK, checks_total: 0, last_run_at: null }).label).toBe('No runs');
  });

  it('shows an in-flight run as Running', () => {
    expect(suiteHealth({ ...SUITE_OK, has_active_run: true }).label).toBe('Running');
  });
});
