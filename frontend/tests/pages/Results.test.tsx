import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  getRun,
  listPipelineRuns,
  listRuns,
  type PipelineRun,
  type Run,
  type RunDetail,
} from '../../src/api/runs';
import { type Check, type Suite, listChecks, listSuites } from '../../src/api/suites';
import { Results } from '../../src/pages/Results';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, listRuns: vi.fn(), getRun: vi.fn(), listPipelineRuns: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listSuites: vi.fn(), listChecks: vi.fn() };
});

const mockListRuns = vi.mocked(listRuns);
const mockGetRun = vi.mocked(getRun);
const mockListPipelineRuns = vi.mocked(listPipelineRuns);
const mockListSuites = vi.mocked(listSuites);
const mockListChecks = vi.mocked(listChecks);

const suite: Suite = {
  id: 's1',
  name: 'Orders quality',
  description: null,
  connection_id: 'c1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

const succeededRun: Run = {
  id: 'r1',
  suite_id: 's1',
  status: 'succeeded',
  triggered_by: 'manual:u1',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:12Z',
  created_at: '2026-06-11T00:00:00Z',
};

const failedRun: Run = {
  ...succeededRun,
  id: 'r2',
  status: 'failed',
  triggered_by: 'seed:run:failed',
  finished_at: '2026-06-11T00:00:02Z',
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

const runDetail: RunDetail = {
  ...succeededRun,
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

const pipelineRun: PipelineRun = {
  id: 'p1',
  provider: 'adf',
  connection_id: 'c2',
  provider_run_id: 'seed-adf-0001',
  pipeline_or_dag_id: 'daily_orders_load',
  env: 'prod',
  status: 'succeeded',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:30Z',
  failure_reason: null,
  created_at: '2026-06-11T00:00:00Z',
};

function renderResults() {
  return render(
    <MemoryRouter>
      <Results />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('Results page', () => {
  it('lists runs with the suite name and a status tag', async () => {
    mockListRuns.mockResolvedValue([succeededRun, failedRun]);
    mockListSuites.mockResolvedValue([suite]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();

    // Both seeded runs resolve to the suite name, with their status tags.
    await waitFor(() => expect(screen.getAllByText('Orders quality').length).toBe(2));
    expect(screen.getByText('succeeded')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
  });

  it('opens a run and shows its per-check results', async () => {
    mockListRuns.mockResolvedValue([succeededRun]);
    mockListSuites.mockResolvedValue([suite]);
    mockListPipelineRuns.mockResolvedValue([]);
    mockGetRun.mockResolvedValue(runDetail);
    mockListChecks.mockResolvedValue([check]);

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Orders quality')).toBeInTheDocument());
    await user.click(screen.getByText('Orders quality'));

    // The detail drawer fetches the run + checks and renders the result row,
    // mapping check_id → name and showing the severity tag.
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByText('order_id not null')).toBeInTheDocument());
    expect(within(dialog).getByText('expect_column_values_to_not_be_null')).toBeInTheDocument();
    expect(within(dialog).getByText('warn')).toBeInTheDocument();
    expect(mockGetRun).toHaveBeenCalledWith('r1');
  });

  it('shows monitored pipeline runs on the Pipeline runs tab', async () => {
    mockListRuns.mockResolvedValue([]);
    mockListSuites.mockResolvedValue([]);
    mockListPipelineRuns.mockResolvedValue([pipelineRun]);

    renderResults();
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Pipeline runs' }));

    await waitFor(() => expect(screen.getByText('daily_orders_load')).toBeInTheDocument());
    // Provider + status render as tags in the row.
    expect(screen.getByText('adf')).toBeInTheDocument();
    expect(screen.getByText('succeeded')).toBeInTheDocument();
  });
});
