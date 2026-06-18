import { App as AntApp } from 'antd';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { listAdminAccess, listAdminSuites, listAdminUsers } from '../../src/api/admin';
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

afterEach(() => vi.clearAllMocks());

describe('Admin', () => {
  it('shows the Forbidden page for a non-admin (server-driven via /me)', () => {
    renderAdmin({ ...adminMe, data: { ...adminMe.data, is_workspace_admin: false } });

    expect(screen.getByText('403 — Forbidden')).toBeInTheDocument();
    // The admin tables must not even attempt to load for a non-admin.
    expect(mockSuites).not.toHaveBeenCalled();
  });

  it('lists all suites with owner + counts on the default tab', async () => {
    mockSuites.mockResolvedValue([
      {
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
      },
    ]);
    renderAdmin(adminMe);

    expect(await screen.findByText('Finance DQ')).toBeInTheDocument();
    expect(screen.getByText('Olive Owner')).toBeInTheDocument();
    expect(screen.getByText('olive@x.io')).toBeInTheDocument();
    expect(screen.getByText('sf-prod')).toBeInTheDocument();
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('shows users with owned/shared counts on the Users tab', async () => {
    mockUsers.mockResolvedValue([
      {
        id: 'u9',
        email: 'bob@x.io',
        display_name: null,
        last_seen_at: null,
        created_at: '2026-06-01T00:00:00Z',
        owned_suite_count: 3,
        shared_suite_count: 1,
      },
    ]);
    renderAdmin(adminMe);

    await userEvent.click(screen.getByRole('tab', { name: 'Users' }));

    // No display name → the email stands in as the identity.
    expect(await screen.findByText('bob@x.io')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(mockUsers).toHaveBeenCalledTimes(1);
  });

  it('shows the access matrix with permission tags on the Access tab', async () => {
    mockAccess.mockResolvedValue([
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
    ]);
    renderAdmin(adminMe);

    await userEvent.click(screen.getByRole('tab', { name: 'Access' }));

    expect(await screen.findByText('owner')).toBeInTheDocument();
    expect(screen.getByText('edit')).toBeInTheDocument();
  });

  it('surfaces a load error on a tab', async () => {
    mockSuites.mockRejectedValue(new Error('boom'));
    renderAdmin(adminMe);

    expect(await screen.findByText('Failed to load suites')).toBeInTheDocument();
  });
});
