import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  grantShare,
  listShares,
  revokeShare,
  type Share,
  searchUsers,
  updateShare,
} from '../../src/api/shares';
import { useCurrentUser } from '../../src/auth/useCurrentUser';
import { SharePanel } from '../../src/components/suites/SharePanel';
import { selectOption } from '../support/antd';

vi.mock('../../src/auth/useCurrentUser', () => ({ useCurrentUser: vi.fn(() => null) }));
const mockCurrentUser = vi.mocked(useCurrentUser);

vi.mock('../../src/api/shares', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/shares')>();
  return {
    ...actual,
    listShares: vi.fn(),
    grantShare: vi.fn(),
    updateShare: vi.fn(),
    revokeShare: vi.fn(),
    searchUsers: vi.fn(),
  };
});

const mockList = vi.mocked(listShares);
const mockGrant = vi.mocked(grantShare);
const mockUpdate = vi.mocked(updateShare);
const mockRevoke = vi.mocked(revokeShare);
const mockSearch = vi.mocked(searchUsers);

const SHARE_B: Share = {
  suite_id: 's1',
  user_id: 'u-b',
  permission: 'view',
  email: 'b@acme.io',
  display_name: 'Bee',
};

function renderPanel(props: Partial<Parameters<typeof SharePanel>[0]> = {}) {
  return render(
    <AntApp>
      <SharePanel open suiteId="s1" ownerId="u-owner" canManage onClose={vi.fn()} {...props} />
    </AntApp>,
  );
}

// Default: no signed-in identity matches any row (existing tests stay editable);
// clearAllMocks doesn't reset return values, so re-establish the default per test.
beforeEach(() => mockCurrentUser.mockReturnValue(null));
afterEach(() => vi.clearAllMocks());

describe('SharePanel', () => {
  it('lists existing collaborators by name with their level', async () => {
    mockList.mockResolvedValue([SHARE_B]);
    renderPanel();

    expect(await screen.findByText('Bee')).toBeInTheDocument();
    expect(screen.getByText('b@acme.io')).toBeInTheDocument();
  });

  it('shows an empty state when nothing is shared', async () => {
    mockList.mockResolvedValue([]);
    renderPanel();

    expect(await screen.findByText('Not shared with anyone yet.')).toBeInTheDocument();
  });

  it('searches the directory and grants a share, excluding the owner + existing shares', async () => {
    mockList.mockResolvedValue([SHARE_B]);
    mockSearch.mockResolvedValue([
      { id: 'u-c', email: 'carol@acme.io', display_name: 'Carol', role: 'member' as const },
      // The owner + already-shared user must be filtered out of the picker.
      { id: 'u-owner', email: 'owner@acme.io', display_name: 'Owner', role: 'member' as const },
      { id: 'u-b', email: 'b@acme.io', display_name: 'Bee', role: 'member' as const },
    ]);
    mockGrant.mockResolvedValue({
      suite_id: 's1',
      user_id: 'u-c',
      permission: 'view',
      email: 'carol@acme.io',
      display_name: 'Carol',
    });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Bee');

    // The search Select is the first combobox in the DOM (before the perm pickers).
    await user.type(screen.getAllByRole('combobox')[0], 'acme');
    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('acme'));

    // Only Carol is offered (owner + existing share filtered out).
    expect(await screen.findByText('Carol · carol@acme.io')).toBeInTheDocument();
    expect(screen.queryByText('Owner · owner@acme.io')).not.toBeInTheDocument();

    await user.click(screen.getByText('Carol · carol@acme.io'));
    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() =>
      expect(mockGrant).toHaveBeenCalledWith('s1', {
        user_id: 'u-c',
        permission: 'view',
      }),
    );
  });

  it('never grants edit to a workspace viewer, even after a later search', async () => {
    // The regression the review caught, reproduced in full.
    mockList.mockResolvedValue([]);
    mockSearch
      .mockResolvedValueOnce([
        { id: 'u-m', email: 'mia@acme.io', display_name: 'Mia', role: 'member' as const },
      ])
      .mockResolvedValueOnce([
        { id: 'u-v', email: 'vic@acme.io', display_name: 'Vic', role: 'viewer' as const },
      ])
      .mockResolvedValueOnce([
        { id: 'u-z', email: 'zoe@acme.io', display_name: 'Zoe', role: 'member' as const },
      ]);
    mockGrant.mockResolvedValue({
      suite_id: 's1',
      user_id: 'u-v',
      permission: 'view',
      email: 'vic@acme.io',
      display_name: 'Vic',
    });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Not shared with anyone yet.');

    const search = screen.getAllByRole('combobox')[0];

    // 1. Pick a member and raise the level to `edit`.
    await user.type(search, 'mia');
    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('mia'));
    await user.click(await screen.findByText('Mia · mia@acme.io'));
    await selectOption(user, 'Can edit', { index: 1, by: 'text' });

    // 2. Switch to a VIEWER — the level clamps to `view`.
    await user.clear(search);
    await user.type(search, 'vic');
    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('vic'));
    await user.click(await screen.findByText('Vic · vic@acme.io'));

    // 3. Search again for someone else, without changing the selection.
    await user.clear(search);
    await user.type(search, 'zoe');
    await waitFor(() => expect(mockSearch).toHaveBeenCalledWith('zoe'));

    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() =>
      expect(mockGrant).toHaveBeenCalledWith('s1', {
        user_id: 'u-v',
        permission: 'view',
      }),
    );
  });

  it('changes a collaborator permission', async () => {
    mockList.mockResolvedValue([SHARE_B]);
    mockUpdate.mockResolvedValue({ ...SHARE_B, permission: 'edit' });
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Bee');

    // The row's permission Select is the second combobox (the add-picker is first).
    const selects = screen.getAllByRole('combobox');
    await user.click(selects[selects.length - 1]);
    await user.click(await screen.findByText('Can edit'));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith('s1', 'u-b', 'edit'));
  });

  it('revokes a collaborator', async () => {
    mockList.mockResolvedValue([SHARE_B]);
    mockRevoke.mockResolvedValue();
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('Bee');

    await user.click(screen.getByRole('button', { name: 'Remove b@acme.io' }));

    await waitFor(() => expect(mockRevoke).toHaveBeenCalledWith('s1', 'u-b'));
  });

  it("locks the signed-in user's own row so they can't self-revoke/-downgrade (#240)", async () => {
    // MSAL username (UPN) matches the share email case-insensitively → self row.
    mockCurrentUser.mockReturnValue({
      name: 'Bee',
      username: 'B@Acme.io',
      homeAccountId: 'h',
      isDev: false,
    });
    mockList.mockResolvedValue([SHARE_B]);
    renderPanel(); // canManage — but the self row must still be read-only
    await screen.findByText('Bee');

    // No remove button + no editable permission select on the self row; instead a
    // locked "· You" tag. (The add-collaborator picker's comboboxes still exist.)
    expect(screen.queryByRole('button', { name: 'Remove b@acme.io' })).not.toBeInTheDocument();
    expect(screen.getByText(/·\s*You/)).toBeInTheDocument();
  });

  it('hides management controls for a non-admin (read-only list)', async () => {
    mockList.mockResolvedValue([SHARE_B]);
    renderPanel({ canManage: false });
    await screen.findByText('Bee');

    // No add picker, no remove button — just the permission shown as a tag.
    expect(screen.queryByRole('button', { name: 'Add' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove b@acme.io' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.getByText('view')).toBeInTheDocument();
  });
});
