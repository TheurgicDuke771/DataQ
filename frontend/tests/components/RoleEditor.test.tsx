import { App } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setAdminUserRole, type AdminUser } from '../../src/api/admin';
import { fetchMe } from '../../src/api/me';
import { RoleEditor } from '../../src/components/admin/RoleEditor';
import { selectOption } from '../support/antd';

vi.mock('../../src/api/me', () => ({ fetchMe: vi.fn() }));

const mockUpdateMe = vi.fn();
let currentMe: { status: string; data?: { id: string } } = { status: 'loading' };
vi.mock('../../src/auth/useMe', () => ({
  useMe: () => currentMe,
  useUpdateMe: () => mockUpdateMe,
}));

vi.mock('../../src/api/admin', async () => {
  const actual = await vi.importActual<typeof import('../../src/api/admin')>('../../src/api/admin');
  return { ...actual, setAdminUserRole: vi.fn() };
});

const mockSet = vi.mocked(setAdminUserRole);

function user(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: 'u-1',
    email: 'bob@x.io',
    display_name: 'Bob',
    last_seen_at: null,
    created_at: '2026-01-01T00:00:00Z',
    owned_suite_count: 0,
    shared_suite_count: 0,
    role: 'member',
    allowlist_admin: false,
    ...overrides,
  };
}

/** antd's `message` comes from `App.useApp()`, so the component needs the
 *  provider — rendering it bare throws, which is how the convention was found. */
function renderEditor(u: AdminUser, onChanged = vi.fn()) {
  render(
    <App>
      <RoleEditor user={u} onChanged={onChanged} />
    </App>,
  );
  return onChanged;
}

/** The shared antd-Select helper (`tests/support/antd`), so the one place that
 *  couples to antd's dropdown internals stays that one place. */
async function pick(role: string) {
  // `by: 'text'` — the CURRENT selection also carries `title=<role>`, so a
  // title match finds two elements once the dropdown opens.
  await selectOption(userEvent.setup(), role, { by: 'text' });
}

describe('RoleEditor', () => {
  beforeEach(() => {
    mockSet.mockReset();
    mockUpdateMe.mockReset();
    vi.mocked(fetchMe).mockReset();
    currentMe = { status: 'ok', data: { id: 'someone-else' } };
  });

  it('shows the STORED role, not the effective one', () => {
    // A break-glass allowlist admin: stored `member`, effectively admin.
    renderEditor(user({ role: 'member', allowlist_admin: true }));
    expect(screen.getByTitle('member')).toBeInTheDocument();
    expect(screen.queryByTitle('admin')).not.toBeInTheDocument();
    expect(screen.getByText('via allowlist')).toBeInTheDocument();
  });

  it('does not show the allowlist tag for an ordinary user', () => {
    renderEditor(user({ allowlist_admin: false }));
    expect(screen.queryByText('via allowlist')).not.toBeInTheDocument();
  });

  it('sends the new role and hands the server response back', async () => {
    const updated = user({ role: 'admin' });
    mockSet.mockResolvedValue(updated);
    const onChanged = renderEditor(user({ role: 'member' }));

    await pick('admin');

    await waitFor(() => expect(mockSet).toHaveBeenCalledWith('u-1', 'admin'));
    // The SERVER's row is what propagates — never an optimistic local guess, so
    // a change that appears to succeed and then reverts is impossible.
    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(updated));
  });

  it('surfaces the server refusal verbatim and does not update the row', async () => {
    // The last-admin guard's message tells the admin what to do instead ("promote another user to
    // admin first").
    mockSet.mockRejectedValue(
      new Error('cannot remove the last workspace admin — promote another user first'),
    );
    const onChanged = renderEditor(user({ role: 'admin' }));

    await pick('member');

    expect(await screen.findByText(/cannot remove the last workspace admin/i)).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('does not call the API when the role is unchanged', async () => {
    renderEditor(user({ role: 'member' }));
    await pick('member');
    expect(mockSet).not.toHaveBeenCalled();
  });

  it('stays enabled for the last admin — the guard is the server’s call', () => {
    // Deliberately NOT disabled: a client-side "is this the last admin?" check would race the
    // server, and would disagree with it about whether allowlist admins count (they do not).
    renderEditor(user({ role: 'admin' }));
    expect(screen.getByRole('combobox')).not.toBeDisabled();
  });

  it('refetches /me when an admin changes their OWN role', async () => {
    // Without this, a self-demoting admin keeps `is_workspace_admin: true` in the shared context:
    // the Admin nav and page keep rendering.
    currentMe = { status: 'ok', data: { id: 'u-1' } };
    const refreshed = { id: 'u-1', role: 'member', is_workspace_admin: false };
    mockSet.mockResolvedValue(user({ role: 'member' }));
    vi.mocked(fetchMe).mockResolvedValue(refreshed as never);
    renderEditor(user({ id: 'u-1', role: 'admin' }));

    await pick('member');

    // Refetched, not patched locally: `/me` reports the EFFECTIVE role, and an
    // admin still on WORKSPACE_ADMIN_EMAILS remains one — only the server knows.
    await waitFor(() => expect(fetchMe).toHaveBeenCalled());
    await waitFor(() => expect(mockUpdateMe).toHaveBeenCalledWith(refreshed));
  });

  it('does not refetch /me when changing someone else’s role', async () => {
    currentMe = { status: 'ok', data: { id: 'someone-else' } };
    mockSet.mockResolvedValue(user({ role: 'viewer' }));
    renderEditor(user({ id: 'u-1', role: 'member' }));

    await pick('viewer');

    await waitFor(() => expect(mockSet).toHaveBeenCalled());
    expect(fetchMe).not.toHaveBeenCalled();
    expect(mockUpdateMe).not.toHaveBeenCalled();
  });
});
