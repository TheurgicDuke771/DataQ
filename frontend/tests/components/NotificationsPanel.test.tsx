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
  has_slack_webhook: false,
  email_recipients: null,
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
    // Both webhook status tags start "not set".
    expect(screen.getAllByText('not set').length).toBe(2);
  });

  it('saves the threshold without resending unchanged webhooks (email is WYSIWYG)', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockResolvedValue({ ...CONFIG, alert_on: 'always' });
    renderPanel();
    await screen.findByText('Send alerts for this suite');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    // No webhook typed → the payload omits webhook/slack_webhook (leaves them); email
    // is returned+editable so it's always sent (here empty → clears / stays null).
    expect(mockPut).toHaveBeenCalledWith('s1', {
      enabled: true,
      alert_on: 'fail',
      email_recipients: '',
    });
  });

  it('sends a typed Teams webhook on save', async () => {
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
        email_recipients: '',
      }),
    );
  });

  it('sends a typed Slack webhook on save', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockResolvedValue({ ...CONFIG, has_slack_webhook: true });
    renderPanel();
    await screen.findByText('Send alerts for this suite');

    await userEvent.type(
      screen.getByLabelText('Slack webhook URL'),
      'https://hooks.slack.com/services/x',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('s1', {
        enabled: true,
        alert_on: 'fail',
        slack_webhook: 'https://hooks.slack.com/services/x',
        email_recipients: '',
      }),
    );
  });

  it('sends edited email recipients on save', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockResolvedValue({ ...CONFIG, email_recipients: 'a@x.io' });
    renderPanel();
    await screen.findByText('Send alerts for this suite');

    await userEvent.type(screen.getByLabelText('Email recipients'), 'a@x.io, b@y.io');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('s1', {
        enabled: true,
        alert_on: 'fail',
        email_recipients: 'a@x.io, b@y.io',
      }),
    );
  });

  it('prefills email recipients from the config', async () => {
    mockGet.mockResolvedValue({ ...CONFIG, email_recipients: 'team@x.io' });
    renderPanel();
    await screen.findByText('Send alerts for this suite');
    expect(screen.getByLabelText('Email recipients')).toHaveValue('team@x.io');
  });

  it('clears the Teams webhook when one is set', async () => {
    mockGet.mockResolvedValue({ ...CONFIG, has_webhook: true });
    mockPut.mockResolvedValue({ ...CONFIG, has_webhook: false });
    renderPanel();
    await screen.findByText('set'); // Teams status tag

    await userEvent.click(screen.getByRole('button', { name: 'Clear Teams' }));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('s1', { enabled: true, alert_on: 'fail', webhook: '' }),
    );
  });

  it('clears the Slack webhook when one is set', async () => {
    mockGet.mockResolvedValue({ ...CONFIG, has_slack_webhook: true });
    mockPut.mockResolvedValue({ ...CONFIG, has_slack_webhook: false });
    renderPanel();
    await screen.findByText('set'); // Slack status tag

    await userEvent.click(screen.getByRole('button', { name: 'Clear Slack' }));

    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('s1', {
        enabled: true,
        alert_on: 'fail',
        slack_webhook: '',
      }),
    );
  });

  it('surfaces an error and does not clear typed input when save fails', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockRejectedValue(new Error('boom'));
    renderPanel();
    await screen.findByText('Send alerts for this suite');

    await userEvent.type(screen.getByLabelText('Teams webhook URL'), 'https://teams.example/hook');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockPut).toHaveBeenCalled());
    // On failure the typed webhook is kept (not reset) so the user can retry.
    expect(screen.getByLabelText('Teams webhook URL')).toHaveValue('https://teams.example/hook');
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
