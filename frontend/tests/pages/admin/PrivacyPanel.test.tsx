import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getPrivacySettings, type PrivacySettings, putPrivacySettings } from '../../../src/api/admin';
import { PrivacyPanel } from '../../../src/pages/admin/PrivacyPanel';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({ getPrivacySettings: vi.fn(), putPrivacySettings: vi.fn() }));

const mockGet = vi.mocked(getPrivacySettings);
const mockPut = vi.mocked(putPrivacySettings);

const OFF: PrivacySettings = {
  effective: false,
  stored: false,
  source: 'off',
  env_forced: false,
  updated_by: null,
  updated_at: null,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PrivacyPanel', () => {
  it('renders off and turns on through the API', async () => {
    mockGet.mockResolvedValue(OFF);
    mockPut.mockResolvedValue({ ...OFF, effective: true, stored: true, source: 'db', updated_by: 'ada@example.com', updated_at: '2026-09-05T10:00:00Z' });
    renderSubPage(<PrivacyPanel />);
    const toggle = await screen.findByRole('switch', { name: 'Zero-sample mode' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith(true));
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Zero-sample mode' })).toBeChecked());
    expect(screen.getByText(/by ada@example.com/)).toBeInTheDocument();
  });

  it('is pinned and disabled when the environment forces it on', async () => {
    mockGet.mockResolvedValue({ ...OFF, effective: true, source: 'env', env_forced: true });
    renderSubPage(<PrivacyPanel />);
    const toggle = await screen.findByRole('switch', { name: 'Zero-sample mode' });
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(screen.getByText('Forced on by PRIVACY_ZERO_SAMPLE_MODE')).toBeInTheDocument();
    expect(mockPut).not.toHaveBeenCalled();
  });

  it('shows the load failure rather than a default state', async () => {
    mockGet.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<PrivacyPanel />);
    expect(await screen.findByText('Could not load the privacy settings')).toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  });
});
