import { App as AntApp } from 'antd';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { listAdminUsers } from '../../src/api/admin';
import { type AssetDetail as AssetDetailData, getAsset, updateAsset } from '../../src/api/assets';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { AssetDetail } from '../../src/pages/AssetDetail';

vi.mock('../../src/api/assets', () => ({ getAsset: vi.fn(), updateAsset: vi.fn() }));
// The OwnerBlock (#773, admin-only) sources its user picker from /admin/users.
vi.mock('../../src/api/admin', () => ({ listAdminUsers: vi.fn() }));
// The AssetDetail now renders the IncidentsPanel, which fetches incidents on
// mount — stub it out here (its own behaviour is covered in IncidentsPanel.test).
vi.mock('../../src/api/incidents', () => ({
  listIncidents: vi.fn().mockResolvedValue([]),
  acknowledgeIncident: vi.fn(),
  resolveIncident: vi.fn(),
}));
const mockGet = vi.mocked(getAsset);
const mockUpdate = vi.mocked(updateAsset);
const mockAdminUsers = vi.mocked(listAdminUsers);

const ADMIN_USERS = [
  {
    id: 'u-1',
    email: 'user@dataq.io',
    display_name: 'User',
    last_seen_at: null,
    created_at: '2026-07-01T00:00:00Z',
    owned_suite_count: 0,
    shared_suite_count: 0,
  },
  {
    id: 'u-2',
    email: 'olivia@dataq.io',
    display_name: 'Olivia Owner',
    last_seen_at: null,
    created_at: '2026-07-01T00:00:00Z',
    owned_suite_count: 0,
    shared_suite_count: 0,
  },
];

const DETAIL: AssetDetailData = {
  summary: {
    id: 'a1',
    namespace: 'snowflake://acct',
    name: 'ANALYTICS.PUBLIC.ORDERS',
    env: 'dev',
    description: 'The canonical orders table',
    owner_user_id: null,
    last_seen: '2026-07-01T10:00:00Z',
    suite_count: 2,
    worst_severity: 'fail',
    checks_total: 8,
    checks_passed: 6,
    last_run_at: '2026-07-01T09:00:00Z',
    has_failed_run: false,
    has_active_run: false,
    has_operational_error: false,
    has_cancelled_run: false,
    has_skip: false,
  },
  restricted_suite_count: 0,
  suites: [
    {
      suite_id: 's1',
      name: 'Orders quality',
      my_permission: 'owner',
      latest_run: {
        run_id: 'r1',
        status: 'succeeded',
        worst_severity: 'fail',
        checks_total: 4,
        checks_passed: 3,
        finished_at: '2026-07-01T09:00:00Z',
        created_at: '2026-07-01T08:59:00Z',
      },
    },
    {
      suite_id: 's2',
      name: 'Orders volume',
      my_permission: 'view',
      latest_run: {
        run_id: null,
        status: null,
        worst_severity: null,
        checks_total: 0,
        checks_passed: 0,
        finished_at: null,
        created_at: null,
      },
    },
  ],
  upstream: [
    {
      id: 'u1',
      namespace: 'snowflake://acct',
      name: 'RAW.ORDERS',
      env: 'dev',
      is_monitored: false,
      depth: 1,
    },
  ],
  downstream: [
    {
      id: 'd1',
      namespace: 'snowflake://acct',
      name: 'ANALYTICS.MART.REVENUE',
      env: 'dev',
      is_monitored: true,
      depth: 1,
    },
  ],
  lineage_edges: [
    { source: 'u1', target: 'a1' },
    { source: 'a1', target: 'd1' },
  ],
  failing_lineage_sources: [],
  warehouse_lineage_status: [],
};

function meState(isAdmin: boolean): AsyncState<MeResponse> {
  return {
    status: 'ok',
    data: {
      id: 'u-1',
      aad_object_id: 'oid-1',
      email: 'user@dataq.io',
      display_name: 'User',
      last_seen_at: null,
      is_workspace_admin: isAdmin,
    },
  };
}

// Clear in beforeEach (not only afterEach) so any call recorded between tests
// by a late-flushing effect can never bleed into the next test's counts, THEN
// default the user list to a resolved array so `users.find(...)` never sees
// `undefined` (the OwnerBlock fetches it whenever an admin renders).
beforeEach(() => {
  vi.clearAllMocks();
  mockAdminUsers.mockResolvedValue(ADMIN_USERS);
});
afterEach(() => vi.clearAllMocks());

function pageTree(isAdmin: boolean) {
  return (
    <MeContext.Provider value={meState(isAdmin)}>
      <AntApp>
        <MemoryRouter initialEntries={['/assets/a1']}>
          <Routes>
            <Route path="/assets/:assetId" element={<AssetDetail />} />
            <Route path="/assets" element={<div>assets list</div>} />
            <Route path="/suites/:suiteId" element={<div>suite page</div>} />
            <Route path="/results/:runId" element={<div>run page</div>} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </MeContext.Provider>
  );
}

function renderPage({ isAdmin = false }: { isAdmin?: boolean } = {}) {
  return render(pageTree(isAdmin));
}

describe('AssetDetail page', () => {
  it('renders identity, description, and health across ≥2 suites', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    expect(
      await screen.findByRole('heading', { name: 'ANALYTICS.PUBLIC.ORDERS' }),
    ).toBeInTheDocument();
    expect(screen.getByText('The canonical orders table')).toBeInTheDocument();
    // Both composing suites render — the acceptance criterion.
    expect(screen.getByText('Orders quality')).toBeInTheDocument();
    expect(screen.getByText('Orders volume')).toBeInTheDocument();
    expect(screen.getByText('Monitored by 2 suites')).toBeInTheDocument();
    // Per-suite health: one failing (also rolled up to the asset tag), one no-run.
    expect(screen.getAllByText('Failing').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('No runs')).toBeInTheDocument();
    expect(screen.getByText('3 / 4')).toBeInTheDocument();
  });

  // #803: the header carries TWO labelled health axes, not one conflated badge.
  it('renders connection health and data-quality health as separate labelled axes', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByRole('heading', { name: 'ANALYTICS.PUBLIC.ORDERS' });
    expect(screen.getByText('Connection')).toBeInTheDocument();
    expect(screen.getByText('Data quality')).toBeInTheDocument();
    // The fixture connected fine (no operational error) but the data is failing:
    // Connection must read Reachable while Data quality reads Failing.
    expect(screen.getByText('Reachable')).toBeInTheDocument();
    expect(screen.getAllByText('Failing').length).toBeGreaterThanOrEqual(1);
  });

  it('reads Errors on the connection axis without touching data-quality health', async () => {
    // A run that succeeded but whose checks threw: DataQ could not evaluate, so the
    // connection axis errors and the data-quality axis honestly says "No data" —
    // it must NOT go green, and it must NOT claim a data failure.
    mockGet.mockResolvedValue({
      ...DETAIL,
      summary: {
        ...DETAIL.summary,
        worst_severity: null,
        checks_total: 0,
        has_operational_error: true,
      },
    });
    renderPage();
    await screen.findByRole('heading', { name: 'ANALYTICS.PUBLIC.ORDERS' });
    expect(screen.getByText('Errors')).toBeInTheDocument();
    // Scope to the health *tag*: the empty-lineage <Empty> also renders an SVG
    // <title>No data</title>, which would otherwise match.
    const noDataTags = screen
      .getAllByText('No data')
      .filter((el) => el.classList.contains('ant-tag'));
    expect(noDataTags).toHaveLength(1);
    expect(screen.queryByText('Reachable')).not.toBeInTheDocument();
  });

  // #805: one left-to-right graph, not two list boxes.
  it('renders lineage as a graph: upstream, the centre asset, and downstream', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    const graph = await screen.findByRole('img', { name: /Lineage graph/ });
    expect(graph).toBeInTheDocument();
    // Each node carries its full identity in an SVG <title>; the visible label is
    // the leaf segment (both ORDERS assets share a leaf, so assert on identity).
    expect(screen.getByLabelText('ANALYTICS.PUBLIC.ORDERS (this asset)')).toBeInTheDocument();
    expect(screen.getByLabelText('Open asset RAW.ORDERS')).toBeInTheDocument(); // upstream
    expect(screen.getByLabelText(/Open asset ANALYTICS\.MART\.REVENUE/)).toBeInTheDocument();
    // One drawn edge per real backend edge (u1→a1, a1→d1).
    expect(graph.querySelectorAll('path[marker-end]')).toHaveLength(2);
    expect(screen.getByText('1 upstream · 1 downstream')).toBeInTheDocument();
  });

  // Regression: useAsyncData fetches on mount only, so an asset→asset navigation
  // (first made possible by the #805 clickable lineage nodes) must REMOUNT the page
  // — otherwise the new URL renders the previous asset's data.
  it('refetches when navigating from one asset to another (no stale detail)', async () => {
    mockGet.mockResolvedValue(DETAIL);
    render(
      <MeContext.Provider value={meState(false)}>
        <AntApp>
          <MemoryRouter initialEntries={['/assets/a1']}>
            <Routes>
              <Route path="/assets/:assetId" element={<AssetDetail />} />
            </Routes>
          </MemoryRouter>
        </AntApp>
      </MeContext.Provider>,
    );
    await screen.findByRole('img', { name: /Lineage graph/ });
    expect(mockGet).toHaveBeenCalledWith('a1');

    // Click the downstream lineage node → the route param becomes d1, and the page
    // must fetch THAT asset rather than keep showing a1's.
    mockGet.mockClear();
    await userEvent.click(screen.getByLabelText(/Open asset ANALYTICS\.MART\.REVENUE/));
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith('d1'));
  }, 15000);

  it('does not make the centre asset clickable — you are already on it', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByRole('img', { name: /Lineage graph/ });
    expect(screen.queryByLabelText('Open asset ANALYTICS.PUBLIC.ORDERS')).not.toBeInTheDocument();
  });

  // CI module-import cost + userEvent on antd eats the 5s default (#778) — 15s budgets.
  it('links a composing suite to its suite page', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Orders quality' }));
    expect(await screen.findByText('suite page')).toBeInTheDocument();
  }, 15000);

  it('links the latest run to its run page', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByText('Orders quality');
    await userEvent.click(screen.getByRole('button', { name: /2026/ }));
    expect(await screen.findByText('run page')).toBeInTheDocument();
  }, 15000);

  it('draws the asset alone when it has no lineage, and says so', async () => {
    mockGet.mockResolvedValue({ ...DETAIL, upstream: [], downstream: [], lineage_edges: [] });
    renderPage();
    expect(await screen.findByText('No lineage recorded for this asset.')).toBeInTheDocument();
    // The asset's own box is still drawn: an <Empty> icon in its place read as "there is
    // nothing here", when the truth is "here is the asset, nothing attached to it yet".
    expect(screen.getByRole('img', { name: /Lineage graph/ })).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockGet.mockRejectedValue(new Error('nope'));
    renderPage();
    expect(await screen.findByText('Failed to load asset')).toBeInTheDocument();
  });

  // ── admin-only description edit (#760; backend PATCH is the security gate) ──

  it('hides the description edit from non-admins', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage({ isAdmin: false });
    await screen.findByText('The canonical orders table');
    expect(screen.queryByRole('button', { name: /Edit/ })).not.toBeInTheDocument();
  });

  it('lets a workspace admin edit the description', async () => {
    mockGet.mockResolvedValue(DETAIL);
    mockUpdate.mockResolvedValue({ ...DETAIL.summary, description: 'Updated text' });
    renderPage({ isAdmin: true });
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }));
    const box = await screen.findByPlaceholderText(/What is this asset/);
    await userEvent.clear(box);
    await userEvent.type(box, 'Updated text');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('a1', { description: 'Updated text' }),
    );
    // Saving reloads the detail.
    expect(mockGet.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('clears the description with an explicit null when saved empty', async () => {
    mockGet.mockResolvedValue(DETAIL);
    mockUpdate.mockResolvedValue({ ...DETAIL.summary, description: null });
    renderPage({ isAdmin: true });
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }));
    await userEvent.clear(await screen.findByPlaceholderText(/What is this asset/));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('a1', { description: null }));
  });

  it('offers the edit affordance to an admin even with no description yet', async () => {
    mockGet.mockResolvedValue({
      ...DETAIL,
      summary: { ...DETAIL.summary, description: null },
    });
    renderPage({ isAdmin: true });
    expect(await screen.findByText('No description yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Edit/ })).toBeInTheDocument();
  });

  it('surfaces a failed metadata update', async () => {
    mockGet.mockResolvedValue(DETAIL);
    mockUpdate.mockRejectedValue(new Error('forbidden'));
    renderPage({ isAdmin: true });
    await userEvent.click(await screen.findByRole('button', { name: /Edit/ }));
    await screen.findByPlaceholderText(/What is this asset/);
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/Update failed: forbidden/)).toBeInTheDocument();
  });

  // ── admin-only owner reassignment (#773; backend PATCH is the security gate) ──

  it('hides the owner block entirely from non-admins', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage({ isAdmin: false });
    await screen.findByText('The canonical orders table');
    expect(screen.queryByText('Owner:')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Reassign owner/ })).not.toBeInTheDocument();
    // Structural with the mount gate: OwnerBlock never mounts for a non-admin,
    // so the admin-only /admin/users endpoint is never called.
    expect(mockAdminUsers).not.toHaveBeenCalled();
  });

  it('fetches the user list once adminness resolves after mount (/me race)', async () => {
    // AuthGate renders children before /me resolves, so an admin deep-linking
    // into an asset first renders as non-admin. The OwnerBlock is MOUNT-gated on
    // adminness: when /me flips is_workspace_admin to true, the block mounts and
    // its on-mount fetch fires — no reload or full remount needed.
    mockGet.mockResolvedValue(DETAIL);
    const { rerender } = render(pageTree(false));
    await screen.findByText('The canonical orders table');
    expect(mockAdminUsers).not.toHaveBeenCalled();

    rerender(pageTree(true));
    expect(await screen.findByText('Owner:')).toBeInTheDocument();
    await waitFor(() => expect(mockAdminUsers).toHaveBeenCalledTimes(1));
  });

  it('surfaces a failed user-list load instead of a silently-empty picker', async () => {
    mockGet.mockResolvedValue(DETAIL);
    mockAdminUsers.mockRejectedValue(new Error('boom'));
    renderPage({ isAdmin: true });
    expect(await screen.findByText('The user list is unavailable right now.')).toBeInTheDocument();
    // Reassignment is disabled — there is nothing valid to pick.
    expect(screen.getByRole('button', { name: /Reassign owner/ })).toBeDisabled();
  });

  it('shows an unassigned owner and the reassign affordance to an admin', async () => {
    mockGet.mockResolvedValue(DETAIL); // owner_user_id: null
    renderPage({ isAdmin: true });
    expect(await screen.findByText('Owner:')).toBeInTheDocument();
    expect(screen.getByText('Unassigned')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Reassign owner/ })).toBeInTheDocument();
  });

  it('resolves the owner display name (not a bare UUID) from the user list', async () => {
    mockGet.mockResolvedValue({
      ...DETAIL,
      summary: { ...DETAIL.summary, owner_user_id: 'u-2' },
    });
    renderPage({ isAdmin: true });
    // The name, resolved from /admin/users — and never the raw id.
    expect(await screen.findByText('Olivia Owner')).toBeInTheDocument();
    expect(screen.queryByText('u-2')).not.toBeInTheDocument();
  });

  it('reassigns the owner to a picked user', async () => {
    mockGet.mockResolvedValue(DETAIL);
    mockUpdate.mockResolvedValue({ ...DETAIL.summary, owner_user_id: 'u-2' });
    renderPage({ isAdmin: true });
    await userEvent.click(await screen.findByRole('button', { name: /Reassign owner/ }));
    // Open the antd Select and pick a user from the portal-rendered dropdown.
    await userEvent.click(await screen.findByRole('combobox'));
    await userEvent.click(await screen.findByText('Olivia Owner'));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('a1', { owner_user_id: 'u-2' }));
    expect(mockGet.mock.calls.length).toBeGreaterThanOrEqual(2);
  }, 15000);

  it('unassigns the owner with an explicit null', async () => {
    mockGet.mockResolvedValue({
      ...DETAIL,
      summary: { ...DETAIL.summary, owner_user_id: 'u-2' },
    });
    mockUpdate.mockResolvedValue({ ...DETAIL.summary, owner_user_id: null });
    renderPage({ isAdmin: true });
    await userEvent.click(await screen.findByRole('button', { name: /Reassign owner/ }));
    // Clear the pre-filled selection (allowClear) → save sends null. antd's clear
    // icon is CSS-hover-revealed, so fireEvent (not userEvent's visibility check).
    const clear = document.querySelector('.ant-select-clear');
    expect(clear).not.toBeNull();
    fireEvent.mouseDown(clear as Element);
    fireEvent.click(clear as Element);
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('a1', { owner_user_id: null }));
  }, 15000);
});

// #828 — the lineage empty state must never lie.
//
// Prod lineage was dark for six days behind an expired credential and this card said
// "No lineage recorded for this asset", which is exactly what it says for an asset that
// genuinely has no upstreams. A broken integration and an isolated table were
// indistinguishable. These pin the difference.
describe('AssetDetail — a failing lineage source (#828)', () => {
  it('warns instead of showing a clean empty state when a lineage source is failing', async () => {
    mockGet.mockResolvedValue({
      ...DETAIL,
      upstream: [],
      downstream: [],
      lineage_edges: [],
      failing_lineage_sources: [
        {
          connection_id: 'c-1',
          name: 'dbt — Retail',
          type: 'dbt',
          consecutive_failures: 864,
          last_error: 'authentication failed',
          last_polled_at: '2026-07-13T00:00:00Z',
        },
      ],
      warehouse_lineage_status: [],
    });

    renderPage();

    expect(await screen.findByText(/a source is failing/i)).toBeInTheDocument();
    expect(screen.getByText(/dbt — Retail/)).toBeInTheDocument();
    expect(screen.getByText(/864/)).toBeInTheDocument();
    expect(screen.getByText(/authentication failed/)).toBeInTheDocument();
    // and the empty state itself stops asserting the absence is the truth
    expect(screen.getByText(/may not be the truth/i)).toBeInTheDocument();
  });

  it('shows the plain empty state when every lineage source is healthy', async () => {
    mockGet.mockResolvedValue({
      ...DETAIL,
      upstream: [],
      downstream: [],
      lineage_edges: [],
      failing_lineage_sources: [],
      warehouse_lineage_status: [],
    });

    renderPage();

    expect(await screen.findByText('No lineage recorded for this asset.')).toBeInTheDocument();
    expect(screen.queryByText(/a source is failing/i)).not.toBeInTheDocument();
  });

  it('counts restricted suites in the title and states they still shape health (ADR 0037)', async () => {
    mockGet.mockResolvedValue({ ...DETAIL, restricted_suite_count: 3 });

    renderPage();

    // 2 visible + 3 restricted — the title matches the workspace-true rollup.
    expect(await screen.findByText('Monitored by 5 suites')).toBeInTheDocument();
    expect(
      screen.getByText(/3 more suites monitor this asset but are outside your access/),
    ).toBeInTheDocument();
    // The restricted suites are counted, never named.
    expect(screen.getByText('Orders quality')).toBeInTheDocument();
    expect(screen.getByText('Orders volume')).toBeInTheDocument();
  });

  it('shows no restricted note when every composing suite is visible', async () => {
    mockGet.mockResolvedValue(DETAIL);

    renderPage();

    expect(await screen.findByText('Monitored by 2 suites')).toBeInTheDocument();
    expect(screen.queryByText(/outside your access/)).not.toBeInTheDocument();
  });
});
