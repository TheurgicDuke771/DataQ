import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

import type { User } from '../../src/auth/authClient';

// authMode is computed at module load, so each case injects the runtime config
// and imports a fresh module graph (the config.test.ts pattern).
beforeEach(() => vi.resetModules());
afterEach(() => {
  delete (window as { __DATAQ_CONFIG__?: unknown }).__DATAQ_CONFIG__;
});

function inject(auth: Record<string, unknown>) {
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
