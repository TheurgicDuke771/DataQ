import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { listAdminAccess, listAdminUsers } from '../../../src/api/admin';
import { AdminMembers } from '../../../src/pages/admin/AdminMembers';
import { ACCESS, USER, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  listAdminUsers: vi.fn(),
  listAdminAccess: vi.fn(),
  setAdminUserRole: vi.fn(),
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
});
