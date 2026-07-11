import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type AssetSummary, listAssets } from '../../src/api/assets';
import { AssetHealthLead } from '../../src/components/dashboard/AssetHealthLead';

vi.mock('../../src/api/assets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/assets')>();
  return { ...actual, listAssets: vi.fn() };
});

const mockListAssets = vi.mocked(listAssets);

function asset(overrides: Partial<AssetSummary> = {}): AssetSummary {
  return {
    id: 'a1',
    namespace: 'snowflake://acct',
    name: 'ANALYTICS.PUBLIC.ORDERS',
    env: 'dev',
    description: null,
    owner_user_id: null,
    last_seen: '2026-07-01T10:00:00Z',
    suite_count: 1,
    worst_severity: null,
    checks_total: 4,
    checks_passed: 4,
    last_run_at: '2026-07-01T09:00:00Z',
    has_failed_run: false,
    has_active_run: false,
    ...overrides,
  };
}

function renderLead() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route path="/dashboard" element={<AssetHealthLead />} />
        <Route path="/assets" element={<div>assets list</div>} />
        <Route path="/assets/:assetId" element={<div>asset page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('AssetHealthLead (#773)', () => {
  it('summarises monitored / attention / in-progress counts and lists failing assets', async () => {
    mockListAssets.mockResolvedValue([
      asset({ id: 'a1', name: 'HEALTHY.ORDERS' }),
      asset({ id: 'a2', name: 'FAILING.ORDERS', worst_severity: 'fail' }),
      asset({ id: 'a3', name: 'RUNFAIL.ORDERS', has_failed_run: true }),
      asset({ id: 'a4', name: 'INFLIGHT.ORDERS', has_active_run: true }),
    ]);
    renderLead();

    expect(await screen.findByText('Asset health')).toBeInTheDocument();
    // Both non-passing assets surface in the attention list.
    expect(screen.getByText('FAILING.ORDERS')).toBeInTheDocument();
    expect(screen.getByText('RUNFAIL.ORDERS')).toBeInTheDocument();
    // A healthy / in-progress asset is NOT in the attention list.
    expect(screen.queryByText('HEALTHY.ORDERS')).not.toBeInTheDocument();
    // Tiles: Monitored 4, Need attention 2, In progress 1.
    expect(screen.getByText('Monitored')).toBeInTheDocument();
    expect(screen.getByText('Need attention')).toBeInTheDocument();
    expect(screen.getByText('In progress')).toBeInTheDocument();
  });

  it('says all healthy when nothing needs attention', async () => {
    mockListAssets.mockResolvedValue([asset(), asset({ id: 'a2', name: 'OTHER' })]);
    renderLead();
    expect(await screen.findByText('All monitored assets are healthy.')).toBeInTheDocument();
  });

  it('shows an empty state when there are no monitored assets', async () => {
    mockListAssets.mockResolvedValue([]);
    renderLead();
    expect(await screen.findByText(/No monitored assets yet/)).toBeInTheDocument();
  });

  it('navigates to the assets list from the header link', async () => {
    mockListAssets.mockResolvedValue([asset()]);
    renderLead();
    await userEvent.click(await screen.findByText(/View all assets/));
    expect(await screen.findByText('assets list')).toBeInTheDocument();
  });

  it('opens an individual asset from the attention list', async () => {
    mockListAssets.mockResolvedValue([asset({ worst_severity: 'critical' })]);
    renderLead();
    await userEvent.click(await screen.findByText('ANALYTICS.PUBLIC.ORDERS'));
    expect(await screen.findByText('asset page')).toBeInTheDocument();
  });

  it('degrades gracefully when the assets read fails', async () => {
    mockListAssets.mockRejectedValue(new Error('boom'));
    renderLead();
    expect(await screen.findByText('Asset health is unavailable right now.')).toBeInTheDocument();
  });
});
