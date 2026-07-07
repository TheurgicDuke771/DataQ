import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { listRuns, type Run } from '../../src/api/runs';
import { type Suite, listSuites } from '../../src/api/suites';
import { RecentRuns } from '../../src/components/dashboard/RecentRuns';

vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, listRuns: vi.fn() };
});
vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listSuites: vi.fn() };
});

const mockListRuns = vi.mocked(listRuns);
const mockListSuites = vi.mocked(listSuites);

const suite: Suite = {
  id: 's1',
  name: 'Orders quality',
  description: null,
  connection_id: 'c1',
  target: { table: 'ORDERS' },
  created_by: 'u1',
};

const run: Run = {
  id: 'r1',
  suite_id: 's1',
  status: 'succeeded',
  triggered_by: 'manual:u1',
  started_at: '2026-06-11T00:00:00Z',
  finished_at: '2026-06-11T00:00:12Z',
  created_at: '2026-06-11T00:00:00Z',
  checks_total: 4,
  checks_passed: 3,
  worst_severity: 'fail',
  failure_reason: null,
};

function RunDetailStub() {
  const { runId } = useParams<{ runId: string }>();
  return <div>run-detail:{runId}</div>;
}

function renderWidget() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route path="/dashboard" element={<RecentRuns />} />
        <Route path="/results/:runId" element={<RunDetailStub />} />
        <Route path="/results" element={<div>results-list</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('RecentRuns', () => {
  it('lists recent runs with the suite name and status', async () => {
    mockListRuns.mockResolvedValue([run]);
    mockListSuites.mockResolvedValue([suite]);
    renderWidget();

    expect(await screen.findByText('Orders quality')).toBeInTheDocument();
    expect(screen.getByText('succeeded')).toBeInTheDocument();
  });

  it('deep-links a row to the routed run detail', async () => {
    mockListRuns.mockResolvedValue([run]);
    mockListSuites.mockResolvedValue([suite]);
    renderWidget();
    const user = userEvent.setup();

    await user.click(await screen.findByText('Orders quality'));
    expect(await screen.findByText('run-detail:r1')).toBeInTheDocument();
  });

  it('shows an empty state when there are no runs', async () => {
    mockListRuns.mockResolvedValue([]);
    mockListSuites.mockResolvedValue([]);
    renderWidget();
    expect(await screen.findByText('No runs yet.')).toBeInTheDocument();
  });
});
