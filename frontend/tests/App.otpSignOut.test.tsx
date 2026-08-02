import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { App } from '../src/App';
import { useIsWorkspaceAdmin } from '../src/auth/useMe';
import { useCurrentUser } from '../src/auth/useCurrentUser';
import { useOtpSession } from '../src/auth/otpSessionContext';
import { logout } from '../src/auth/authClient';

/**
 * Sign-out in `otp` mode (ADR 0032, #736).
 *
 * Its own file because App.test.tsx pins `authMode` to dev_bypass at module scope
 * (a hoisted `vi.mock`), and the mode is what is under test here.
 *
 * The behaviour that matters: OTP sign-out is a **POST that revokes the session
 * server-side**, not the OIDC redirect. The SPA cannot clear an HttpOnly cookie
 * itself, so a "sign out" that only forgot local state would leave a live,
 * usable session behind on the server.
 */
vi.mock('../src/auth/config', () => ({ authMode: 'otp' }));
vi.mock('../src/auth/authClient', () => ({ login: vi.fn(), logout: vi.fn() }));
// ProfileCompletionPrompt (#1139) also reads useMe() (via itself) and
// useUpdateMe() (via useSaveDisplayName) — 'loading' keeps the prompt closed
// (shouldShow requires status 'ok') so both are inert for this sign-out test.
vi.mock('../src/auth/useMe', () => ({
  useIsWorkspaceAdmin: vi.fn(),
  useMe: vi.fn(() => ({ status: 'loading' })),
  useUpdateMe: vi.fn(() => vi.fn()),
}));
vi.mock('../src/auth/useCurrentUser', () => ({ useCurrentUser: vi.fn() }));
vi.mock('../src/auth/otpSessionContext', () => ({ useOtpSession: vi.fn() }));
vi.mock('../src/api/client', () => ({
  api: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
    put: vi.fn(() => new Promise(() => {})),
    delete: vi.fn(() => new Promise(() => {})),
  },
}));

const signOut = vi.fn();

beforeEach(() => {
  signOut.mockReset();
  vi.mocked(useIsWorkspaceAdmin).mockReturnValue(false);
  vi.mocked(useCurrentUser).mockReturnValue({
    name: 'Ada L',
    username: 'ada@acme.io',
    homeAccountId: 'u-9',
    isDev: false,
  });
  vi.mocked(useOtpSession).mockReturnValue({
    // Signed in, so AuthGate lets the app shell (and its account menu) render at
    // all — the same hook gates the shell and supplies the sign-out action.
    state: {
      status: 'signed_in',
      me: {
        id: 'u-9',
        aad_object_id: null,
        email: 'ada@acme.io',
        display_name: 'Ada L',
        last_seen_at: null,
        is_workspace_admin: false,
      },
    },
    adopt: vi.fn(),
    signOut,
    retry: vi.fn(),
  });
});

describe('UserMenu — otp sign-out', () => {
  it('revokes the cookie session instead of running the OIDC redirect', async () => {
    render(
      <MemoryRouter initialEntries={['/no-such-page']}>
        <App />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByText('Ada L'));
    const item = await screen.findByText('Sign out');
    // Enabled, and NOT the "(dev bypass)" affordance — this is a real session.
    expect(item.closest('li')).not.toHaveAttribute('aria-disabled', 'true');
    expect(screen.queryByText('DEV BYPASS')).not.toBeInTheDocument();

    await userEvent.click(item);
    expect(signOut).toHaveBeenCalledOnce();
    // The OIDC signoutRedirect would navigate to an IdP this deployment has none of.
    expect(vi.mocked(logout)).not.toHaveBeenCalled();
  });
});
