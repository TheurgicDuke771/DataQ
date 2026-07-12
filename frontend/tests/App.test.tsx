import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
    for (const item of ['Dashboard', 'Assets', 'Connections', 'Suites', 'Results', 'Profile']) {
      expect(screen.getByRole('link', { name: item })).toBeInTheDocument();
    }
    expect(screen.getByRole('link', { name: 'Documentation' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Settings' })).not.toBeInTheDocument();
  });

  it('leads the primary nav with Assets, right after Dashboard (nav inversion, #773)', () => {
    mockIsAdmin.mockReturnValue(false);
    mockUser.mockReturnValue(devUser);
    renderAt('/no-such-page');
    // The primary nav order is Dashboard → Assets → Connections → Suites → …:
    // Assets is the first-class lens (above Suites), suites stay secondary.
    const primary = screen.getAllByRole('link').map((l) => l.textContent);
    const dashboardIdx = primary.indexOf('Dashboard');
    const assetsIdx = primary.indexOf('Assets');
    const suitesIdx = primary.indexOf('Suites');
    expect(assetsIdx).toBe(dashboardIdx + 1);
    expect(assetsIdx).toBeLessThan(suitesIdx);
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

  // Mobile overlay nav (#801): below `lg` the Sider collapses to zero width and
  // the nav moves into an overlay Drawer so it never squeezes the content. The
  // Sider fires `onBreakpoint(matchMedia.matches)` on mount, so forcing
  // matchMedia to match drives the narrow layout in jsdom.
  describe('narrow viewport (#801)', () => {
    let realMatchMedia: typeof window.matchMedia;
    beforeEach(() => {
      realMatchMedia = window.matchMedia;
      window.matchMedia = (query: string): MediaQueryList =>
        ({
          matches: true,
          media: query,
          onchange: null,
          addListener: () => {},
          removeListener: () => {},
          addEventListener: () => {},
          removeEventListener: () => {},
          dispatchEvent: () => false,
        }) as MediaQueryList;
    });
    afterEach(() => {
      window.matchMedia = realMatchMedia;
    });

    it('hides the Sider nav and shows the hamburger; the nav opens in a Drawer overlay', async () => {
      mockIsAdmin.mockReturnValue(false);
      mockUser.mockReturnValue(devUser);
      renderAt('/dashboard');

      // The nav is not inline (would consume layout width) — it lives in a closed
      // Drawer, so no nav links are in the DOM yet, and the ☰ toggle is present.
      const toggle = screen.getByLabelText('Toggle navigation');
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByRole('link', { name: 'Assets' })).not.toBeInTheDocument();

      // Opening the overlay surfaces the nav inside a dialog (the Drawer, with its
      // built-in scrim) rather than pushing the page.
      await userEvent.click(toggle);
      expect(toggle).toHaveAttribute('aria-expanded', 'true');
      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).getByRole('link', { name: 'Assets' })).toBeInTheDocument();
      expect(within(dialog).getByRole('link', { name: 'Suites' })).toBeInTheDocument();
    });

    it('closes the overlay after a nav item is chosen', async () => {
      mockIsAdmin.mockReturnValue(false);
      mockUser.mockReturnValue(devUser);
      renderAt('/dashboard');

      const toggle = screen.getByLabelText('Toggle navigation');
      await userEvent.click(toggle);
      const dialog = await screen.findByRole('dialog');
      await userEvent.click(within(dialog).getByRole('link', { name: 'Assets' }));
      await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'false'));
    });
  });
});
