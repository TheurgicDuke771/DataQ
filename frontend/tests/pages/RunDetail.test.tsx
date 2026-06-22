import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getRun, type RunDetail as RunDetailType } from '../../src/api/runs';
import { type Check, type Suite, getSuite, listChecks } from '../../src/api/suites';
import { RunDetail } from '../../src/pages/RunDetail';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, getRun: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, getSuite: vi.fn(), listChecks: vi.fn() };
});

const mockGetRun = vi.mocked(getRun);
const mockGetSuite = vi.mocked(getSuite);
const mockListChecks = vi.mocked(listChecks);

const suite: Suite = {
  id: 's1',
  name: 'Orders quality',
  description: null,
  connection_id: 'c1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

const check: Check = {
  id: 'chk1',
  suite_id: 's1',
  name: 'order_id not null',
  kind: 'expectation',
  expectation_type: 'expect_column_values_to_not_be_null',
  config: { column: 'order_id' },
  warn_threshold: null,
  fail_threshold: null,
  critical_threshold: null,
};

const runDetail: RunDetailType = {
  id: 'r1',
  suite_id: 's1',
  status: 'succeeded',
  triggered_by: 'manual:u1',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:12Z',
  created_at: '2026-06-11T00:00:00Z',
  results: [
    {
      id: 'res1',
      check_id: 'chk1',
      status: 'warn',
      metric_value: 2,
      duration_ms: null,
      observed_value: { unexpected_percent: 2 },
      expected_value: null,
    },
  ],
};

function renderAt(runId: string) {
  return render(
    <MemoryRouter initialEntries={[`/results/${runId}`]}>
      <Routes>
        <Route path="/results/:runId" element={<RunDetail />} />
        <Route path="/results" element={<div>results-list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('RunDetail page', () => {
  it('loads the run by id and renders its per-check results', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);

    renderAt('r1');

    expect(await screen.findByText('Orders quality')).toBeInTheDocument();
    // check_id → name + expectation + severity tag.
    expect(screen.getByText('order_id not null')).toBeInTheDocument();
    expect(screen.getByText('expect_column_values_to_not_be_null')).toBeInTheDocument();
    expect(screen.getByText('warn')).toBeInTheDocument();
    // Checks-passed stat: 0 of 1 passed (the one result is a warn).
    expect(screen.getByText('0 / 1')).toBeInTheDocument();
    expect(mockGetRun).toHaveBeenCalledWith('r1');
  });

  it('still renders when the suite name and checks fail to load', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockRejectedValue(new Error('forbidden'));
    mockListChecks.mockRejectedValue(new Error('forbidden'));

    renderAt('r1');

    // Falls back to a suite-id stub heading; the result row still shows (by id).
    await waitFor(() => expect(screen.getByText('warn')).toBeInTheDocument());
  });

  it('shows an error when the run fails to load', async () => {
    mockGetRun.mockRejectedValue(new Error('boom'));
    renderAt('rX');
    expect(await screen.findByText('Failed to load run')).toBeInTheDocument();
  });
});
