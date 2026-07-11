import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type AssetDetail as AssetDetailData, getAsset, updateAsset } from '../../src/api/assets';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { AssetDetail } from '../../src/pages/AssetDetail';

vi.mock('../../src/api/assets', () => ({ getAsset: vi.fn(), updateAsset: vi.fn() }));
const mockGet = vi.mocked(getAsset);
const mockUpdate = vi.mocked(updateAsset);

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
  },
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
    },
  ],
  downstream: [
    {
      id: 'd1',
      namespace: 'snowflake://acct',
      name: 'ANALYTICS.MART.REVENUE',
      env: 'dev',
      is_monitored: true,
    },
  ],
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

afterEach(() => vi.clearAllMocks());

function renderPage({ isAdmin = false }: { isAdmin?: boolean } = {}) {
  return render(
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
    </MeContext.Provider>,
  );
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

  it('renders upstream/downstream lineage with monitored flags', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    expect(await screen.findByText('RAW.ORDERS')).toBeInTheDocument();
    expect(screen.getByText('Unmonitored')).toBeInTheDocument();
    expect(screen.getByText('ANALYTICS.MART.REVENUE')).toBeInTheDocument();
    expect(screen.getByText('Monitored')).toBeInTheDocument();
  });

  it('links a composing suite to its suite page', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    await userEvent.click(await screen.findByRole('button', { name: 'Orders quality' }));
    expect(await screen.findByText('suite page')).toBeInTheDocument();
  });

  it('links the latest run to its run page', async () => {
    mockGet.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByText('Orders quality');
    await userEvent.click(screen.getByRole('button', { name: /2026/ }));
    expect(await screen.findByText('run page')).toBeInTheDocument();
  });

  it('renders empty lineage panels when there are none', async () => {
    mockGet.mockResolvedValue({ ...DETAIL, upstream: [], downstream: [] });
    renderPage();
    expect(await screen.findByText('No known upstream sources.')).toBeInTheDocument();
    expect(screen.getByText('No known downstream consumers.')).toBeInTheDocument();
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
});
