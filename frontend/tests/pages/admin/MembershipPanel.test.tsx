import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MembershipView, WorkspaceMember } from '../../../src/api/admin';
import {
  addWorkspaceMember,
  confirmWorkspaceMember,
  listWorkspaceMembers,
  removeWorkspaceMember,
} from '../../../src/api/admin';
import { MembershipPanel } from '../../../src/pages/admin/MembershipPanel';
import { renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  listWorkspaceMembers: vi.fn(),
  addWorkspaceMember: vi.fn(),
  removeWorkspaceMember: vi.fn(),
  confirmWorkspaceMember: vi.fn(),
  WORKSPACE_ROLES: ['admin', 'member', 'viewer'],
}));

const mockList = vi.mocked(listWorkspaceMembers);
const mockAdd = vi.mocked(addWorkspaceMember);
const mockRemove = vi.mocked(removeWorkspaceMember);
const mockConfirm = vi.mocked(confirmWorkspaceMember);

function member(overrides: Partial<WorkspaceMember> = {}): WorkspaceMember {
  return {
    id: 'm1',
    email: 'ada@x.io',
    initial_role: 'member',
    source: 'admin',
    invited_by_email: 'admin@dataq.io',
    created_at: '2026-09-01T00:00:00Z',
    user_id: 'u1',
    stored_role: 'member',
    status: 'active',
    ...overrides,
  };
}

function view(overrides: Partial<MembershipView> = {}): MembershipView {
  return { enforcement_active: true, unmanaged_user_count: 0, members: [member()], ...overrides };
}

beforeEach(() => mockList.mockResolvedValue(view()));
afterEach(() => vi.clearAllMocks());

describe('MembershipPanel', () => {
  it('lists members with their source and status', async () => {
    mockList.mockResolvedValue(
      view({ members: [member(), member({ id: 'm2', email: 'pending@x.io', status: 'pending' })] }),
    );
    renderSubPage(<MembershipPanel />);

    expect(await screen.findByText('ada@x.io')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('pending first sign-in')).toBeInTheDocument();
  });

  it('says enforcement is off rather than letting an empty list read as "nobody"', async () => {
    mockList.mockResolvedValue(
      view({ enforcement_active: false, unmanaged_user_count: 4, members: [] }),
    );
    renderSubPage(<MembershipPanel />);

    expect(await screen.findByText('Membership is not enforced yet')).toBeInTheDocument();
    expect(screen.getByText(/4 existing users/)).toBeInTheDocument();
  });

  it('banners imported rows for review with per-row verbs', async () => {
    mockList.mockResolvedValue(view({ members: [member({ source: 'auto_import' })] }));
    renderSubPage(<MembershipPanel />);

    expect(await screen.findByText('Review 1 imported member')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeInTheDocument();
  });

  it('confirms an imported member and reloads', async () => {
    mockList.mockResolvedValue(view({ members: [member({ source: 'auto_import' })] }));
    mockConfirm.mockResolvedValue(member());
    renderSubPage(<MembershipPanel />);

    await userEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
    await waitFor(() => expect(mockConfirm).toHaveBeenCalledWith('m1'));
    // Reloaded, rather than patching the row in place from the response.
    await waitFor(() => expect(mockList.mock.calls.length).toBeGreaterThan(1));
  });

  it('removes a member after the confirmation step', async () => {
    mockRemove.mockResolvedValue(undefined);
    renderSubPage(<MembershipPanel />);

    await userEvent.click((await screen.findAllByRole('button', { name: 'Remove' }))[0]);
    await userEvent.click(await screen.findByRole('button', { name: 'Remove member' }));
    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith('m1'));
  });

  it('shows a failed mutation instead of silently doing nothing', async () => {
    mockConfirm.mockRejectedValue(new Error('nope'));
    mockList.mockResolvedValue(view({ members: [member({ source: 'auto_import' })] }));
    renderSubPage(<MembershipPanel />);

    await userEvent.click(await screen.findByRole('button', { name: 'Confirm' }));
    expect(await screen.findByText(/nope/)).toBeInTheDocument();
  });

  it('surfaces a load error rather than an empty table', async () => {
    mockList.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<MembershipPanel />);
    expect(await screen.findByText('Failed to load workspace members')).toBeInTheDocument();
  });

  it('warns that the first add turns enforcement on, with the real count', async () => {
    mockList.mockResolvedValue(
      view({ enforcement_active: false, unmanaged_user_count: 3, members: [] }),
    );
    renderSubPage(<MembershipPanel />);

    await userEvent.click(await screen.findByRole('button', { name: 'Add member' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('This turns membership enforcement on')).toBeInTheDocument();
    expect(dialog.getByText(/imports your 3 existing users/)).toBeInTheDocument();
  });

  it('adds a member with the chosen initial role', async () => {
    mockAdd.mockResolvedValue({
      member: member({ id: 'm2', email: 'new@x.io' }),
      auto_imported_count: 0,
      enforcement_active: true,
    });
    renderSubPage(<MembershipPanel />);

    await userEvent.click(await screen.findByRole('button', { name: 'Add member' }));
    await userEvent.type(screen.getByPlaceholderText('person@example.com'), 'new@x.io');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(mockAdd).toHaveBeenCalledWith('new@x.io', 'member'));
  });
});
