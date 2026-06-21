import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import { getRunProgress, type Run, type RunProgress, runSuite } from '../../src/api/runs';
import { listSuites, type Suite } from '../../src/api/suites';
import { RunNowPanel } from '../../src/components/runs/RunNowPanel';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnections: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return { ...actual, listSuites: vi.fn() };
});

// Triggering hands off to LiveRunProgress, which polls getRunProgress — so it
// must be a mock here too (not undefined), even though we don't assert on it.
vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, runSuite: vi.fn(), getRunProgress: vi.fn(), cancelRun: vi.fn() };
});

const mockListSuites = vi.mocked(listSuites);
const mockListConnections = vi.mocked(listConnections);
const mockRunSuite = vi.mocked(runSuite);
const mockGetRunProgress = vi.mocked(getRunProgress);

const connection: Connection = {
  id: 'conn1',
  name: 'sf-prod',
  type: 'snowflake',
  env: 'prod',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

function suite(overrides: Partial<Suite> = {}): Suite {
  return {
    id: 's1',
    name: 'orders-suite',
    description: '',
    connection_id: 'conn1',
    target: { schema: 'ANALYTICS', table: 'ORDERS' },
    created_by: 'u1',
    my_permission: 'owner',
    ...overrides,
  };
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <AntApp>
        <RunNowPanel open onClose={() => {}} />
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('RunNowPanel', () => {
  it('shows an empty state when no suite is runnable (only view access)', async () => {
    mockListSuites.mockResolvedValue([suite({ my_permission: 'view' })]);
    mockListConnections.mockResolvedValue([connection]);
    renderPanel();
    expect(await screen.findByText(/No runnable suites/)).toBeInTheDocument();
  });

  it('renders env + datasource + target once a suite is picked, then triggers a run', async () => {
    mockListSuites.mockResolvedValue([suite()]);
    mockListConnections.mockResolvedValue([connection]);
    const queued: Run = {
      id: 'run1',
      suite_id: 's1',
      status: 'queued',
      triggered_by: 'manual:u1',
      started_at: null,
      finished_at: null,
      created_at: '2026-06-21T00:00:00Z',
    };
    mockRunSuite.mockResolvedValue(queued);
    // The handed-off progress drawer polls this once; keep it non-terminal-safe.
    mockGetRunProgress.mockResolvedValue({
      run_id: 'run1',
      suite_id: 's1',
      status: 'succeeded',
      total_checks: 0,
      completed_checks: 0,
      counts: {},
      checks: [],
      started_at: null,
      finished_at: null,
    } satisfies RunProgress);

    renderPanel();
    const user = userEvent.setup();

    // The first combobox is the suite picker (the second is the disabled
    // notification placeholder).
    await user.click((await screen.findAllByRole('combobox'))[0]);
    await user.click(await screen.findByText('orders-suite'));

    // Env / datasource / target readout, derived from the suite's connection.
    expect(await screen.findByText('PROD')).toBeInTheDocument();
    expect(screen.getByText('sf-prod')).toBeInTheDocument();
    expect(screen.getByText('ANALYTICS.ORDERS')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Run/ }));
    await waitFor(() => expect(mockRunSuite).toHaveBeenCalledWith('s1'));
  });

  it('blocks Run for a targetless suite and explains why', async () => {
    mockListSuites.mockResolvedValue([suite({ target: null })]);
    mockListConnections.mockResolvedValue([connection]);
    renderPanel();
    const user = userEvent.setup();

    // The first combobox is the suite picker (the second is the disabled
    // notification placeholder).
    await user.click((await screen.findAllByRole('combobox'))[0]);
    await user.click(await screen.findByText('orders-suite'));

    expect(await screen.findByText(/has no run target/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run/ })).toBeDisabled();
    expect(mockRunSuite).not.toHaveBeenCalled();
  });
});
