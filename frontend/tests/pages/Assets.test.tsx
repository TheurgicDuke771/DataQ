import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type AssetSummary, listAssets } from '../../src/api/assets';
import { Assets } from '../../src/pages/Assets';

vi.mock('../../src/api/assets', () => ({ listAssets: vi.fn() }));
const mockList = vi.mocked(listAssets);

const ASSET: AssetSummary = {
  id: 'a1',
  namespace: 'snowflake://acct',
  name: 'ANALYTICS.PUBLIC.ORDERS',
  env: 'dev',
  description: null,
  owner_user_id: null,
  last_seen: '2026-07-01T10:00:00Z',
  suite_count: 2,
  worst_severity: 'fail',
  checks_total: 8,
  checks_passed: 6,
  last_run_at: '2026-07-01T09:00:00Z',
  has_failed_run: false,
  has_active_run: false,
};

afterEach(() => vi.clearAllMocks());

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/assets']}>
      <Routes>
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/:assetId" element={<div>detail for asset</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Assets page', () => {
  it('lists visible assets with health + suite count', async () => {
    mockList.mockResolvedValue([ASSET]);
    renderPage();
    expect(await screen.findByText('ANALYTICS.PUBLIC.ORDERS')).toBeInTheDocument();
    expect(screen.getByText('snowflake://acct')).toBeInTheDocument();
    expect(screen.getByText('Failing')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
  });

  it('navigates to the detail on row click', async () => {
    mockList.mockResolvedValue([ASSET]);
    renderPage();
    await userEvent.click(await screen.findByText('ANALYTICS.PUBLIC.ORDERS'));
    expect(await screen.findByText('detail for asset')).toBeInTheDocument();
  });

  it('shows an empty state when there are no assets', async () => {
    mockList.mockResolvedValue([]);
    renderPage();
    await waitFor(() => expect(screen.getByText(/No assets yet/)).toBeInTheDocument());
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    renderPage();
    expect(await screen.findByText('Failed to load assets')).toBeInTheDocument();
  });
});
