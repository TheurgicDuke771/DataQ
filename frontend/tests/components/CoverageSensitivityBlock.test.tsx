import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from 'antd';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { type AssetSummary, updateAsset } from '../../src/api/assets';
import {
  type AssetCoverageSettings,
  type CoverageSettings,
  type EffectiveCoverage,
  getAssetCoverageSettings,
  getCoverageSettings,
} from '../../src/api/coverage';
import { CoverageSensitivityBlock } from '../../src/components/assets/CoverageSensitivityBlock';

vi.mock('../../src/api/assets', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/assets')>();
  return { ...actual, updateAsset: vi.fn() };
});
vi.mock('../../src/api/coverage', () => ({
  getAssetCoverageSettings: vi.fn(),
  getCoverageSettings: vi.fn(),
}));

const mockUpdate = vi.mocked(updateAsset);
const mockAsset = vi.mocked(getAssetCoverageSettings);
const mockWorkspace = vi.mocked(getCoverageSettings);

const D = { warn: 3, fail: 4, critical: 6, source: 'default' as const };
const W = { warn: 5, fail: 7, critical: 9, source: 'workspace' as const };
const WORKSPACE_EFFECTIVE: EffectiveCoverage = {
  sensitivity: { volume: W, freshness: D, profile: D },
  baseline: { window: 8, min_points: 4, seasonality: true, source: 'default' },
};
const WORKSPACE = { effective: WORKSPACE_EFFECTIVE } as CoverageSettings;
const NO_OVERRIDE: AssetCoverageSettings = { override: null, effective: WORKSPACE_EFFECTIVE };
const OVERRIDDEN: AssetCoverageSettings = {
  override: { sensitivity: { volume: { warn: 8, fail: 9, critical: 10 } } },
  effective: {
    ...WORKSPACE_EFFECTIVE,
    sensitivity: {
      ...WORKSPACE_EFFECTIVE.sensitivity,
      volume: { warn: 8, fail: 9, critical: 10, source: 'asset' },
    },
  },
};

async function open() {
  render(
    <App>
      <CoverageSensitivityBlock assetId="a1" />
    </App>,
  );
  await userEvent.click(await screen.findByText('Sensitivity of this table’s automatic checks'));
}

beforeEach(() => {
  vi.clearAllMocks();
  mockWorkspace.mockResolvedValue(WORKSPACE);
  mockUpdate.mockResolvedValue({} as AssetSummary);
});

describe('CoverageSensitivityBlock (ADR 0047)', () => {
  it('shows what the table follows when it sets nothing itself', async () => {
    mockAsset.mockResolvedValue(NO_OVERRIDE);
    await open();

    expect(screen.getAllByText('Workspace setting').length).toBeGreaterThan(0);
    const volume = within(screen.getByTestId('coverage-volume'));
    // The level below is the workspace's 5, not the built-in 3.
    expect(volume.getByRole('spinbutton', { name: 'Row count warn deviations' })).toHaveValue(
      '5.0',
    );
    expect(
      within(screen.getByTestId('coverage-profile')).getByText('Built-in default'),
    ).toBeInTheDocument();
  });

  it('saves an override for one group and re-reads what the table resolves to', async () => {
    mockAsset.mockResolvedValueOnce(NO_OVERRIDE).mockResolvedValue(OVERRIDDEN);
    await open();

    await userEvent.click(
      screen.getByRole('switch', { name: 'Set for this table: Row count thresholds' }),
    );
    for (const [tier, value] of [
      ['critical', '10'],
      ['fail', '9'],
      ['warn', '8'],
    ]) {
      const input = screen.getByRole('spinbutton', { name: `Row count ${tier} deviations` });
      fireEvent.change(input, { target: { value } });
      fireEvent.blur(input);
    }
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('a1', {
        auto_coverage_config: { sensitivity: { volume: { warn: 8, fail: 9, critical: 10 } } },
      }),
    );
    expect(
      await screen.findByText('Set for this table', { selector: '.ant-tag' }),
    ).toBeInTheDocument();
  });

  it('removing the override sends null', async () => {
    mockAsset.mockResolvedValueOnce(OVERRIDDEN).mockResolvedValue(NO_OVERRIDE);
    await open();

    await userEvent.click(screen.getByRole('button', { name: 'Use the workspace setting' }));

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('a1', { auto_coverage_config: null }),
    );
  });

  it('says so when the settings cannot be loaded', async () => {
    mockAsset.mockRejectedValue(new Error('boom'));
    render(
      <App>
        <CoverageSensitivityBlock assetId="a1" />
      </App>,
    );

    expect(
      await screen.findByText('Could not load this table’s coverage sensitivity'),
    ).toBeInTheDocument();
  });
});
