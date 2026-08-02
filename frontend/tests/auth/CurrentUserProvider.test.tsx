import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

import type { User } from '../../src/auth/authClient';
import type { DataqAuthConfig } from '../../src/auth/config';

// authMode is computed at module load, so each case injects the runtime config
// and imports a fresh module graph (the config.test.ts pattern).
beforeEach(() => vi.resetModules());
afterEach(() => {
  delete (window as { __DATAQ_CONFIG__?: unknown }).__DATAQ_CONFIG__;
});

function inject(auth: DataqAuthConfig) {
  (window as { __DATAQ_CONFIG__?: unknown }).__DATAQ_CONFIG__ = { auth };
}

async function renderTree(oidcUser: User | null = null) {
  const { CurrentUserProvider } = await import('../../src/auth/CurrentUserProvider');
  const { AuthContext } = await import('../../src/auth/authContext');
  const { useCurrentUser } = await import('../../src/auth/useCurrentUser');

  function Probe() {
    const user = useCurrentUser();
    return (
      <span data-testid="who">
        {user ? `${user.name}|${user.username}|dev:${user.isDev}` : 'null'}
      </span>
    );
  }
  function tree(children: ReactNode) {
    return <AuthContext.Provider value={{ user: oidcUser }}>{children}</AuthContext.Provider>;
  }
  render(
    tree(
      <CurrentUserProvider>
        <Probe />
      </CurrentUserProvider>,
    ),
  );
}

describe('CurrentUserProvider', () => {
  it('provides the static dev user under bypass', async () => {
    inject({ mode: 'bypass' });
    await renderTree();
    expect(screen.getByTestId('who')).toHaveTextContent('dev:true');
  });

  it('provides null when unconfigured', async () => {
    inject({});
    await renderTree();
    expect(screen.getByTestId('who')).toHaveTextContent('null');
  });

  it('derives the real user from the OIDC profile', async () => {
    inject({ mode: 'oidc', authority: 'https://issuer.example/v2.0', clientId: 'spa-1' });
    await renderTree({
      profile: { name: 'Olivia', preferred_username: 'olivia@example.com', sub: 's-1' },
    } as unknown as User);
    expect(screen.getByTestId('who')).toHaveTextContent('Olivia|olivia@example.com|dev:false');
  });

  it('falls back username to email→sub and name to "(unknown)"', async () => {
    inject({ mode: 'oidc', authority: 'https://issuer.example/v2.0', clientId: 'spa-1' });
    await renderTree({ profile: { sub: 's-2' } } as unknown as User);
    expect(screen.getByTestId('who')).toHaveTextContent('(unknown)|s-2|dev:false');
  });

  it('provides null in real mode while signed out', async () => {
    inject({ mode: 'oidc', authority: 'https://issuer.example/v2.0', clientId: 'spa-1' });
    await renderTree(null);
    expect(screen.getByTestId('who')).toHaveTextContent('null');
  });
});

/**
 * `otp` mode (ADR 0032, #736). The identity comes from the resolved session, not
 * from an OIDC profile — and `homeAccountId` deliberately carries the DataQ user
 * id, because MeProvider keys its refetch on that value: if it were a constant,
 * one user's `is_workspace_admin` would survive a sign-out into the next user's
 * session.
 */
describe('CurrentUserProvider — otp mode', () => {
  const ME = {
    id: 'u-9',
    aad_object_id: null,
    email: 'ada@acme.io',
    display_name: 'Ada L',
    last_seen_at: null,
    is_workspace_admin: false,
  };

  async function renderOtpTree(state: unknown) {
    inject({ mode: 'otp' });
    const { CurrentUserProvider } = await import('../../src/auth/CurrentUserProvider');
    const { OtpSessionContext } = await import('../../src/auth/otpSessionContext');
    const { useCurrentUser } = await import('../../src/auth/useCurrentUser');
    const noop = () => {};

    function Probe() {
      const user = useCurrentUser();
      return (
        <span data-testid="who">
          {user ? `${user.name}|${user.username}|${user.homeAccountId}|dev:${user.isDev}` : 'null'}
        </span>
      );
    }
    render(
      <OtpSessionContext.Provider
        value={
          { state, adopt: noop, signOut: noop, retry: noop } as React.ContextType<
            typeof OtpSessionContext
          >
        }
      >
        <CurrentUserProvider>
          <Probe />
        </CurrentUserProvider>
      </OtpSessionContext.Provider>,
    );
  }

  it('derives the user from the signed-in session, keyed on the DataQ user id', async () => {
    await renderOtpTree({ status: 'signed_in', me: ME });
    expect(screen.getByTestId('who')).toHaveTextContent('Ada L|ada@acme.io|u-9|dev:false');
  });

  it('falls back to the email when the identity has no display name', async () => {
    await renderOtpTree({ status: 'signed_in', me: { ...ME, display_name: null } });
    expect(screen.getByTestId('who')).toHaveTextContent('ada@acme.io|ada@acme.io|u-9');
  });

  it.each(['probing', 'signed_out'])('provides null while %s', async (status) => {
    await renderOtpTree({ status });
    expect(screen.getByTestId('who')).toHaveTextContent('null');
  });

  it('is never the dev user — otp is a real authenticator, not a bypass', async () => {
    await renderOtpTree({ status: 'signed_in', me: ME });
    expect(screen.getByTestId('who')).not.toHaveTextContent('dev:true');
  });
});
