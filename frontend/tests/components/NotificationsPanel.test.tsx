import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  getNotifications,
  putNotifications,
  type SuiteNotification,
} from '../../src/api/notifications';
import {
  linkSuiteChannel,
  listChannels,
  listSuiteChannels,
  type NotificationChannel,
  unlinkSuiteChannel,
} from '../../src/api/notificationChannels';
import { NotificationsPanel } from '../../src/components/suites/NotificationsPanel';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/notifications', () => ({
  getNotifications: vi.fn(),
  putNotifications: vi.fn(),
  deleteNotifications: vi.fn(),
}));

vi.mock('../../src/api/notificationChannels', () => ({
  listChannels: vi.fn(),
  listSuiteChannels: vi.fn(),
  linkSuiteChannel: vi.fn(),
  unlinkSuiteChannel: vi.fn(),
}));

const mockGet = vi.mocked(getNotifications);
const mockPut = vi.mocked(putNotifications);
const mockListChannels = vi.mocked(listChannels);
const mockListSuiteChannels = vi.mocked(listSuiteChannels);
const mockLink = vi.mocked(linkSuiteChannel);
const mockUnlink = vi.mocked(unlinkSuiteChannel);

const CONFIG: SuiteNotification = {
  configured: true,
  enabled: true,
  alert_on: 'fail',
  has_webhook: false,
  has_slack_webhook: false,
  email_recipients: null,
};

function channel(overrides: Partial<NotificationChannel> = {}): NotificationChannel {
  return {
    id: 'c1',
    name: 'on-call-teams',
    type: 'teams',
    has_webhook: true,
    email_recipients: null,
    webhook_url: null,
    has_hmac_secret: false,
    hmac_secret: null,
    payload_template: null,
    has_payload_template: false,
    auth_header_name: null,
    has_auth_header: false,
    ...overrides,
  };
}

function renderPanel(props: Partial<Parameters<typeof NotificationsPanel>[0]> = {}) {
  return render(
    <AntApp>
      <NotificationsPanel suiteId="s1" canManage {...props} />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

// Every NotificationsPanel test mounts the ChannelPicker too — default it empty so
// tests that don't care about channels aren't left hanging on an unresolved fetch.
mockListChannels.mockResolvedValue([]);
mockListSuiteChannels.mockResolvedValue([]);

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

  it('clearing a webhook does not persist an unsaved enabled toggle', async () => {
    // #639 review: a "Clear" must send the server-known enabled/alert_on, not an
    // unsaved switch edit — else it silently disables alerting for the suite.
    mockGet.mockResolvedValue({ ...CONFIG, enabled: true, has_webhook: true });
    mockPut.mockResolvedValue({ ...CONFIG, has_webhook: false });
    renderPanel();
    await screen.findByText('set');

    // Toggle Enabled OFF but do NOT save, then clear the Teams webhook.
    await userEvent.click(screen.getByLabelText('Enable notifications'));
    await userEvent.click(screen.getByRole('button', { name: 'Clear Teams' }));

    await waitFor(() =>
      // enabled stays true (the loaded value), not the unsaved false.
      expect(mockPut).toHaveBeenCalledWith('s1', { enabled: true, alert_on: 'fail', webhook: '' }),
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

describe('NotificationsPanel channel picker', () => {
  it("pre-selects the suite's already-linked channels", async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockListChannels.mockResolvedValue([
      channel({ id: 'c1', name: 'on-call' }),
      channel({ id: 'c2', name: 'pager' }),
    ]);
    mockListSuiteChannels.mockResolvedValue([channel({ id: 'c1', name: 'on-call' })]);
    renderPanel();

    expect(await screen.findByText('on-call (teams)')).toBeInTheDocument();
    expect(screen.queryByText('pager (teams)')).not.toBeInTheDocument();
  });

  it('selecting a new channel calls PUT for the suite/channel pair', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockListChannels.mockResolvedValue([
      channel({ id: 'c1', name: 'on-call' }),
      channel({ id: 'c2', name: 'pager' }),
    ]);
    mockListSuiteChannels.mockResolvedValue([]);
    mockLink.mockResolvedValue();
    const user = userEvent.setup();
    renderPanel();
    await screen.findByLabelText('Linked channels');

    await selectOption(user, 'pager (teams)', { index: 1, by: 'text' });

    await waitFor(() => expect(mockLink).toHaveBeenCalledWith('s1', 'c2'));
    expect(mockUnlink).not.toHaveBeenCalled();
  });

  it('deselecting a linked channel calls DELETE for the suite/channel pair', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockListChannels.mockResolvedValue([channel({ id: 'c1', name: 'on-call' })]);
    mockListSuiteChannels.mockResolvedValue([channel({ id: 'c1', name: 'on-call' })]);
    mockUnlink.mockResolvedValue();
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('on-call (teams)');

    // Clicking an already-selected option in an antd multi-select toggles it off.
    await selectOption(user, 'on-call (teams)', { index: 1, by: 'text' });

    await waitFor(() => expect(mockUnlink).toHaveBeenCalledWith('s1', 'c1'));
    expect(mockLink).not.toHaveBeenCalled();
  });

  it('renders read-only for a viewer, with no mutation handlers wired up', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockListChannels.mockResolvedValue([channel({ id: 'c1', name: 'on-call' })]);
    mockListSuiteChannels.mockResolvedValue([channel({ id: 'c1', name: 'on-call' })]);
    renderPanel({ canManage: false });

    expect(await screen.findByText('on-call')).toBeInTheDocument();
    // No editable Select for a viewer — just the plain tag list.
    expect(screen.queryByLabelText('Linked channels')).not.toBeInTheDocument();
    expect(mockLink).not.toHaveBeenCalled();
    expect(mockUnlink).not.toHaveBeenCalled();
  });

  it('shows a plain empty state for a viewer with nothing linked', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockListChannels.mockResolvedValue([]);
    mockListSuiteChannels.mockResolvedValue([]);
    renderPanel({ canManage: false });

    expect(await screen.findByText('No channels linked.')).toBeInTheDocument();
  });

  it('never fetches the full workspace channel list for a viewer (#1879)', async () => {
    // A viewer only ever sees the already-linked tags (from listSuiteChannels) — the
    // full listChannels fetch exists solely to populate the editable Select's options,
    // which a viewer never renders.
    mockGet.mockResolvedValue(CONFIG);
    mockListSuiteChannels.mockResolvedValue([channel({ id: 'c1', name: 'on-call' })]);
    renderPanel({ canManage: false });

    expect(await screen.findByText('on-call')).toBeInTheDocument();
    expect(mockListChannels).not.toHaveBeenCalled();
  });
});
