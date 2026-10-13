import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type CoverageSettings,
  type EffectiveCoverage,
  getCoverageSettings,
  putCoverageSettings,
  resetCoverageSettings,
} from '../../../src/api/coverage';
import { CoveragePanel } from '../../../src/pages/admin/CoveragePanel';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/coverage', () => ({
  getCoverageSettings: vi.fn(),
  putCoverageSettings: vi.fn(),
  resetCoverageSettings: vi.fn(),
}));

const mockGet = vi.mocked(getCoverageSettings);
const mockPut = vi.mocked(putCoverageSettings);
const mockReset = vi.mocked(resetCoverageSettings);

const D = { warn: 3, fail: 4, critical: 6, source: 'default' as const };
const BUILT_IN: EffectiveCoverage = {
  sensitivity: { volume: D, freshness: D, profile: D },
  baseline: { window: 8, min_points: 4, seasonality: true, source: 'default' },
};
const DEFAULTS: CoverageSettings = {
  setting: {},
  effective: BUILT_IN,
  defaults: BUILT_IN,
  is_default: true,
  updated_by: null,
  updated_at: null,
};
const CUSTOM: CoverageSettings = {
  setting: { sensitivity: { profile: { warn: 5, fail: 7, critical: 9 } } },
  effective: {
    ...BUILT_IN,
    sensitivity: {
      ...BUILT_IN.sensitivity,
      profile: { warn: 5, fail: 7, critical: 9, source: 'workspace' },
    },
  },
  defaults: BUILT_IN,
  is_default: false,
  updated_by: 'ada@example.com',
  updated_at: '2026-10-13T10:00:00Z',
};

function setNumber(name: string, value: string) {
  const input = screen.getByRole('spinbutton', { name });
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

const save = () => screen.getByRole('button', { name: 'Save' });

beforeEach(() => vi.clearAllMocks());

describe('CoveragePanel', () => {
  it('shows the built-in values read-only until a group is set for the workspace', async () => {
    mockGet.mockResolvedValue(DEFAULTS);
    renderSubPage(<CoveragePanel />);
    expect(await screen.findByText('Defaults')).toBeInTheDocument();

    const profile = within(screen.getByTestId('coverage-profile'));
    expect(profile.getByText('Built-in default')).toBeInTheDocument();
    expect(
      profile.getByRole('spinbutton', { name: 'Column profile warn deviations' }),
    ).toBeDisabled();
    expect(profile.getByRole('spinbutton', { name: 'Column profile warn deviations' })).toHaveValue(
      '3.0',
    );
    expect(save()).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset to defaults' })).toBeDisabled();
  });

  it('saves only the groups that were set, and shows who changed it', async () => {
    mockGet.mockResolvedValue(DEFAULTS);
    mockPut.mockResolvedValue(CUSTOM);
    renderSubPage(<CoveragePanel />);
    await screen.findByText('Defaults');

    await userEvent.click(
      screen.getByRole('switch', { name: 'Set for the workspace: Column profile thresholds' }),
    );
    setNumber('Column profile warn deviations', '5');
    setNumber('Column profile fail deviations', '7');
    setNumber('Column profile critical deviations', '9');
    await userEvent.click(save());

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith({
        sensitivity: { profile: { warn: 5, fail: 7, critical: 9 } },
      }),
    );
    expect(await screen.findByText('Customised')).toBeInTheDocument();
    expect(screen.getByText(/by ada@example.com/)).toBeInTheDocument();
  });

  it('blocks an out-of-order group with the reason', async () => {
    mockGet.mockResolvedValue(DEFAULTS);
    renderSubPage(<CoveragePanel />);
    await screen.findByText('Defaults');

    await userEvent.click(
      screen.getByRole('switch', { name: 'Set for the workspace: Row count thresholds' }),
    );
    setNumber('Row count warn deviations', '5');

    expect(
      await screen.findByText('Row count: keep the order warn ≤ fail ≤ critical.'),
    ).toBeInTheDocument();
    expect(save()).toBeDisabled();
  });

  it('warns before a shorter window is saved, and not for a longer one', async () => {
    mockGet.mockResolvedValue(DEFAULTS);
    renderSubPage(<CoveragePanel />);
    await screen.findByText('Defaults');
    await userEvent.click(screen.getByRole('switch', { name: 'Set for the workspace: history' }));

    setNumber('Runs compared', '14');
    expect(screen.queryByText('A shorter window trims history')).not.toBeInTheDocument();
    setNumber('Runs compared', '5');

    expect(await screen.findByText('A shorter window trims history')).toBeInTheDocument();
    expect(save()).toBeEnabled();
  });

  it('switching the last set group off saves as a reset', async () => {
    mockGet.mockResolvedValue(CUSTOM);
    mockReset.mockResolvedValue(DEFAULTS);
    renderSubPage(<CoveragePanel />);
    await screen.findByText('Customised');

    await userEvent.click(
      screen.getByRole('switch', { name: 'Set for the workspace: Column profile thresholds' }),
    );
    await userEvent.click(save());

    await waitFor(() => expect(mockReset).toHaveBeenCalled());
    expect(mockPut).not.toHaveBeenCalled();
    expect(await screen.findByText('Defaults')).toBeInTheDocument();
  });

  it('reset returns to the defaults', async () => {
    mockGet.mockResolvedValue(CUSTOM);
    mockReset.mockResolvedValue(DEFAULTS);
    renderSubPage(<CoveragePanel />);
    await screen.findByText('Customised');

    await userEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }));

    expect(await screen.findByText('Defaults')).toBeInTheDocument();
  });

  it('keeps what was typed when the save fails', async () => {
    mockGet.mockResolvedValue(DEFAULTS);
    mockPut.mockRejectedValue(new Error('boom'));
    renderSubPage(<CoveragePanel />);
    await screen.findByText('Defaults');
    await userEvent.click(screen.getByRole('switch', { name: 'Set for the workspace: history' }));
    setNumber('Runs compared', '14');

    await userEvent.click(save());

    await waitFor(() => expect(mockPut).toHaveBeenCalled());
    expect(await screen.findByText(/Could not save the coverage settings/)).toBeInTheDocument();
    // antd leaves its spinner in the button's accessible name while it animates out.
    await waitFor(() => expect(screen.getByRole('button', { name: /Save$/ })).toBeEnabled());
    expect(screen.getByRole('spinbutton', { name: 'Runs compared' })).toHaveValue('14');
  });

  it('asks before a reset that shortens the window, and warns when history is switched off', async () => {
    const longWindow: CoverageSettings = {
      ...CUSTOM,
      setting: { baseline: { window: 30, min_points: 4, seasonality: true } },
      effective: {
        ...BUILT_IN,
        baseline: { window: 30, min_points: 4, seasonality: true, source: 'workspace' },
      },
    };
    mockGet.mockResolvedValue(longWindow);
    mockReset.mockResolvedValue(DEFAULTS);
    renderSubPage(<CoveragePanel />);
    await screen.findByText('Customised');

    await userEvent.click(screen.getByRole('switch', { name: 'Set for the workspace: history' }));
    expect(await screen.findByText('A shorter window trims history')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    expect(await screen.findByText('This shortens the window')).toBeInTheDocument();
    expect(mockReset).not.toHaveBeenCalled();
    await userEvent.click(
      within(screen.getByRole('tooltip')).getByRole('button', { name: 'Reset to defaults' }),
    );

    await waitFor(() => expect(mockReset).toHaveBeenCalled());
  });

  it('says so when the settings cannot be loaded', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    renderSubPage(<CoveragePanel />);

    expect(await screen.findByText('Could not load the coverage settings')).toBeInTheDocument();
  });
});
