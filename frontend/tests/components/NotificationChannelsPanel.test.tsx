import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createChannel,
  deleteChannel,
  listChannels,
  type NotificationChannel,
  updateChannel,
} from '../../src/api/notificationChannels';
import { NotificationChannelsPanel } from '../../src/components/admin/NotificationChannelsPanel';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/notificationChannels', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/notificationChannels')>();
  return {
    ...actual, // keep CHANNEL_TYPES / CHANNEL_TYPE_LABELS real
    listChannels: vi.fn(),
    createChannel: vi.fn(),
    updateChannel: vi.fn(),
    deleteChannel: vi.fn(),
  };
});

const mockList = vi.mocked(listChannels);
const mockCreate = vi.mocked(createChannel);
const mockUpdate = vi.mocked(updateChannel);
const mockDelete = vi.mocked(deleteChannel);

const TEAMS_CHANNEL: NotificationChannel = {
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
};

const WEBHOOK_CHANNEL: NotificationChannel = {
  id: 'c2',
  name: 'pagerduty',
  type: 'webhook',
  has_webhook: false,
  email_recipients: null,
  webhook_url: 'https://events.example/hook',
  has_hmac_secret: true,
  hmac_secret: null,
  payload_template: null,
  has_payload_template: false,
  auth_header_name: null,
  has_auth_header: false,
};

function renderPanel() {
  return render(
    <AntApp>
      <NotificationChannelsPanel />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('NotificationChannelsPanel', () => {
  it('lists channels with their destination summary, never a secret value', async () => {
    mockList.mockResolvedValue([TEAMS_CHANNEL, WEBHOOK_CHANNEL]);
    renderPanel();

    expect(await screen.findByText('on-call-teams')).toBeInTheDocument();
    expect(screen.getByText('webhook set')).toBeInTheDocument();
    expect(screen.getByText('pagerduty')).toBeInTheDocument();
    expect(screen.getByText('https://events.example/hook')).toBeInTheDocument();
    expect(screen.queryByText('THE_HMAC_SECRET')).not.toBeInTheDocument();
    expect(screen.queryByText('secret-value')).not.toBeInTheDocument();
  });

  it('shows an empty state when there are no channels', async () => {
    mockList.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText('No channels yet.')).toBeInTheDocument();
  });

  it('creates a webhook channel and reveals the HMAC secret exactly once', async () => {
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue({ ...WEBHOOK_CHANNEL, hmac_secret: 'THE_HMAC_SECRET' });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('No channels yet.');

    await user.click(screen.getByRole('button', { name: /New channel/ }));
    await user.type(screen.getByLabelText('Channel name'), 'pagerduty');
    await selectOption(user, 'Generic webhook', { by: 'text' });
    await user.type(
      screen.getByLabelText('Webhook destination URL'),
      'https://events.example/hook',
    );
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({
        name: 'pagerduty',
        type: 'webhook',
        webhook_url: 'https://events.example/hook',
      }),
    );
    expect(await screen.findByText('THE_HMAC_SECRET')).toBeInTheDocument();
    expect(screen.getByText('This HMAC signing key is shown only once')).toBeInTheDocument();

    // Show-once: after Done, reopening New channel shows a fresh form with no trace
    // of the secret left in state.
    await user.click(screen.getByRole('button', { name: 'Done' }));
    await user.click(await screen.findByRole('button', { name: /New channel/ }));
    expect(screen.queryByText('THE_HMAC_SECRET')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Channel name')).toHaveValue('');
  });

  it("switching Type clears the previous type's field so the payload never mixes both (#1878 review)", async () => {
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue(WEBHOOK_CHANNEL);
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('No channels yet.');

    await user.click(screen.getByRole('button', { name: /New channel/ }));
    await user.type(screen.getByLabelText('Channel name'), 'pagerduty');
    // Fill the Teams field (default type), THEN switch to a generic webhook.
    await user.type(screen.getByLabelText('Webhook URL'), 'https://teams.example/hook');
    await selectOption(user, 'Generic webhook', { by: 'text' });
    await user.type(
      screen.getByLabelText('Webhook destination URL'),
      'https://events.example/hook',
    );
    await user.click(screen.getByRole('button', { name: 'Create' }));

    // Only the fields belonging to the FINAL type reach the payload — no stale `webhook`
    // left over from Teams, which the backend would 422 on for a webhook-type channel.
    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({
        name: 'pagerduty',
        type: 'webhook',
        webhook_url: 'https://events.example/hook',
      }),
    );
  });

  it('creates a Teams channel with no secret reveal (non-webhook types mint no HMAC key)', async () => {
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue(TEAMS_CHANNEL);
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('No channels yet.');

    await user.click(screen.getByRole('button', { name: /New channel/ }));
    await user.type(screen.getByLabelText('Channel name'), 'on-call-teams');
    await user.type(screen.getByLabelText('Webhook URL'), 'https://teams.example/hook');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({
        name: 'on-call-teams',
        type: 'teams',
        webhook: 'https://teams.example/hook',
      }),
    );
    // Modal closes straight away — no reveal screen for a type with no HMAC secret.
    await waitFor(() =>
      expect(
        screen.queryByText('This HMAC signing key is shown only once'),
      ).not.toBeInTheDocument(),
    );
  });

  it('edit sends only the changed fields — tri-state PATCH (omitted fields untouched)', async () => {
    mockList.mockResolvedValue([TEAMS_CHANNEL]);
    mockUpdate.mockResolvedValue(TEAMS_CHANNEL);
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('on-call-teams');

    await user.click(screen.getByRole('button', { name: 'Edit on-call-teams' }));
    // Rename only — leave the webhook field blank.
    const nameInput = await screen.findByLabelText('Channel name');
    await user.clear(nameInput);
    await user.type(nameInput, 'renamed-channel');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('c1', { name: 'renamed-channel' }));
    // Crucially: no `webhook` key at all (not even ''), since the admin never typed
    // or asked to clear it.
    const payload = mockUpdate.mock.calls[0]?.[1];
    expect(payload).not.toHaveProperty('webhook');
  });

  it('edit explicitly clears the webhook only when the Clear checkbox is used', async () => {
    mockList.mockResolvedValue([TEAMS_CHANNEL]);
    mockUpdate.mockResolvedValue({ ...TEAMS_CHANNEL, has_webhook: false });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('on-call-teams');

    await user.click(screen.getByRole('button', { name: 'Edit on-call-teams' }));
    await user.click(await screen.findByRole('checkbox', { name: /Clear the stored webhook/ }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('c1', { name: 'on-call-teams', webhook: '' }),
    );
  });

  it('regenerating the HMAC secret on edit reveals the new key once', async () => {
    mockList.mockResolvedValue([WEBHOOK_CHANNEL]);
    mockUpdate.mockResolvedValue({ ...WEBHOOK_CHANNEL, hmac_secret: 'ROTATED_SECRET' });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('pagerduty');

    await user.click(screen.getByRole('button', { name: 'Edit pagerduty' }));
    await user.click(await screen.findByRole('checkbox', { name: /Regenerate HMAC signing key/ }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(mockUpdate).toHaveBeenCalledWith('c2', {
        name: 'pagerduty',
        regenerate_hmac_secret: true,
      }),
    );
    expect(await screen.findByText('ROTATED_SECRET')).toBeInTheDocument();
  });

  it('deletes a channel after confirmation', async () => {
    mockList.mockResolvedValue([TEAMS_CHANNEL]);
    mockDelete.mockResolvedValue();
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('on-call-teams');

    await user.click(screen.getByRole('button', { name: 'Delete on-call-teams' }));
    const confirm = await within(document.body).findByRole('button', { name: 'Delete' });
    await user.click(confirm);

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('c1'));
  }, 15000);

  it('surfaces a clean in-use message from delete rather than a generic failure', async () => {
    mockList.mockResolvedValue([TEAMS_CHANNEL]);
    mockDelete.mockRejectedValue(
      new Error('2 suite(s) still reference this channel — unlink them first'),
    );
    // antd's own ActionButton re-throws a rejecting onOk's error after handling it
    // (ant-design/ant-design#6183, so it reaches a global reporter like Sentry) —
    // useConfirmDelete's "keep the modal open on failure" rethrow triggers that by
    // design. Node's unhandled-rejection detector still flags it even though the
    // app catches and displays it correctly, so it's suppressed for this one
    // expected rejection rather than either weakening the hook's contract or
    // leaving the test suite red for behavior that isn't a bug.
    const swallowKnownAntdRethrow = (err: unknown) => {
      if (!(err instanceof Error) || !err.message.includes('unlink them first')) throw err;
    };
    process.on('unhandledRejection', swallowKnownAntdRethrow);
    try {
      const user = userEvent.setup();
      renderPanel();
      await screen.findByText('on-call-teams');

      await user.click(screen.getByRole('button', { name: 'Delete on-call-teams' }));
      const confirm = await within(document.body).findByRole('button', { name: 'Delete' });
      await user.click(confirm);

      await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('c1'));
      expect(
        await within(document.body).findByText(
          /Delete failed: 2 suite\(s\) still reference this channel — unlink them first/,
        ),
      ).toBeInTheDocument();
      // The channel stays listed — a failed delete must not silently vanish it.
      expect(screen.getByText('on-call-teams')).toBeInTheDocument();
    } finally {
      process.off('unhandledRejection', swallowKnownAntdRethrow);
    }
  }, 15000);

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));
    renderPanel();
    expect(await screen.findByText('Failed to load notification channels')).toBeInTheDocument();
  });
});
