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
    has_operational_error: false,
    has_cancelled_run: false,
    has_skip: false,
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
      // A run that failed operationally: the backend rolls that up as BOTH a
      // failed run and an operational error (the #803 connection axis), and it
      // still "needs attention" — DataQ couldn't evaluate the asset at all.
      asset({
        id: 'a3',
        name: 'RUNFAIL.ORDERS',
        has_failed_run: true,
        has_operational_error: true,
      }),
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

  it('sorts the attention preview by severity so a critical never hides behind warns', async () => {
    // Backend order is (namespace, name)-alphabetical: five warns first, then a
    // fail and a critical that would fall past the 5-row preview unsorted.
    mockListAssets.mockResolvedValue([
      ...[1, 2, 3, 4, 5].map((n) =>
        asset({ id: `w${n}`, name: `A${n}.WARN`, worst_severity: 'warn' }),
      ),
      asset({ id: 'f1', name: 'Y.FAIL', worst_severity: 'fail' }),
      asset({ id: 'c1', name: 'Z.CRITICAL', worst_severity: 'critical' }),
    ]);
    renderLead();

    // Critical + fail lead the preview; the overflow fold holds two warns.
    expect(await screen.findByText('Z.CRITICAL')).toBeInTheDocument();
    expect(screen.getByText('Y.FAIL')).toBeInTheDocument();
    expect(screen.getByText('+2 more')).toBeInTheDocument();
    // Stable within a tier: the last two warns are the ones folded away.
    expect(screen.getByText('A1.WARN')).toBeInTheDocument();
    expect(screen.queryByText('A4.WARN')).not.toBeInTheDocument();
    expect(screen.queryByText('A5.WARN')).not.toBeInTheDocument();
  });

  it('flags a possibly-truncated list at the backend 200-row cap', async () => {
    mockListAssets.mockResolvedValue(
      Array.from({ length: 200 }, (_, i) => asset({ id: `a${i}`, name: `T.ASSET_${i}` })),
    );
    renderLead();
    expect(
      await screen.findByText(/Showing the first 200 assets — open Assets for the full list/),
    ).toBeInTheDocument();
  });

  it('shows no truncation note below the cap', async () => {
    mockListAssets.mockResolvedValue([asset()]);
    renderLead();
    await screen.findByText('All monitored assets are healthy.');
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
  });

  it('degrades gracefully when the assets read fails', async () => {
    mockListAssets.mockRejectedValue(new Error('boom'));
    renderLead();
    expect(await screen.findByText('Asset health is unavailable right now.')).toBeInTheDocument();
  });
});
