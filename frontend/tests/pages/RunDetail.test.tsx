import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getRun, type RunDetail as RunDetailType } from '../../src/api/runs';
import { type Check, type Suite, getSuite, listChecks } from '../../src/api/suites';
import { RunDetail } from '../../src/pages/RunDetail';
import { downloadCsv, downloadJson } from '../../src/utils/download';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, getRun: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, getSuite: vi.fn(), listChecks: vi.fn() };
});

vi.mock('../../src/utils/download', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/utils/download')>();
  return { ...actual, downloadCsv: vi.fn(), downloadJson: vi.fn() };
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
  alert_snoozed_until: null,
};

const runDetail: RunDetailType = {
  id: 'r1',
  suite_id: 's1',
  status: 'succeeded',
  triggered_by: 'manual:u1',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:12Z',
  created_at: '2026-06-11T00:00:00Z',
  checks_total: 1,
  checks_passed: 0,
  worst_severity: 'warn',
  results: [
    {
      id: 'res1',
      check_id: 'chk1',
      status: 'warn',
      metric_value: 2,
      duration_ms: null,
      observed_value: { unexpected_percent: 2 },
      expected_value: null,
      // Redacted at the API boundary (#226): counts kept, cell values masked.
      sample_failures: {
        unexpected_count: 2,
        unexpected_percent: 2,
        partial_unexpected_list: [{ order_id: '<redacted>' }, { order_id: '<redacted>' }],
      },
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

  it('marks a snoozed check in the results table (#653 — triage surface)', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([{ ...check, alert_snoozed_until: '2099-01-01T00:00:00Z' }]);

    renderAt('r1');

    expect(await screen.findByText('order_id not null')).toBeInTheDocument();
    expect(screen.getByText(/Snoozed until/)).toBeInTheDocument();
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

  it('exports the run results as CSV with check names resolved', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /download/i }));
    await user.click(await screen.findByText('Download CSV'));

    expect(downloadCsv).toHaveBeenCalledTimes(1);
    const [filename, headers, rows] = vi.mocked(downloadCsv).mock.calls[0];
    expect(filename).toBe('orders_quality_run_r1.csv');
    expect(headers).toEqual(['check', 'expectation', 'status', 'metric_value', 'observed']);
    // check_id → name, observed scalar JSON-stringified.
    expect(rows[0]).toEqual([
      'order_id not null',
      'expect_column_values_to_not_be_null',
      'warn',
      2,
      '{"unexpected_percent":2}',
    ]);
  });

  it('surfaces the redacted failing-row sample in a check’s expanded row', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const user = userEvent.setup();

    await screen.findByText('order_id not null');
    await user.click(screen.getByRole('button', { name: /expand row/i }));

    // Count is surfaced; the masked cell value shows the shape, not real data.
    expect(await screen.findByText(/Failing rows/)).toBeInTheDocument();
    expect(screen.getByText(/2 rows/)).toBeInTheDocument();
    expect(screen.getAllByText('<redacted>').length).toBeGreaterThan(0);
  });

  it('exports the run as JSON (failing-row sample omitted from the payload)', async () => {
    mockGetRun.mockResolvedValue(runDetail);
    mockGetSuite.mockResolvedValue(suite);
    mockListChecks.mockResolvedValue([check]);
    renderAt('r1');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: /download/i }));
    await user.click(await screen.findByText('Download JSON'));

    expect(downloadJson).toHaveBeenCalledTimes(1);
    const [filename, payload] = vi.mocked(downloadJson).mock.calls[0];
    expect(filename).toBe('orders_quality_run_r1.json');
    const body = payload as { run: { suite_name: string }; checks: unknown[] };
    expect(body.run.suite_name).toBe('Orders quality');
    expect(body.checks).toHaveLength(1);
  });
});
