import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { compareRuns, type ComparisonRun, type RunComparison } from '../../src/api/runs';
import { getSuite, type Suite } from '../../src/api/suites';
import { formatMetricDelta } from '../../src/components/results/resultsFormat';
import { RunCompare } from '../../src/pages/RunCompare';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, compareRuns: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, getSuite: vi.fn() };
});

const mockCompareRuns = vi.mocked(compareRuns);
const mockGetSuite = vi.mocked(getSuite);

const suite: Suite = {
  id: 's1',
  name: 'Orders quality',
  description: null,
  connection_id: 'c1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

function run(id: string, overrides: Partial<ComparisonRun> = {}): ComparisonRun {
  return {
    id,
    suite_id: 's1',
    status: 'succeeded',
    triggered_by: 'manual:u1',
    started_at: '2026-06-11T00:00:00Z',
    finished_at: '2026-06-11T00:00:12Z',
    created_at: '2026-06-11T00:00:00Z',
    checks_total: 3,
    checks_passed: 2,
    worst_severity: 'fail',
    failure_reason: null,
    results_final: true,
    ...overrides,
  };
}

const check = {
  name: 'order_id not null',
  kind: 'expectation',
  engine: 'gx',
  expectation_type: 'expect_column_values_to_not_be_null',
  dimension: 'completeness',
  column: 'order_id',
};

const comparison: RunComparison = {
  base: run('r0', { created_at: '2026-06-10T00:00:00Z', started_at: '2026-06-10T00:00:00Z' }),
  target: run('r1'),
  summary: {
    changed: 2,
    unchanged: 1,
    improved: 1,
    regressed: 1,
    added: 1,
    removed: 1,
    metric_changed: 2,
  },
  rows: [
    {
      check_id: 'k-added',
      label: 'amount positive',
      base: null,
      target: {
        result_id: 'x1',
        status: 'warn',
        metric_value: 3,
        observed_value: null,
        check: { ...check, name: 'amount positive' },
      },
      delta: {
        status_change: 'added',
        metric_delta: null,
        metric_delta_pct: null,
        metric_changed: false,
      },
    },
    {
      check_id: 'k-improved',
      label: 'customer present',
      base: {
        result_id: 'b2',
        status: 'critical',
        metric_value: 40,
        observed_value: { observed_value: 40 },
        check: { ...check, name: 'customer present' },
      },
      target: {
        result_id: 't2',
        status: 'warn',
        metric_value: 4,
        observed_value: { observed_value: 4 },
        check: { ...check, name: 'customer present' },
      },
      delta: {
        status_change: 'improved',
        metric_delta: -36,
        metric_delta_pct: -90,
        metric_changed: true,
      },
    },
    {
      check_id: 'k-regressed',
      label: 'order_id not null',
      // Renamed between the runs: the base side carries the old name.
      base: {
        result_id: 'b3',
        status: 'pass',
        metric_value: 0,
        observed_value: { observed_value: 0 },
        check: { ...check, name: 'id not null', engine: 'gx' },
      },
      target: {
        result_id: 't3',
        status: 'fail',
        metric_value: 12.5,
        observed_value: { observed_value: 12.5 },
        check: { ...check, engine: 'dmf' },
      },
      delta: {
        status_change: 'regressed',
        metric_delta: 12.5,
        metric_delta_pct: null,
        metric_changed: true,
      },
    },
    {
      check_id: 'k-removed',
      label: 'row count stable',
      base: {
        result_id: 'b4',
        status: 'pass',
        metric_value: 1000,
        observed_value: null,
        check: { ...check, name: 'row count stable' },
      },
      target: null,
      delta: {
        status_change: 'removed',
        metric_delta: null,
        metric_delta_pct: null,
        metric_changed: false,
      },
    },
    {
      check_id: 'k-same',
      label: 'status in set',
      base: {
        result_id: 'b5',
        status: 'pass',
        metric_value: 0,
        observed_value: null,
        check: { ...check, name: 'status in set' },
      },
      target: {
        result_id: 't5',
        status: 'pass',
        metric_value: 0,
        observed_value: null,
        check: { ...check, name: 'status in set' },
      },
      delta: {
        status_change: 'same',
        metric_delta: 0,
        metric_delta_pct: null,
        metric_changed: false,
      },
    },
  ],
};

function renderAt(base: string, target: string) {
  return render(
    <MemoryRouter initialEntries={[`/results/${base}/compare/${target}`]}>
      <Routes>
        <Route path="/results/:runId/compare/:otherRunId" element={<RunCompare />} />
        <Route path="/results/:runId" element={<div>run page</div>} />
        <Route path="/results" element={<div>results-list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const region = () => within(screen.getByTestId('rc-screen'));

afterEach(() => {
  vi.clearAllMocks();
});

describe('RunCompare page', () => {
  it('renders both runs, the summary and one row per check with its change', async () => {
    mockCompareRuns.mockResolvedValue(comparison);
    mockGetSuite.mockResolvedValue(suite);
    renderAt('r0', 'r1');

    expect(
      await screen.findByRole('heading', { name: 'Compare runs — Orders quality', level: 3 }),
    ).toBeInTheDocument();
    expect(mockCompareRuns).toHaveBeenCalledWith('r0', 'r1');
    const r = region();
    expect(r.getByRole('link', { name: 'Open base run' })).toHaveAttribute('href', '/results/r0');
    expect(r.getByRole('link', { name: 'Open target run' })).toHaveAttribute('href', '/results/r1');
    // Every row, with its change spelled out as text — the colour is not the only signal.
    expect(r.getAllByRole('row')).toHaveLength(6); // header + 5
    expect(r.getByText('Regressed', { selector: '.ant-tag' })).toBeInTheDocument();
    expect(r.getByText('Improved', { selector: '.ant-tag' })).toBeInTheDocument();
    expect(r.getByText('Added', { selector: '.ant-tag' })).toBeInTheDocument();
    expect(r.getByText('Removed', { selector: '.ant-tag' })).toBeInTheDocument();
    expect(r.getByText('Same', { selector: '.ant-tag' })).toBeInTheDocument();
    // Metric deltas: absolute, plus a percentage only where the base is non-zero.
    expect(r.getByText('−36 (−90%)')).toBeInTheDocument();
    expect(r.getByText('+12.5')).toBeInTheDocument();
    // A side the check has no result on says so in words.
    expect(r.getAllByText('not in this run')).toHaveLength(2);
    expect(r.getByRole('status')).toHaveTextContent('Showing 5 of 5 checks');
  });

  it('shows each side as the check was at that run — a rename is named, not hidden', async () => {
    mockCompareRuns.mockResolvedValue(comparison);
    mockGetSuite.mockResolvedValue(suite);
    renderAt('r0', 'r1');
    const r = region();
    expect(await r.findByText('was “id not null” in the base run')).toBeInTheDocument();
    // The engine badge is the target side's as-of value (DMF), not a current-check lookup.
    expect(r.getByText('DMF')).toBeInTheDocument();
  });

  it('filters the rows by change and announces the count, operable by keyboard', async () => {
    mockCompareRuns.mockResolvedValue(comparison);
    mockGetSuite.mockResolvedValue(suite);
    renderAt('r0', 'r1');
    const r = region();
    await r.findByText('status in set');
    const user = userEvent.setup();

    const group = r.getByRole('radiogroup', { name: 'Filter comparison rows' });
    await user.click(within(group).getByText('Regressed'));
    expect(r.getAllByRole('row')).toHaveLength(2);
    expect(r.getByText('order_id not null')).toBeInTheDocument();
    expect(r.getByRole('status')).toHaveTextContent('Showing 1 of 5 checks');

    // Arrow keys move the selection within the radio group.
    within(group).getByRole('radio', { name: 'Regressed' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(within(group).getByRole('radio', { name: 'Improved' })).toBeChecked();
    expect(r.getByText('customer present')).toBeInTheDocument();
    expect(r.queryByText('order_id not null')).not.toBeInTheDocument();

    await user.click(within(group).getByText('Changed'));
    expect(r.getAllByRole('row')).toHaveLength(3); // header + regressed + improved
    await user.click(within(group).getByText('Added'));
    expect(r.getByText('amount positive')).toBeInTheDocument();
    expect(r.getByRole('status')).toHaveTextContent('Showing 1 of 5 checks');
  });

  it('warns when a side is not final and offers to swap the sides', async () => {
    mockCompareRuns.mockResolvedValue({
      ...comparison,
      target: run('r1', { status: 'running', results_final: false, finished_at: null }),
    });
    mockGetSuite.mockResolvedValue(suite);
    renderAt('r0', 'r1');
    const r = region();
    expect(await r.findByText('One of these runs has not finished')).toBeInTheDocument();
    expect(r.getByText('results not final')).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(r.getByRole('button', { name: /Swap sides/ }));
    expect(mockCompareRuns).toHaveBeenLastCalledWith('r1', 'r0');
  });

  it('shows the API error for a pair that cannot be compared', async () => {
    mockCompareRuns.mockRejectedValue(new Error('the two runs belong to different suites'));
    mockGetSuite.mockResolvedValue(suite);
    renderAt('r0', 'r9');
    expect(await screen.findByText(/different suites/)).toBeInTheDocument();
  });
});

describe('formatMetricDelta', () => {
  it('formats sign, magnitude and a rounded percentage', () => {
    expect(formatMetricDelta(null, null)).toBe('—');
    expect(formatMetricDelta(0, null)).toBe('0');
    expect(formatMetricDelta(2, 20)).toBe('+2 (+20%)');
    expect(formatMetricDelta(-36, -90)).toBe('−36 (−90%)');
    expect(formatMetricDelta(1.5, 33.333)).toBe('+1.5 (+33.3%)');
    expect(formatMetricDelta(12.5, null)).toBe('+12.5');
  });
});
