import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  getNotifications,
  putNotifications,
  type SuiteNotification,
} from '../../src/api/notifications';
import { NotificationsPanel } from '../../src/components/suites/NotificationsPanel';

vi.mock('../../src/api/notifications', () => ({
  getNotifications: vi.fn(),
  putNotifications: vi.fn(),
  deleteNotifications: vi.fn(),
}));

const mockGet = vi.mocked(getNotifications);
const mockPut = vi.mocked(putNotifications);

const CONFIG: SuiteNotification = {
  configured: true,
  enabled: true,
  alert_on: 'fail',
  has_webhook: false,
};

function renderPanel(props: Partial<Parameters<typeof NotificationsPanel>[0]> = {}) {
  return render(
    <AntApp>
      <NotificationsPanel suiteId="s1" canManage {...props} />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('NotificationsPanel', () => {
  it('loads and shows the current config', async () => {
    mockGet.mockResolvedValue(CONFIG);
    renderPanel();
    expect(await screen.findByText('Send alerts for this suite')).toBeInTheDocument();
    expect(screen.getByText('not set')).toBeInTheDocument(); // webhook status
  });

  it('saves the threshold without resending an unchanged webhook', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockResolvedValue({ ...CONFIG, alert_on: 'always' });
    renderPanel();
    await screen.findByText('Send alerts for this suite');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    // No webhook typed → the payload omits `webhook` (leaves the stored one).
    expect(mockPut).toHaveBeenCalledWith('s1', { enabled: true, alert_on: 'fail' });
  });

  it('sends a typed webhook on save', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockResolvedValue({ ...CONFIG, has_webhook: true });
    renderPanel();
    await screen.findByText('Send alerts for this suite');

    await userEvent.type(screen.getByLabelText('Teams webhook URL'), 'https://teams.example/hook');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('s1', {
        enabled: true,
        alert_on: 'fail',
        webhook: 'https://teams.example/hook',
      }),
    );
  });

  it('clears the webhook when one is set', async () => {
    mockGet.mockResolvedValue({ ...CONFIG, has_webhook: true });
    mockPut.mockResolvedValue({ ...CONFIG, has_webhook: false });
    renderPanel();
    await screen.findByText('set'); // webhook status tag

    await userEvent.click(screen.getByRole('button', { name: 'Clear webhook' }));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('s1', { enabled: true, alert_on: 'fail', webhook: '' }),
    );
  });

  it('hides the controls for a viewer', async () => {
    mockGet.mockResolvedValue(CONFIG);
    renderPanel({ canManage: false });
    await screen.findByText('Send alerts for this suite');
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    renderPanel();
    expect(await screen.findByText('Failed to load notifications')).toBeInTheDocument();
  });
});
