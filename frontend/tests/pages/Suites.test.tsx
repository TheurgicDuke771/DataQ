import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import { getRunProgress, runSuite } from '../../src/api/runs';
import {
  type Check,
  deleteCheck,
  deleteSuite,
  listChecks,
  listSuites,
  type Suite,
} from '../../src/api/suites';
import { Suites } from '../../src/pages/Suites';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnections: vi.fn() };
});

vi.mock('../../src/api/suites', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/suites')>();
  return {
    ...actual,
    listSuites: vi.fn(),
    listChecks: vi.fn(),
    deleteSuite: vi.fn(),
    deleteCheck: vi.fn(),
  };
});

// Preserve the real types/helpers; the manual Run flow opens LiveRunProgress,
// which polls getRunProgress — so it must be a mock here too, not undefined.
vi.mock('../../src/api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/runs')>();
  return { ...actual, runSuite: vi.fn(), getRunProgress: vi.fn(), cancelRun: vi.fn() };
});

const mockListSuites = vi.mocked(listSuites);
const mockListConnections = vi.mocked(listConnections);
const mockListChecks = vi.mocked(listChecks);
const mockDeleteSuite = vi.mocked(deleteSuite);
const mockDeleteCheck = vi.mocked(deleteCheck);
const mockRunSuite = vi.mocked(runSuite);
const mockGetRunProgress = vi.mocked(getRunProgress);

const connection: Connection = {
  id: 'conn1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_by: 'u1',
};

function suite(overrides: Partial<Suite> = {}): Suite {
  return {
    id: 's1',
    name: 'orders-suite',
    description: 'Checks for the orders table',
    connection_id: 'conn1',
    target: null,
    created_by: 'u1',
    ...overrides,
  };
}

function check(overrides: Partial<Check> = {}): Check {
  return {
    id: 'chk1',
    suite_id: 's1',
    name: 'order_id not null',
    kind: 'expectation',
    expectation_type: 'expect_column_values_to_not_be_null',
    config: {},
    warn_threshold: null,
    fail_threshold: null,
    critical_threshold: null,
    ...overrides,
  };
}

// Selecting a suite navigates to /suites/:suiteId, so render both routes at the
// same Suites component (the param drives which suite is shown).
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/suites']}>
      <AntApp>
        <Routes>
          <Route path="/suites" element={<Suites />} />
          <Route path="/suites/new" element={<div>New suite page</div>} />
          <Route path="/suites/:suiteId" element={<Suites />} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('Suites', () => {
  it('lists suites and shows the detail panel on selection', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([check()]);

    renderPage();

    await user.click(await screen.findByText('orders-suite'));

    // Detail panel: connection context + the check.
    expect(await screen.findByText('order_id not null')).toBeInTheDocument();
    expect(screen.getByText('sf-dev · Snowflake')).toBeInTheDocument();
    // The env tag now renders in both the list row and the detail panel.
    expect(screen.getAllByText('DEV').length).toBeGreaterThan(0);
    expect(mockListChecks).toHaveBeenCalledWith('s1');
  });

  it('deep-links to a suite via the route param (no click needed)', async () => {
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([check()]);

    render(
      <MemoryRouter initialEntries={['/suites/s1']}>
        <AntApp>
          <Routes>
            <Route path="/suites" element={<Suites />} />
            <Route path="/suites/:suiteId" element={<Suites />} />
          </Routes>
        </AntApp>
      </MemoryRouter>,
    );

    // The detail panel renders straight from the URL.
    expect(await screen.findByText('order_id not null')).toBeInTheDocument();
    expect(mockListChecks).toHaveBeenCalledWith('s1');
  });

  it('navigates to the new-suite page from the New suite button', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([]);

    renderPage();

    await user.click(await screen.findByRole('button', { name: /New suite/ }));
    expect(await screen.findByText('New suite page')).toBeInTheDocument();
  });

  it('shows an empty state when there are no suites', async () => {
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([]);

    renderPage();

    expect(
      await screen.findByText('No suites yet — create one to start authoring checks.'),
    ).toBeInTheDocument();
  });

  it('warns when connections fail to load (create depends on them)', async () => {
    mockListConnections.mockRejectedValue(new Error('conn down'));
    mockListSuites.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('Couldn’t load connections')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /New suite/ })).toBeDisabled();
  });

  it('surfaces a load error', async () => {
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockRejectedValue(new Error('boom'));

    renderPage();

    expect(await screen.findByText('Failed to load suites')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('deletes a check from the detail panel after confirming', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([check()]);
    mockDeleteCheck.mockResolvedValue();

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('order_id not null');

    // The check row's own Delete (link button), scoped to its confirm dialog.
    const checkRow = screen.getByText('order_id not null').closest('li') as HTMLElement;
    await user.click(within(checkRow).getByRole('button', { name: 'Delete' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDeleteCheck).toHaveBeenCalledWith('s1', 'chk1'));
  });

  it('deletes a suite via the detail panel after confirming', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([]);
    mockDeleteSuite.mockResolvedValue();

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDeleteSuite).toHaveBeenCalledWith('s1'));
  });

  it('triggers a run from the detail panel when runnable', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([
      suite({ target: { table: 'orders' }, my_permission: 'owner' }),
    ]);
    mockListChecks.mockResolvedValue([check()]);
    mockRunSuite.mockResolvedValue({
      id: 'r1',
      suite_id: 's1',
      status: 'queued',
      triggered_by: 'manual:u1',
      started_at: null,
      finished_at: null,
      created_at: '2026-06-12T00:00:00Z',
    });
    mockGetRunProgress.mockResolvedValue({
      run_id: 'r1',
      suite_id: 's1',
      status: 'running',
      total_checks: 1,
      completed_checks: 0,
      counts: {},
      checks: [{ check_id: 'c1', name: 'not-null id', status: null }],
      started_at: null,
      finished_at: null,
    });

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await user.click(await screen.findByRole('button', { name: /Run/ }));

    await waitFor(() => expect(mockRunSuite).toHaveBeenCalledWith('s1'));
    // The manual run opens the live-progress drawer (it polls the queued run)
    // rather than navigating away.
    expect(await screen.findByText('Run progress · orders-suite')).toBeInTheDocument();
    await waitFor(() => expect(mockGetRunProgress).toHaveBeenCalledWith('r1'));
  });

  it('disables Run (no click) when the suite has no target', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    // target null = not runnable, even for the owner.
    mockListSuites.mockResolvedValue([suite({ target: null, my_permission: 'owner' })]);
    mockListChecks.mockResolvedValue([check()]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));

    const runButton = await screen.findByRole('button', { name: /Run/ });
    expect(runButton).toBeDisabled();
    await user.click(runButton);
    expect(mockRunSuite).not.toHaveBeenCalled();
  });

  it('hides Run for a viewer (no edit permission)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([
      suite({ target: { table: 'orders' }, my_permission: 'view' }),
    ]);
    mockListChecks.mockResolvedValue([check()]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('order_id not null');

    expect(screen.queryByRole('button', { name: /Run/ })).not.toBeInTheDocument();
  });
});
