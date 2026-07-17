import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import { getRunProgress, runSuite } from '../../src/api/runs';
import {
  type Check,
  clearCheckSnooze,
  deleteCheck,
  deleteSuite,
  listChecks,
  listSuites,
  rebaselineCheck,
  snoozeCheck,
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
    snoozeCheck: vi.fn(),
    clearCheckSnooze: vi.fn(),
    rebaselineCheck: vi.fn(),
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
const mockSnoozeCheck = vi.mocked(snoozeCheck);
const mockRebaseline = vi.mocked(rebaselineCheck);
const mockClearSnooze = vi.mocked(clearCheckSnooze);
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
    alert_snoozed_until: null,
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

  it('surfaces an Asset link on the detail panel and navigates to the asset (#773)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ asset_id: 'asset-9' })]);
    mockListChecks.mockResolvedValue([check()]);

    render(
      <MemoryRouter initialEntries={['/suites/s1']}>
        <AntApp>
          <Routes>
            <Route path="/suites/:suiteId" element={<Suites />} />
            <Route path="/assets/:assetId" element={<div>asset page</div>} />
          </Routes>
        </AntApp>
      </MemoryRouter>,
    );

    await user.click(await screen.findByText('Asset'));
    expect(await screen.findByText('asset page')).toBeInTheDocument();
  });

  it('omits the Asset link when the suite has no resolved asset (#773)', async () => {
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ asset_id: null })]);
    mockListChecks.mockResolvedValue([check()]);
    renderPage();
    await screen.findByText('orders-suite');
    // No selection yet → grid view; select to reach the detail panel.
    await userEvent.click(screen.getByText('orders-suite'));
    await screen.findByText('order_id not null');
    expect(screen.queryByText('Asset')).not.toBeInTheDocument();
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
    const checkRow = screen
      .getByText('order_id not null')
      .closest('[role="listitem"]') as HTMLElement;
    await user.click(within(checkRow).getByRole('button', { name: 'Delete' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDeleteCheck).toHaveBeenCalledWith('s1', 'chk1'));
  });

  it('snoozes a check from the detail panel and refreshes the list (#653)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ my_permission: 'edit' })]);
    const active = check();
    const snoozed = check({ alert_snoozed_until: '2099-01-01T00:00:00Z' });
    mockListChecks.mockResolvedValueOnce([active]).mockResolvedValueOnce([snoozed]);
    mockSnoozeCheck.mockResolvedValue(snoozed);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('order_id not null');

    await user.click(screen.getByRole('button', { name: 'Snooze' }));
    await user.click(await screen.findByText('24 hours'));

    await waitFor(() => expect(mockSnoozeCheck).toHaveBeenCalledWith('s1', 'chk1', 24));
    // The list refetches and the row now carries the snoozed badge.
    expect(await screen.findByText(/Snoozed until/)).toBeInTheDocument();
  });

  it('offers Re-baseline only on schema_drift checks and confirms before calling (#592)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ my_permission: 'edit' })]);
    const drift = check({
      id: 'chk-drift',
      name: 'schema drift',
      kind: 'schema_drift',
      expectation_type: 'monitor:schema_drift',
    });
    mockListChecks.mockResolvedValue([drift, check({ id: 'chk-exp' })]);
    mockRebaseline.mockResolvedValue(undefined);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('schema drift');

    // Exactly ONE Re-baseline action — the expectation check offers none.
    const buttons = screen.getAllByRole('button', { name: 'Re-baseline' });
    expect(buttons).toHaveLength(1);
    await user.click(buttons[0]);
    // Nothing fires until the modal confirms (dropping the reference is
    // consequential — accumulated drift reads as "no drift" afterwards).
    expect(mockRebaseline).not.toHaveBeenCalled();
    await screen.findByText(/Drops the stored schema baseline/);
    // The confirm modal's OK also reads "Re-baseline" — it's the primary button.
    const ok = screen
      .getAllByRole('button', { name: 'Re-baseline' })
      .find((b) => b.className.includes('ant-btn-primary'));
    if (!ok) throw new Error('confirm modal OK button not found');
    await user.click(ok);
    await waitFor(() => expect(mockRebaseline).toHaveBeenCalledWith('s1', 'chk-drift'));
  });

  it('unsnoozes a snoozed check (badge + Unsnooze action) (#653)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ my_permission: 'edit' })]);
    const snoozed = check({ alert_snoozed_until: '2099-01-01T00:00:00Z' });
    mockListChecks.mockResolvedValueOnce([snoozed]).mockResolvedValueOnce([check()]);
    mockClearSnooze.mockResolvedValue(check());

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText(/Snoozed until/);

    await user.click(screen.getByRole('button', { name: 'Unsnooze' }));

    await waitFor(() => expect(mockClearSnooze).toHaveBeenCalledWith('s1', 'chk1'));
    await waitFor(() => expect(screen.queryByText(/Snoozed until/)).not.toBeInTheDocument());
  });

  it('treats an expired snooze as active — no badge, Snooze offered (#653)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ my_permission: 'edit' })]);
    mockListChecks.mockResolvedValue([check({ alert_snoozed_until: '2020-01-01T00:00:00Z' })]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('order_id not null');

    expect(screen.queryByText(/Snoozed until/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Snooze' })).toBeInTheDocument();
  });

  it('hides snooze AND re-baseline controls (but keeps the badge) for a view-only user', async () => {
    // Snooze/unsnooze are edit-gated on the backend — a viewer must not be
    // offered a control that can only 403 (matches the sibling panels).
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ my_permission: 'view' })]);
    mockListChecks.mockResolvedValue([
      check({ alert_snoozed_until: '2099-01-01T00:00:00Z' }),
      check({
        id: 'chk-drift',
        name: 'drift',
        kind: 'schema_drift',
        expectation_type: 'monitor:schema_drift',
      }),
    ]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('order_id not null');

    expect(screen.getByText(/Snoozed until/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Snooze' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Unsnooze' })).not.toBeInTheDocument();
    // Re-baseline is edit-gated the same way (#592) — a viewer must never see it.
    expect(screen.queryByRole('button', { name: 'Re-baseline' })).not.toBeInTheDocument();
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
      checks_total: 0,
      checks_passed: 0,
      worst_severity: null,
      failure_reason: null,
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
