import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type CoverageFigures, getCoverageFigures } from '../../src/api/dashboard';
import { CoveragePanel } from '../../src/components/dashboard/CoveragePanel';

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

afterEach(() => vi.clearAllMocks());

describe('CoveragePanel', () => {
  it('shows both figures with their denominators', async () => {
    mockGet.mockResolvedValue(FIGURES);
    render(<CoveragePanel />);

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
    render(<CoveragePanel />);

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
    render(<CoveragePanel />);

    expect(await screen.findByText('No assets in the inventory yet.')).toBeInTheDocument();
    expect(
      screen.getByText('Not measured: no incident on an automatic suite was resolved by a person.'),
    ).toBeInTheDocument();
  });

  it('degrades to a line of text when the figures cannot be loaded', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    render(<CoveragePanel />);
    expect(
      await screen.findByText('Coverage figures are unavailable right now.'),
    ).toBeInTheDocument();
  });
});
