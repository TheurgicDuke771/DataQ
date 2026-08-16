import { App } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { setAdminUserRole, type AdminUser } from '../../src/api/admin';
import { RoleEditor } from '../../src/components/admin/RoleEditor';
import { selectOption } from '../support/antd';

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
  });

  it('shows the STORED role, not the effective one', () => {
    // A break-glass allowlist admin: stored `member`, effectively admin. The
    // editor writes the stored column, so showing `admin` would make demoting
    // them look like it silently failed.
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
    // The last-admin guard's message tells the admin what to do instead
    // ("promote another user to admin first"). A generic "failed to update role"
    // would discard exactly that part.
    // The axios interceptor swaps the error-envelope message onto `err.message`
    // before any caller sees it, so THIS is the shape a component actually
    // catches — mocking the raw response envelope would test a shape that never
    // reaches here.
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
    // Deliberately NOT disabled: a client-side "is this the last admin?" check
    // would race the server, and would disagree with it about whether allowlist
    // admins count (they do not). Better to let the server refuse and explain.
    renderEditor(user({ role: 'admin' }));
    expect(screen.getByRole('combobox')).not.toBeDisabled();
  });
});
