import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { listPipelineRuns, listRuns, type PipelineRun, type Run } from '../../src/api/runs';
import { type Suite, listSuites } from '../../src/api/suites';
import { Results } from '../../src/pages/Results';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, listRuns: vi.fn(), listPipelineRuns: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listSuites: vi.fn() };
});

const mockListRuns = vi.mocked(listRuns);
const mockListPipelineRuns = vi.mocked(listPipelineRuns);
const mockListSuites = vi.mocked(listSuites);

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

/** A stub for the run-detail route so a row click's navigation is observable. */
function RunDetailStub() {
  const { runId } = useParams<{ runId: string }>();
  return <div>run-detail:{runId}</div>;
}

function renderResults() {
  return render(
    <MemoryRouter initialEntries={['/results']}>
      <Routes>
        <Route path="/results" element={<Results />} />
        <Route path="/results/:runId" element={<RunDetailStub />} />
      </Routes>
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

  it('navigates to the routed run-detail page on row click', async () => {
    mockListRuns.mockResolvedValue([succeededRun]);
    mockListSuites.mockResolvedValue([suite]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getByText('Orders quality')).toBeInTheDocument());
    await user.click(screen.getByText('Orders quality'));

    // The run-detail drawer is gone — the row deep-links to /results/:runId.
    expect(await screen.findByText('run-detail:r1')).toBeInTheDocument();
  });

  it('filters the runs table by status', async () => {
    mockListRuns.mockResolvedValue([succeededRun, failedRun]);
    mockListSuites.mockResolvedValue([suite]);
    mockListPipelineRuns.mockResolvedValue([]);

    renderResults();
    const user = userEvent.setup();

    await waitFor(() => expect(screen.getAllByText('Orders quality').length).toBe(2));

    // Pick "failed" in the status Select → only the failed run's row remains.
    await user.click(screen.getByRole('combobox'));
    await user.click(await screen.findByTitle('failed'));

    // Scope to the table body rows (the closed dropdown still holds the
    // 'succeeded' option text, so assert on rows, not document-wide text).
    await waitFor(() => expect(document.querySelectorAll('tr.ant-table-row').length).toBe(1));
    const row = document.querySelector('tr.ant-table-row');
    expect(row?.textContent).toContain('failed');
    expect(row?.textContent).not.toContain('succeeded');
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
