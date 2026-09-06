import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  getScoringWeights,
  putScoringWeights,
  resetScoringWeights,
  type ScoringWeights,
} from '../../../src/api/admin';
import { ScoringPanel } from '../../../src/pages/admin/ScoringPanel';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  getScoringWeights: vi.fn(),
  putScoringWeights: vi.fn(),
  resetScoringWeights: vi.fn(),
}));

const mockGet = vi.mocked(getScoringWeights);
const mockPut = vi.mocked(putScoringWeights);
const mockReset = vi.mocked(resetScoringWeights);

const DEFAULTS: ScoringWeights = {
  warn: 0.5,
  fail: 1,
  critical: 2,
  is_default: true,
  defaults: { warn: 0.5, fail: 1, critical: 2 },
  updated_by: null,
  updated_at: null,
};

function setWeight(label: string, value: string) {
  const input = screen.getByRole('spinbutton', { name: `${label} weight` });
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
}

beforeEach(() => vi.clearAllMocks());

describe('ScoringPanel', () => {
  it('saves a valid change and shows who made it', async () => {
    mockGet.mockResolvedValue(DEFAULTS);
    mockPut.mockResolvedValue({
      ...DEFAULTS,
      critical: 4,
      is_default: false,
      updated_by: 'ada@example.com',
      updated_at: '2026-09-05T10:00:00Z',
    });
    renderSubPage(<ScoringPanel />);
    expect(await screen.findByText('Defaults')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();

    setWeight('Critical', '4');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith({ warn: 0.5, fail: 1, critical: 4 }));
    expect(await screen.findByText('Customised')).toBeInTheDocument();
    expect(screen.getByText(/by ada@example.com/)).toBeInTheDocument();
  });

  it('refuses a bad ordering before any request', async () => {
    mockGet.mockResolvedValue(DEFAULTS);
    renderSubPage(<ScoringPanel />);
    await screen.findByText('Defaults');
    setWeight('Warn', '3');
    expect(await screen.findByText('Keep the order warn ≤ fail ≤ critical.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(mockPut).not.toHaveBeenCalled();
  });

  it('resets to the defaults through the API', async () => {
    mockGet.mockResolvedValue({ ...DEFAULTS, critical: 5, is_default: false });
    mockReset.mockResolvedValue(DEFAULTS);
    renderSubPage(<ScoringPanel />);
    await screen.findByText('Customised');
    fireEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    await waitFor(() => expect(mockReset).toHaveBeenCalled());
    expect(await screen.findByText('Defaults')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reset to defaults' })).toBeDisabled();
  });

  it('shows the load failure rather than the defaults', async () => {
    mockGet.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<ScoringPanel />);
    expect(await screen.findByText('Could not load the scoring weights')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });
});
