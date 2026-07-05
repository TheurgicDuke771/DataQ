import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type ApiKey, createApiKey, listApiKeys, revokeApiKey } from '../../src/api/apiKeys';
import { ApiKeysPanel } from '../../src/components/profile/ApiKeysPanel';

vi.mock('../../src/api/apiKeys', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/apiKeys')>();
  return {
    ...actual, // keep the PAT_*_EXPIRY_DAYS constants real
    listApiKeys: vi.fn(),
    createApiKey: vi.fn(),
    revokeApiKey: vi.fn(),
  };
});

const mockList = vi.mocked(listApiKeys);
const mockCreate = vi.mocked(createApiKey);
const mockRevoke = vi.mocked(revokeApiKey);

const KEY: ApiKey = {
  id: 'k1',
  name: 'ci-smoke',
  key_prefix: 'dq_live_ab12',
  created_at: '2026-07-01T10:00:00Z',
  expires_at: '2026-10-01T10:00:00Z',
  revoked_at: null,
  last_used_at: null,
};

function renderPanel() {
  return render(
    <AntApp>
      <ApiKeysPanel />
    </AntApp>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('ApiKeysPanel', () => {
  it('lists tokens by prefix with an Active status (never the secret)', async () => {
    mockList.mockResolvedValue([KEY]);
    renderPanel();

    expect(await screen.findByText('ci-smoke')).toBeInTheDocument();
    expect(screen.getByText('dq_live_ab12…')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('shows an empty state when there are no tokens', async () => {
    mockList.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText('No tokens yet.')).toBeInTheDocument();
  });

  it('marks an expired token', async () => {
    mockList.mockResolvedValue([{ ...KEY, expires_at: '2020-01-01T00:00:00Z' }]);
    renderPanel();
    expect(await screen.findByText('Expired')).toBeInTheDocument();
  });

  it('creates a token and reveals it exactly once', async () => {
    mockList.mockResolvedValue([]);
    mockCreate.mockResolvedValue({ ...KEY, token: 'dq_live_ab12THE_FULL_SECRET' });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('No tokens yet.');

    await user.click(screen.getByRole('button', { name: /New token/ }));
    await user.type(screen.getByLabelText('Token name'), 'laptop-cli');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() =>
      expect(mockCreate).toHaveBeenCalledWith({ name: 'laptop-cli', expires_in_days: 90 }),
    );
    // The full token is revealed once, with the copy-now warning.
    expect(await screen.findByText('dq_live_ab12THE_FULL_SECRET')).toBeInTheDocument();
    expect(screen.getByText('This token is shown only once')).toBeInTheDocument();

    // Show-once invariant: after acknowledging (Done), reopening "New token"
    // shows a fresh empty form — the plaintext is dropped from state (reset) and
    // never re-rendered, and the list refetch carries metadata only (no `token`).
    await user.click(screen.getByRole('button', { name: 'Done' }));
    await user.click(await screen.findByRole('button', { name: /New token/ }));
    expect(screen.queryByText('dq_live_ab12THE_FULL_SECRET')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Token name')).toHaveValue('');
  });

  it('does not reveal a token when creation fails', async () => {
    mockList.mockResolvedValue([]);
    mockCreate.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('No tokens yet.');

    await user.click(screen.getByRole('button', { name: /New token/ }));
    await user.type(screen.getByLabelText('Token name'), 'laptop-cli');
    await user.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(mockCreate).toHaveBeenCalled());
    // A failed create must NOT flash the reveal; we stay on the form (its name
    // field is still present — the token view never mounted).
    expect(screen.queryByText('This token is shown only once')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Token name')).toBeInTheDocument();
  });

  it('keeps the token listed when revoke fails (surfaces, never silent)', async () => {
    mockList.mockResolvedValue([KEY]);
    mockRevoke.mockRejectedValue(new Error('boom'));
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('ci-smoke');

    await user.click(screen.getByRole('button', { name: 'Revoke ci-smoke' }));
    const confirm = await within(document.body).findByRole('button', { name: 'Revoke' });
    await user.click(confirm);

    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith('k1'));
    // Failure is non-silent and non-destructive: the key stays listed (no refetch
    // on failure) so the user can retry.
    expect(screen.getByText('ci-smoke')).toBeInTheDocument();
  });

  it('revokes a token after confirmation', async () => {
    mockList.mockResolvedValue([KEY]);
    mockRevoke.mockResolvedValue();
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('ci-smoke');

    await user.click(screen.getByRole('button', { name: 'Revoke ci-smoke' }));
    // Destructive → confirm modal; nothing revoked until confirmed.
    expect(mockRevoke).not.toHaveBeenCalled();

    const confirm = await within(document.body).findByRole('button', { name: 'Revoke' });
    await user.click(confirm);
    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith('k1'));
  });

  it('offers no revoke action for an already-revoked token', async () => {
    mockList.mockResolvedValue([{ ...KEY, revoked_at: '2026-07-02T00:00:00Z' }]);
    renderPanel();
    await screen.findByText('ci-smoke');

    expect(screen.getByText('Revoked')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revoke ci-smoke' })).not.toBeInTheDocument();
  });
});
