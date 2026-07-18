import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type AssetSummary, listAssets } from '../../src/api/assets';
import { Assets } from '../../src/pages/Assets';

vi.mock('../../src/api/assets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/assets')>()),
  listAssets: vi.fn(),
}));
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
  has_operational_error: false,
  has_cancelled_run: false,
  has_skip: false,
  is_accessible: true,
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
  it('defaults to the connection-rooted tree (namespace root, table leaf with health)', async () => {
    mockList.mockResolvedValue([ASSET]);
    renderPage();
    // Root = the OL namespace; leaf = the table segment (not the full dotted name),
    // with its env + health tags — the drill-down levels are expanded by default.
    // The root reads as a datasource now, not a raw OL namespace (#830).
    expect(await screen.findByText('Snowflake · acct')).toBeInTheDocument();
    expect(screen.getByText('ANALYTICS')).toBeInTheDocument();
    expect(screen.getByText('PUBLIC')).toBeInTheDocument();
    expect(screen.getByText('ORDERS')).toBeInTheDocument();
    expect(screen.getByText('dev')).toBeInTheDocument();
    expect(screen.getByText('Failing')).toBeInTheDocument();
  });

  it('opens the detail when a leaf asset is selected', async () => {
    mockList.mockResolvedValue([ASSET]);
    renderPage();
    await userEvent.click(await screen.findByText('ORDERS'));
    expect(await screen.findByText('detail for asset')).toBeInTheDocument();
  });

  it('selecting a folder node does not navigate', async () => {
    mockList.mockResolvedValue([ASSET]);
    renderPage();
    // ANALYTICS is a database folder (no asset) — clicking it must not open a detail.
    await userEvent.click(await screen.findByText('ANALYTICS'));
    expect(screen.queryByText('detail for asset')).not.toBeInTheDocument();
  });

  it('switches to the flat "All assets" table and back', async () => {
    mockList.mockResolvedValue([ASSET]);
    renderPage();
    await screen.findByText('Snowflake · acct');

    await userEvent.click(screen.getByText('All assets'));
    // The table shows the full dotted name + suite count (2) the tree omits.
    expect(await screen.findByText('ANALYTICS.PUBLIC.ORDERS')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();

    // Row click still navigates from the table view.
    await userEvent.click(screen.getByText('ANALYTICS.PUBLIC.ORDERS'));
    expect(await screen.findByText('detail for asset')).toBeInTheDocument();
  });

  it('groups assets under their datasource and drills into each schema', async () => {
    mockList.mockResolvedValue([
      ASSET,
      { ...ASSET, id: 'a2', name: 'ANALYTICS.PUBLIC.CUSTOMERS', env: 'dev' },
      { ...ASSET, id: 'a3', namespace: 's3://lake', name: 'raw/events.parquet', env: 'qa' },
    ]);
    renderPage();
    // Two datasource roots.
    expect(await screen.findByText('Snowflake · acct')).toBeInTheDocument();
    expect(screen.getByText('S3 · lake')).toBeInTheDocument();
    // Both tables share the PUBLIC schema folder (merged), each a distinct leaf.
    expect(screen.getByText('ORDERS')).toBeInTheDocument();
    expect(screen.getByText('CUSTOMERS')).toBeInTheDocument();
    // The flat-file asset splits on '/'.
    expect(screen.getByText('raw')).toBeInTheDocument();
    expect(screen.getByText('events.parquet')).toBeInTheDocument();
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

it('renders a redacted asset as a locked, non-navigating entry in its group (#920)', async () => {
  mockList.mockResolvedValue([
    ASSET,
    {
      ...ASSET,
      id: 'r1',
      name: null,
      env: null,
      last_seen: null,
      is_accessible: false,
      name_prefix_segments: ['ANALYTICS', 'PUBLIC'],
      suite_count: 0,
    },
  ]);
  renderPage();
  const locked = await screen.findByLabelText('A restricted asset outside your access');
  expect(locked).toBeInTheDocument();
  expect(screen.queryByText(/MART_/)).not.toBeInTheDocument(); // no hidden identity anywhere
  fireEvent.click(locked);
  expect(screen.queryByTestId('detail-page')).not.toBeInTheDocument(); // not openable
});
