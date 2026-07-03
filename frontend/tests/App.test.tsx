import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from '../src/App';
import { useIsWorkspaceAdmin } from '../src/auth/useMe';
import { useCurrentUser } from '../src/auth/useCurrentUser';

// The shell composes AuthGate + nav + lazy routes; the hooks are its inputs.
// dev_bypass keeps AuthGate a passthrough so the Layout itself is under test.
vi.mock('../src/auth/config', () => ({ authMode: 'dev_bypass' }));
vi.mock('../src/auth/authClient', () => ({ login: vi.fn(), logout: vi.fn() }));
vi.mock('../src/auth/useMe', () => ({ useIsWorkspaceAdmin: vi.fn() }));
vi.mock('../src/auth/useCurrentUser', () => ({ useCurrentUser: vi.fn() }));
// Lazy route pages fetch on mount; a forever-pending client keeps them in
// their loading state so shell assertions don't race real requests.
vi.mock('../src/api/client', () => ({
  api: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
    put: vi.fn(() => new Promise(() => {})),
    delete: vi.fn(() => new Promise(() => {})),
  },
}));

const mockIsAdmin = vi.mocked(useIsWorkspaceAdmin);
const mockUser = vi.mocked(useCurrentUser);

const devUser = {
  name: 'Dev Bypass User',
  username: 'dev-bypass@dataq.local',
  homeAccountId: 'acc',
  isDev: true,
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('App shell', () => {
  it('renders the primary nav and hides Admin/Settings for non-admins', () => {
    mockIsAdmin.mockReturnValue(false);
    mockUser.mockReturnValue(devUser);
    renderAt('/no-such-page');
    for (const item of ['Dashboard', 'Connections', 'Suites', 'Results', 'Profile']) {
      expect(screen.getByRole('link', { name: item })).toBeInTheDocument();
    }
    expect(screen.getByRole('link', { name: 'Documentation' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Settings' })).not.toBeInTheDocument();
  });

  it('shows the Admin/Settings footer nav to workspace admins', () => {
    mockIsAdmin.mockReturnValue(true);
    mockUser.mockReturnValue(devUser);
    renderAt('/no-such-page');
    expect(screen.getByRole('link', { name: 'Admin' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument();
  });

  it('routes an unknown path to the in-brand 404 page (no silent redirect)', async () => {
    mockIsAdmin.mockReturnValue(false);
    mockUser.mockReturnValue(devUser);
    renderAt('/no-such-page');
    expect(await screen.findByText(/404|not found/i)).toBeInTheDocument();
  });

  it('highlights the owning nav item on a sub-path', async () => {
    mockIsAdmin.mockReturnValue(false);
    mockUser.mockReturnValue(devUser);
    renderAt('/suites/123');
    await waitFor(() => {
      const selected = document.querySelectorAll('.ant-menu-item-selected');
      expect(selected).toHaveLength(1);
      expect(selected[0].textContent).toBe('Suites');
    });
  });

  it('does not highlight by plain prefix on a sibling path', async () => {
    mockIsAdmin.mockReturnValue(false);
    mockUser.mockReturnValue(devUser);
    // '/results-export' starts with '/results' but is NOT under it — plain
    // startsWith would mis-highlight Results; segment-boundary matching must not.
    renderAt('/results-export');
    await waitFor(() =>
      expect(document.querySelectorAll('.ant-menu-item-selected')).toHaveLength(0),
    );
  });

  it('opens the account menu: identity, DEV BYPASS tag, disabled sign-out', async () => {
    mockIsAdmin.mockReturnValue(false);
    mockUser.mockReturnValue(devUser);
    renderAt('/no-such-page');
    // Avatar initials: first + last word initials, uppercased.
    expect(screen.getByText('DU')).toBeInTheDocument();
    await userEvent.click(screen.getByText('Dev Bypass User'));
    expect(await screen.findByText('DEV BYPASS')).toBeInTheDocument();
    expect(screen.getByText('dev-bypass@dataq.local')).toBeInTheDocument();
    const signOut = await screen.findByText('Sign out (dev bypass)');
    expect(signOut.closest('li')).toHaveAttribute('aria-disabled', 'true');
  });

  it('renders no account menu while the user is unresolved', () => {
    mockIsAdmin.mockReturnValue(false);
    mockUser.mockReturnValue(null);
    renderAt('/no-such-page');
    expect(screen.queryByText('DU')).not.toBeInTheDocument();
  });
});
