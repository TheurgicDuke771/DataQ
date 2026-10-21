import { render, screen, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type CoverageFigures, getCoverageFigures } from '../../src/api/dashboard';
import { MeContext } from '../../src/auth/meContext';
import { CoveragePanel } from '../../src/components/dashboard/CoveragePanel';
import { meAt } from '../pages/admin/adminFixtures';

vi.mock('../../src/api/dashboard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/dashboard')>()),
  getCoverageFigures: vi.fn(),
}));
const mockGet = vi.mocked(getCoverageFigures);

const FIGURES: CoverageFigures = {
  assets_total: 6,
  assets_watched: 3,
  assets_watched_authored: 2,
  assets_watched_auto_only: 1,
  coverage_pct: 50,
  coverage_window_days: 7,
  false_positive_window_days: 30,
  resolved: 5,
  stated: 4,
  unstated: 1,
  false_positive: 1,
  false_positive_rate: 25,
};

function renderAs(role: 'admin' | 'member', node: ReactElement = <CoveragePanel />) {
  return render(
    <MemoryRouter>
      <MeContext.Provider value={meAt(role)}>{node}</MeContext.Provider>
    </MemoryRouter>,
  );
}

const BREAKDOWN: CoverageFigures = {
  ...FIGURES,
  false_positives_by_baseline: [
    { key: 'volume', label: 'Row count', stated: 1, false_positive: 0, rate: 0 },
    { key: 'profile', label: 'Column profile', stated: 3, false_positive: 1, rate: 33.3 },
  ],
  false_positives_by_connection: [
    { key: 'c1', label: 'warehouse (prod)', stated: 4, false_positive: 1, rate: 25 },
  ],
};

afterEach(() => vi.clearAllMocks());

describe('CoveragePanel', () => {
  it('shows both figures with their denominators', async () => {
    mockGet.mockResolvedValue(FIGURES);
    renderAs('member');

    expect(await screen.findByText('Coverage, across the workspace')).toBeInTheDocument();
    expect(
      screen.getByText(
        '3 of 6 assets had a suite complete a run: 2 with authored checks, 1 by automatic coverage only.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '1 of 4 resolved incidents marked a false positive. 1 more resolved without saying.',
      ),
    ).toBeInTheDocument();
  });

  it('says "not measured" instead of 0% when no resolution was stated', async () => {
    mockGet.mockResolvedValue({
      ...FIGURES,
      stated: 0,
      unstated: 2,
      resolved: 2,
      false_positive: 0,
      false_positive_rate: null,
    });
    renderAs('member');

    expect(
      await screen.findByText(
        'Not measured: 2 incidents resolved, none said what it turned out to be.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('says there is nothing to cover when the inventory is empty', async () => {
    mockGet.mockResolvedValue({
      ...FIGURES,
      assets_total: 0,
      assets_watched: 0,
      assets_watched_authored: 0,
      assets_watched_auto_only: 0,
      coverage_pct: null,
      resolved: 0,
      stated: 0,
      unstated: 0,
      false_positive: 0,
      false_positive_rate: null,
    });
    renderAs('member');

    expect(await screen.findByText('No assets in the inventory yet.')).toBeInTheDocument();
    expect(
      screen.getByText('Not measured: no incident on an automatic suite was resolved by a person.'),
    ).toBeInTheDocument();
  });

  it('degrades to a line of text when the figures cannot be loaded', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    renderAs('member');
    expect(
      await screen.findByText('Coverage figures are unavailable right now.'),
    ).toBeInTheDocument();
  });

  it('splits the rate by check and by connection, each with its denominator', async () => {
    mockGet.mockResolvedValue(BREAKDOWN);
    renderAs('admin');

    const profile = (await screen.findByText('Column profile')).closest('tr') as HTMLElement;
    expect(
      within(screen.getByRole('table', { name: 'By automatic check' })).getByText('Column profile'),
    ).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'By connection' })).toBeInTheDocument();
    expect(within(profile).getByText('1 of 3')).toBeInTheDocument();
    expect(within(profile).getByText('33.3%')).toBeInTheDocument();
    const prod = screen.getByText('warehouse (prod)').closest('tr') as HTMLElement;
    expect(within(prod).getByText('1 of 4')).toBeInTheDocument();
    // Freshness had nothing stated: absent, and the note says absent means not measured.
    expect(screen.queryByText('Freshness')).not.toBeInTheDocument();
    expect(screen.getByText(/the rest were not measured/)).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Admin → Settings → Automatic coverage' }),
    ).toHaveAttribute('href', '/admin/settings');
  });

  it('gives a member no link to settings they cannot open', async () => {
    mockGet.mockResolvedValue(BREAKDOWN);
    renderAs('member');

    expect(await screen.findByText('Column profile')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText(/A workspace admin can make row count/)).toBeInTheDocument();
  });

  it('does not point at the settings when only checks they do not govern are noisy', async () => {
    mockGet.mockResolvedValue({
      ...BREAKDOWN,
      false_positives_by_baseline: [
        { key: 'volume', label: 'Row count', stated: 1, false_positive: 0, rate: 0 },
        { key: 'schema', label: 'Schema', stated: 2, false_positive: 1, rate: 50 },
        { key: 'other', label: 'Other checks', stated: 1, false_positive: 1, rate: 100 },
      ],
    });
    renderAs('admin');

    expect(await screen.findByText('Schema')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText(/the rest were not measured/)).toBeInTheDocument();
  });

  it('shows no breakdown, and no caption, when the lists are empty or absent', async () => {
    mockGet.mockResolvedValue({
      ...FIGURES,
      false_positives_by_baseline: [],
      false_positives_by_connection: [],
    });
    const { unmount } = renderAs('admin');
    expect(await screen.findByText('Coverage, across the workspace')).toBeInTheDocument();
    expect(screen.queryByText(/were not measured/)).not.toBeInTheDocument();
    unmount();

    // An API that predates the breakdown: figures stated, no lists at all.
    mockGet.mockResolvedValue(FIGURES);
    renderAs('admin');
    expect(await screen.findByText('Coverage, across the workspace')).toBeInTheDocument();
    expect(screen.queryByText('By automatic check')).not.toBeInTheDocument();
    expect(screen.queryByText(/were not measured/)).not.toBeInTheDocument();
  });
});
