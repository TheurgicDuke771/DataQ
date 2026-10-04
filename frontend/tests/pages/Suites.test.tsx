import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { WithMe } from '../support/me';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections } from '../../src/api/connections';
import { getRunProgress, runSuite } from '../../src/api/runs';
import {
  type Check,
  bulkDeleteChecks,
  bulkSetThresholds,
  bulkSnoozeChecks,
  bulkUnsnoozeChecks,
  clearCheckSnooze,
  deleteCheck,
  deleteSuite,
  getSuiteDeletionImpact,
  listChecks,
  listSuites,
  rebaselineCheck,
  snoozeCheck,
  type Suite,
} from '../../src/api/suites';
import { BULK_CHECKS_MAX, exceedsBulkLimit } from '../../src/components/checks/bulkLimit';
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
    getSuiteDeletionImpact: vi.fn(),
    snoozeCheck: vi.fn(),
    clearCheckSnooze: vi.fn(),
    bulkSnoozeChecks: vi.fn(),
    bulkUnsnoozeChecks: vi.fn(),
    bulkDeleteChecks: vi.fn(),
    bulkSetThresholds: vi.fn(),
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
const mockGetSuiteDeletionImpact = vi.mocked(getSuiteDeletionImpact);
const mockSnoozeCheck = vi.mocked(snoozeCheck);
const mockRebaseline = vi.mocked(rebaselineCheck);
const mockClearSnooze = vi.mocked(clearCheckSnooze);
const mockBulkSnooze = vi.mocked(bulkSnoozeChecks);
const mockBulkUnsnooze = vi.mocked(bulkUnsnoozeChecks);
const mockBulkDelete = vi.mocked(bulkDeleteChecks);
const mockBulkThresholds = vi.mocked(bulkSetThresholds);
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

// Selecting a suite navigates to /suites/:suiteId, so render both routes at the same Suites
// component (the param drives which suite is shown).
function renderPage(role: 'admin' | 'member' | 'viewer' = 'admin') {
  return render(
    <MemoryRouter initialEntries={['/suites']}>
      <WithMe role={role}>
        <AntApp>
          <Routes>
            <Route path="/suites" element={<Suites />} />
            <Route path="/suites/new" element={<div>New suite page</div>} />
            <Route path="/suites/:suiteId" element={<Suites />} />
          </Routes>
        </AntApp>
      </WithMe>
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

  it('shows engine, dimension and threshold badges on a check card (#1551)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([
      check({
        id: 'chk-gx',
        name: 'gx check',
        engine: 'gx',
        dimension: 'completeness',
        warn_threshold: 5,
        fail_threshold: 10,
      }),
      check({
        id: 'chk-dmf',
        name: 'dmf check',
        engine: 'dmf',
        dimension: null,
        warn_threshold: null,
        fail_threshold: null,
        critical_threshold: null,
      }),
    ]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await screen.findByText('gx check');

    // Engine: two checks on the same suite, one gx one dmf, must not render identically.
    expect(screen.getByText('GX')).toBeInTheDocument();
    expect(screen.getByText('DMF')).toBeInTheDocument();
    // Dimension: classified renders title-cased; unclassified is an explicit state, not hidden.
    expect(screen.getByText('Completeness')).toBeInTheDocument();
    expect(screen.getByText('Unclassified')).toBeInTheDocument();
    // Thresholds: rendered compactly, omitting the unset critical tier on the gx check, and
    // omitted entirely for the plain pass/fail dmf check (no tiers set).
    expect(screen.getByText('· warn 5 · fail 10')).toBeInTheDocument();
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

    expect(await screen.findByText('No suites yet')).toBeInTheDocument();
    // The empty state's own action, beside the header's "New suite".
    const actions = screen.getAllByRole('button', { name: 'New suite' });
    await userEvent.click(actions[actions.length - 1]);
    expect(await screen.findByText('New suite page')).toBeInTheDocument();
  });

  it('sends an author to connections first when there is no data source', async () => {
    mockListConnections.mockResolvedValue([]);
    mockListSuites.mockResolvedValue([]);

    renderPage();

    expect(
      await screen.findByText('A suite needs a data source connection to run against.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Go to connections' })).toBeInTheDocument();
  });

  it('tells a viewer why the list is empty and offers nothing to create', async () => {
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([]);

    renderPage('viewer');

    expect(
      await screen.findByText('You see a suite once someone shares it with you.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'New suite' })).not.toBeInTheDocument();
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

    // #910: dedicated error page, not the old inline alert. A plain Error is a
    // CLIENT failure → 500; only a real network failure claims 503 (#930 review).
    expect(await screen.findByText('500 — Something went wrong')).toBeInTheDocument();
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

  describe('bulk check actions', () => {
    const three = [
      check({ id: 'a', name: 'alpha' }),
      check({ id: 'b', name: 'beta' }),
      check({ id: 'c', name: 'gamma' }),
    ];

    async function openWithChecks(permission: 'edit' | 'view' = 'edit') {
      const user = userEvent.setup();
      mockListConnections.mockResolvedValue([connection]);
      mockListSuites.mockResolvedValue([suite({ my_permission: permission })]);
      mockListChecks.mockResolvedValue(three);
      renderPage();
      await user.click(await screen.findByText('orders-suite'));
      await screen.findByText('alpha');
      return user;
    }

    it('offers no bulk action until something is selected', async () => {
      await openWithChecks();
      expect(screen.getByRole('checkbox', { name: 'Select all' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Delete selected' })).not.toBeInTheDocument();
    });

    it('snoozes exactly the selected checks and clears the selection', async () => {
      const user = await openWithChecks();
      mockBulkSnooze.mockResolvedValue({ affected: 2, checks: [] });

      await user.click(screen.getByRole('checkbox', { name: 'Select alpha' }));
      await user.click(screen.getByRole('checkbox', { name: 'Select gamma' }));
      expect(screen.getByText('2 selected')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Snooze selected' }));
      await user.click(await screen.findByText('24 hours'));

      await waitFor(() => expect(mockBulkSnooze).toHaveBeenCalledWith('s1', ['a', 'c'], 24));
      expect(await screen.findByText('2 checks: alerts snoozed for 24 hours')).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText('2 selected')).not.toBeInTheDocument());
    });

    it('selects every check with "Select all" and unsnoozes them', async () => {
      const user = await openWithChecks();
      mockBulkUnsnooze.mockResolvedValue({ affected: 3, checks: [] });

      await user.click(screen.getByRole('checkbox', { name: 'Select all' }));
      await user.click(screen.getByRole('button', { name: 'Unsnooze selected' }));

      await waitFor(() => expect(mockBulkUnsnooze).toHaveBeenCalledWith('s1', ['a', 'b', 'c']));
    });

    it('deletes only after a confirmation that says what is lost', async () => {
      const user = await openWithChecks();
      mockBulkDelete.mockResolvedValue({ affected: 1, checks: [] });

      await user.click(screen.getByRole('checkbox', { name: 'Select beta' }));
      await user.click(screen.getByRole('button', { name: 'Delete selected' }));

      expect(await screen.findByRole('dialog', { name: 'Delete 1 check?' })).toBeInTheDocument();
      expect(screen.getByText(/results, version history and baseline/)).toBeInTheDocument();
      expect(mockBulkDelete).not.toHaveBeenCalled();
      await user.click(screen.getByRole('button', { name: 'Delete 1 check' }));

      await waitFor(() => expect(mockBulkDelete).toHaveBeenCalledWith('s1', ['b']));
    });

    it('refetches after a refusal, so a check deleted elsewhere leaves the selection', async () => {
      const user = await openWithChecks();
      mockBulkUnsnooze.mockRejectedValue(new Error('1 of 2 checks were not found'));

      await user.click(screen.getByRole('checkbox', { name: 'Select alpha' }));
      await user.click(screen.getByRole('checkbox', { name: 'Select beta' }));
      // Someone else deleted "beta" in the meantime: the refetch no longer returns it.
      mockListChecks.mockResolvedValue(three.filter((c) => c.id !== 'b'));
      await user.click(screen.getByRole('button', { name: 'Unsnooze selected' }));

      expect(await screen.findByText(/Bulk action failed/)).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText('beta')).not.toBeInTheDocument());
      // The retry would now name only the check that still exists.
      expect(screen.getByText('1 selected')).toBeInTheDocument();
    });

    it('treats a selection over the request cap as blocked, and the cap itself as fine', () => {
      // 501 rows are too slow to render in jsdom; the bar disables on this predicate.
      expect(exceedsBulkLimit(BULK_CHECKS_MAX)).toBe(false);
      expect(exceedsBulkLimit(BULK_CHECKS_MAX + 1)).toBe(true);
      expect(BULK_CHECKS_MAX).toBe(500); // the backend's BULK_CHECKS_MAX
    });

    it('sets thresholds on the selected checks through the dialog', async () => {
      const user = await openWithChecks();
      mockBulkThresholds.mockResolvedValue({ affected: 2, checks: [] });

      await user.click(screen.getByRole('checkbox', { name: 'Select alpha' }));
      await user.click(screen.getByRole('checkbox', { name: 'Select beta' }));
      await user.click(screen.getByRole('button', { name: 'Set thresholds' }));
      await user.click(await screen.findByRole('combobox', { name: 'Fail' }));
      const setTo = await screen.findAllByText('Set to');
      await user.click(setTo[setTo.length - 1]);
      await user.type(screen.getByRole('spinbutton', { name: 'Fail threshold' }), '5');
      await user.click(screen.getByRole('button', { name: 'Apply to 2 checks' }));

      await waitFor(() =>
        expect(mockBulkThresholds).toHaveBeenCalledWith('s1', ['a', 'b'], { fail_threshold: 5 }),
      );
      expect(await screen.findByText('Thresholds set on 2 checks')).toBeInTheDocument();
    });

    it('opens the thresholds dialog fresh each time, not with the last choice', async () => {
      const user = await openWithChecks();
      await user.click(screen.getByRole('checkbox', { name: 'Select alpha' }));
      await user.click(screen.getByRole('button', { name: 'Set thresholds' }));
      await user.click(await screen.findByRole('combobox', { name: 'Fail' }));
      const clear = await screen.findAllByText('Clear');
      await user.click(clear[clear.length - 1]);
      expect(screen.getByRole('button', { name: 'Apply to 1 check' })).toBeEnabled();
      await user.click(screen.getByRole('button', { name: 'Cancel' }));

      await user.click(screen.getByRole('button', { name: 'Set thresholds' }));

      // Nothing is chosen, so nothing can be applied by one stray click.
      expect(await screen.findByRole('button', { name: 'Apply to 1 check' })).toBeDisabled();
    });

    it('shows no selection controls to a view-only user', async () => {
      await openWithChecks('view');
      expect(screen.queryByRole('checkbox', { name: 'Select all' })).not.toBeInTheDocument();
      expect(screen.queryByRole('checkbox', { name: 'Select alpha' })).not.toBeInTheDocument();
    });
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
    mockGetSuiteDeletionImpact.mockResolvedValue({
      checks: 0,
      runs: 0,
      results: 0,
      trigger_bindings: 0,
      schedules: 0,
    });

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));

    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDeleteSuite).toHaveBeenCalledWith('s1'));
  });

  it('states the exact blast radius in the delete confirmation (#1320)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([]);
    mockGetSuiteDeletionImpact.mockResolvedValue({
      checks: 12,
      runs: 431,
      results: 5180,
      trigger_bindings: 1,
      schedules: 2,
    });

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));

    const dialog = await screen.findByRole('dialog');
    expect(mockGetSuiteDeletionImpact).toHaveBeenCalledWith('s1');
    expect(
      within(dialog).getByText(
        'Deletes 12 checks, 431 runs and 5,180 results. 1 trigger binding and 2 schedules point at this suite and will be removed. This cannot be undone.',
      ),
    ).toBeInTheDocument();
  });

  it('falls back to a plain irreversible warning when the impact fetch fails (#1320)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite()]);
    mockListChecks.mockResolvedValue([]);
    mockGetSuiteDeletionImpact.mockRejectedValue(new Error('boom'));

    renderPage();
    await user.click(await screen.findByText('orders-suite'));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));

    // Never blocks the delete itself — the confirm dialog still opens, degraded.
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(
        'Counts unavailable — this removes the suite and everything in it. This cannot be undone.',
      ),
    ).toBeInTheDocument();
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

  it('offers an editor the first check on a suite that has none', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ my_permission: 'edit' })]);
    mockListChecks.mockResolvedValue([]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));

    expect(await screen.findByText('No checks yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a check' })).toBeInTheDocument();
  });

  it('offers a view-only user no way to add a check', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ my_permission: 'view' })]);
    mockListChecks.mockResolvedValue([]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));

    expect(await screen.findByText('You have view access to this suite.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add a check' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add check' })).not.toBeInTheDocument();
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

  it('shows the batch target summary on the detail panel, marked configured-not-resolved (#1205)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([
      suite({ target: { prefix: 'raw/', pattern: 'orders_(.*)\\.csv', strategy: 'latest' } }),
    ]);
    mockListChecks.mockResolvedValue([check()]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));

    // summarizeTarget renders the configured prefix/pattern/strategy, not a resolved filename — and
    // the detail page must say so (#1205), unlike the single-file case below.
    expect(await screen.findByText('raw/orders_(.*)\\.csv (latest)')).toBeInTheDocument();
    expect(screen.getByText('Configured, not resolved')).toBeInTheDocument();
  });

  it('shows a single-file target summary with no configured-not-resolved marker (#1205)', async () => {
    const user = userEvent.setup();
    mockListConnections.mockResolvedValue([connection]);
    mockListSuites.mockResolvedValue([suite({ target: { path: 'raw/orders.csv' } })]);
    mockListChecks.mockResolvedValue([check()]);

    renderPage();
    await user.click(await screen.findByText('orders-suite'));

    // A single-file target's path IS the exact target — no "not resolved"
    // ambiguity, so the batch marker must not appear.
    expect(await screen.findByText('raw/orders.csv')).toBeInTheDocument();
    expect(screen.queryByText('Configured, not resolved')).not.toBeInTheDocument();
  });
});
