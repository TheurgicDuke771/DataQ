import { App as AntApp } from 'antd';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type AdminAccess,
  type AdminSuite,
  type AdminUser,
  listAdminAccess,
  listAdminSuites,
  listAdminUsers,
} from '../../src/api/admin';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Admin } from '../../src/pages/Admin';

vi.mock('../../src/api/admin', () => ({
  listAdminSuites: vi.fn(),
  listAdminUsers: vi.fn(),
  listAdminAccess: vi.fn(),
}));

const mockSuites = vi.mocked(listAdminSuites);
const mockUsers = vi.mocked(listAdminUsers);
const mockAccess = vi.mocked(listAdminAccess);

const adminMe: AsyncState<MeResponse> = {
  status: 'ok',
  data: {
    id: 'u-1',
    aad_object_id: 'oid-1',
    email: 'admin@dataq.io',
    display_name: 'Ada Admin',
    last_seen_at: null,
    is_workspace_admin: true,
  },
};

const SUITE: AdminSuite = {
  id: 's1',
  name: 'Finance DQ',
  connection_name: 'sf-prod',
  connection_type: 'snowflake',
  env: 'prod',
  owner_id: 'o1',
  owner_email: 'olive@x.io',
  owner_name: 'Olive Owner',
  check_count: 7,
  share_count: 2,
  created_at: '2026-06-10T10:00:00Z',
  updated_at: '2026-06-10T10:00:00Z',
};
const USER: AdminUser = {
  id: 'u9',
  email: 'bob@x.io',
  display_name: null,
  last_seen_at: null,
  created_at: '2026-06-01T00:00:00Z',
  owned_suite_count: 3,
  shared_suite_count: 1,
};
const ACCESS: AdminAccess[] = [
  {
    suite_id: 's1',
    suite_name: 'Finance DQ',
    user_id: 'o1',
    user_email: 'olive@x.io',
    user_name: 'Olive Owner',
    permission: 'owner',
  },
  {
    suite_id: 's1',
    suite_name: 'Finance DQ',
    user_id: 'e1',
    user_email: 'ed@x.io',
    user_name: null,
    permission: 'edit',
  },
];

function renderAdmin(me: AsyncState<MeResponse>) {
  return render(
    <MemoryRouter>
      <AntApp>
        <MeContext.Provider value={me}>
          <Admin />
        </MeContext.Provider>
      </AntApp>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockSuites.mockResolvedValue([SUITE]);
  mockUsers.mockResolvedValue([USER]);
  mockAccess.mockResolvedValue(ACCESS);
});
afterEach(() => vi.clearAllMocks());

describe('Admin', () => {
  it('shows the Forbidden page for a non-admin and fetches nothing', () => {
    renderAdmin({ ...adminMe, data: { ...adminMe.data, is_workspace_admin: false } });
    expect(screen.getByText('403 — Forbidden')).toBeInTheDocument();
    expect(mockSuites).not.toHaveBeenCalled();
  });

  it('renders KPI cards + all suites + members + access in one view (no tabs)', async () => {
    renderAdmin(adminMe);
    // No tabs in the reconciled layout.
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    // KPI labels.
    expect(screen.getByText('Suites')).toBeInTheDocument();
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('Access grants')).toBeInTheDocument();
    // All three tables render without any interaction. 'Finance DQ' appears in
    // both the suites table and the access rows (so use findAllByText).
    expect((await screen.findAllByText('Finance DQ')).length).toBeGreaterThan(0);
    expect(screen.getByText('bob@x.io')).toBeInTheDocument(); // members
    expect(screen.getByText('owner')).toBeInTheDocument(); // access permission tag
    expect(screen.getByText('edit')).toBeInTheDocument();
  });

  it('surfaces a load error for a failed dataset', async () => {
    mockSuites.mockRejectedValue(new Error('boom'));
    renderAdmin(adminMe);
    expect(await screen.findByText('Failed to load suites')).toBeInTheDocument();
  });
});
