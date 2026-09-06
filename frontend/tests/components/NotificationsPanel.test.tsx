import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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
    <MemoryRouter>
      <AntApp>
        <NotificationsPanel suiteId="s1" canManage {...props} />
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

// Every NotificationsPanel test mounts the ChannelPicker too — default it empty so
// tests that don't care about channels aren't left hanging on an unresolved fetch.
mockListChannels.mockResolvedValue([]);
mockListSuiteChannels.mockResolvedValue([]);

describe('NotificationsPanel', () => {
  it('loads and shows the current config, with no inline destination inputs', async () => {
    mockGet.mockResolvedValue(CONFIG);
    renderPanel();
    expect(await screen.findByText('Send alerts for this suite')).toBeInTheDocument();
    // Destinations are channels an admin configured — never typed here (#1926).
    expect(screen.queryByLabelText('Teams webhook URL')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Email recipients')).not.toBeInTheDocument();
    expect(screen.queryByText('Legacy inline destinations')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Notification channels/ })).toHaveAttribute(
      'href',
      '/admin/settings',
    );
  });

  it('saves only the switch and the threshold', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockResolvedValue({ ...CONFIG, alert_on: 'always' });
    renderPanel();
    await screen.findByText('Send alerts for this suite');

    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    expect(mockPut).toHaveBeenCalledWith('s1', { enabled: true, alert_on: 'fail' });
  });

  it('shows a legacy card for each inline destination still set, clear-only', async () => {
    mockGet.mockResolvedValue({
      ...CONFIG,
      has_webhook: true,
      has_slack_webhook: false,
      email_recipients: 'team@x.io',
    });
    mockPut.mockResolvedValue({ ...CONFIG, has_webhook: false, email_recipients: 'team@x.io' });
    renderPanel();
    expect(await screen.findByText('Legacy inline destinations')).toBeInTheDocument();
    expect(screen.getByText('Teams webhook')).toBeInTheDocument();
    expect(screen.getByText('Email recipients (team@x.io)')).toBeInTheDocument();
    expect(screen.queryByText('Slack webhook')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByRole('button', { name: 'Clear' })[0]);
    await waitFor(() =>
      expect(mockPut).toHaveBeenCalledWith('s1', { enabled: true, alert_on: 'fail', webhook: '' }),
    );
  });

  it('clearing a legacy destination does not persist an unsaved enabled toggle', async () => {
    mockGet.mockResolvedValue({ ...CONFIG, has_slack_webhook: true });
    mockPut.mockResolvedValue({ ...CONFIG });
    renderPanel();
    await screen.findByText('Legacy inline destinations');
    await userEvent.click(screen.getByRole('switch', { name: 'Enable notifications' }));

    await userEvent.click(screen.getByRole('button', { name: 'Clear' }));

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    // The loaded `enabled: true`, not the un-saved toggle.
    expect(mockPut).toHaveBeenCalledWith('s1', {
      enabled: true,
      alert_on: 'fail',
      slack_webhook: '',
    });
  });

  it('surfaces a save failure', async () => {
    mockGet.mockResolvedValue(CONFIG);
    mockPut.mockRejectedValue(new Error('boom'));
    renderPanel();
    await screen.findByText('Send alerts for this suite');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText(/Save failed: boom/)).toBeInTheDocument();
  });

  it('hides the controls for a viewer', async () => {
    mockGet.mockResolvedValue({ ...CONFIG, has_webhook: true });
    renderPanel({ canManage: false });
    await screen.findByText('Send alerts for this suite');
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Enable notifications' })).toBeDisabled();
    // The legacy card still says what is set, but offers no Clear.
    expect(screen.getByText('Legacy inline destinations')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockGet.mockRejectedValue(new Error('nope'));
    renderPanel();
    expect(await screen.findByText('Failed to load notifications')).toBeInTheDocument();
  });
});

describe('NotificationsPanel channel picker', () => {
  it('fetches listChannels and listSuiteChannels in parallel for a manager (#1879 review)', async () => {
    // An earlier version of the #1879 fix gated `ManagedChannelPicker`'s mount on
    // `linked` having already resolved, which serialized the two GETs. Assert
    // listChannels is called WITHOUT waiting on listSuiteChannels to resolve first.
    mockGet.mockResolvedValue(CONFIG);
    let resolveLinked: (v: NotificationChannel[]) => void = () => {};
    mockListSuiteChannels.mockReturnValue(
      new Promise((resolve) => {
        resolveLinked = resolve;
      }),
    );
    mockListChannels.mockResolvedValue([channel({ id: 'c1', name: 'on-call' })]);
    renderPanel();

    await waitFor(() => expect(mockListChannels).toHaveBeenCalled());
    // listSuiteChannels is still unresolved at this point — proving listChannels
    // didn't wait for it.
    expect(mockListSuiteChannels).toHaveBeenCalled();
    resolveLinked([]);
  });

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
