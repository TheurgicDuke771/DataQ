import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  listAdminAccess,
  listAdminUsers,
  listWorkspaceMembers,
  offboardUser,
  previewOffboarding,
} from '../../../src/api/admin';
import { AdminMembers } from '../../../src/pages/admin/AdminMembers';
import { ACCESS, USER, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  listAdminUsers: vi.fn(),
  listAdminAccess: vi.fn(),
  setAdminUserRole: vi.fn(),
  // The membership panel this page now embeds reads from the same module.
  listWorkspaceMembers: vi.fn(async () => ({
    enforcement_active: false,
    enforced: false,
    enforced_reason: 'no members have been added yet',
    unmanaged_user_count: 0,
    env_allowed_domains: [],
    members: [],
  })),
  addWorkspaceMember: vi.fn(),
  removeWorkspaceMember: vi.fn(),
  confirmWorkspaceMember: vi.fn(),
  // The row action this page now renders reads from the same module.
  previewOffboarding: vi.fn(),
  offboardUser: vi.fn(),
  // A VALUE export, not a function — the role editor iterates it to build its options.
  WORKSPACE_ROLES: ['admin', 'member', 'viewer'],
}));

const mockUsers = vi.mocked(listAdminUsers);
const mockAccess = vi.mocked(listAdminAccess);

beforeEach(() => {
  mockUsers.mockResolvedValue([USER]);
  mockAccess.mockResolvedValue(ACCESS);
});
afterEach(() => vi.clearAllMocks());

describe('AdminMembers', () => {
  it('lists members with their role editor and every access grant', async () => {
    renderSubPage(<AdminMembers />);
    expect(await screen.findByText('bob@x.io')).toBeInTheDocument();
    expect(screen.getByText('owner')).toBeInTheDocument();
    expect(screen.getByText('edit')).toBeInTheDocument();
    expect((await screen.findAllByText('Finance DQ')).length).toBeGreaterThan(0);
  });

  it('surfaces a load error per table rather than taking the page down', async () => {
    mockUsers.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<AdminMembers />);
    expect(await screen.findByText('Failed to load members')).toBeInTheDocument();
    // The sibling table still renders its data.
    expect(screen.getByText('owner')).toBeInTheDocument();
  });

  it('reloads the membership panel and drops the re-role overlay after an offboarding', async () => {
    vi.mocked(previewOffboarding).mockResolvedValue({
      user_id: USER.id,
      email: USER.email,
      display_name: null,
      role: 'member',
      is_self: false,
      is_last_admin: false,
      membership_state: 'member',
      membership_id: 'm1',
      membership_note: null,
      still_admitted_by: [],
      owned_suites: [],
      open_api_key_count: 0,
      live_session_count: 0,
    });
    vi.mocked(offboardUser).mockResolvedValue({
      user_id: USER.id,
      email: USER.email,
      new_owner_user_id: null,
      transferred_suite_ids: [],
      api_keys_revoked: 0,
      sessions_revoked: 0,
      membership_removed: true,
      still_admitted_by: [],
      skipped: [],
    });
    renderSubPage(<AdminMembers />);
    await screen.findByText(USER.email);
    await waitFor(() => expect(listWorkspaceMembers).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: 'Offboard' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('Confirm email address'), USER.email);
    const ok = within(dialog).getByRole('button', { name: /^Offboard/ });
    await waitFor(() => expect(ok).toBeEnabled());
    fireEvent.click(ok);

    await waitFor(() => expect(offboardUser).toHaveBeenCalled());
    // Both lists refetch — the membership panel is remounted, not left stale.
    await waitFor(() => expect(listWorkspaceMembers).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockUsers).toHaveBeenCalledTimes(2));
  });
});
