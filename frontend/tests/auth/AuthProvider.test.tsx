import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getUserManager, type User } from '../../src/auth/authClient';
import { AuthProvider } from '../../src/auth/AuthProvider';
import { useAuthUser } from '../../src/auth/authContext';

vi.mock('../../src/auth/authClient', () => ({ getUserManager: vi.fn() }));
const mockGetUserManager = vi.mocked(getUserManager);

function Probe() {
  const user = useAuthUser();
  return <span data-testid="user">{user ? user.profile.name : 'none'}</span>;
}

type Handler = (u: User) => void;

/** A UserManager double: persisted-session seed + the four event channels. */
function makeManager(persisted: User | null) {
  const listeners: Record<string, Set<() => void> | Set<Handler>> = {
    loaded: new Set<Handler>(),
    unloaded: new Set<() => void>(),
    renewError: new Set<() => void>(),
    signedOut: new Set<() => void>(),
  };
  return {
    listeners,
    getUser: () => Promise.resolve(persisted),
    events: {
      addUserLoaded: (h: Handler) => (listeners.loaded as Set<Handler>).add(h),
      removeUserLoaded: (h: Handler) => (listeners.loaded as Set<Handler>).delete(h),
      addUserUnloaded: (h: () => void) => (listeners.unloaded as Set<() => void>).add(h),
      removeUserUnloaded: (h: () => void) => (listeners.unloaded as Set<() => void>).delete(h),
      addSilentRenewError: (h: () => void) => (listeners.renewError as Set<() => void>).add(h),
      removeSilentRenewError: (h: () => void) =>
        (listeners.renewError as Set<() => void>).delete(h),
      addUserSignedOut: (h: () => void) => (listeners.signedOut as Set<() => void>).add(h),
      removeUserSignedOut: (h: () => void) => (listeners.signedOut as Set<() => void>).delete(h),
    },
  };
}

const oidcUser = { profile: { name: 'Olivia', sub: 's-1' } } as unknown as User;

afterEach(() => vi.clearAllMocks());

describe('AuthProvider', () => {
  it('passes through a null user when no manager exists (bypass/unconfigured)', () => {
    mockGetUserManager.mockReturnValue(null);
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId('user')).toHaveTextContent('none');
  });

  it('seeds from the persisted session', async () => {
    const mgr = makeManager(oidcUser);
    mockGetUserManager.mockReturnValue(mgr as unknown as ReturnType<typeof getUserManager>);
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(await screen.findByText('Olivia')).toBeInTheDocument();
  });

  it('tracks load → renew-error/sign-out transitions and unsubscribes on unmount', async () => {
    const mgr = makeManager(null);
    mockGetUserManager.mockReturnValue(mgr as unknown as ReturnType<typeof getUserManager>);
    const { unmount } = render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(screen.getByTestId('user')).toHaveTextContent('none');

    act(() => (mgr.listeners.loaded as Set<(u: User) => void>).forEach((h) => h(oidcUser)));
    expect(screen.getByTestId('user')).toHaveTextContent('Olivia');

    // A failed silent renew must drop the user (stale-session guard).
    act(() => (mgr.listeners.renewError as Set<() => void>).forEach((h) => h()));
    expect(screen.getByTestId('user')).toHaveTextContent('none');

    act(() => (mgr.listeners.loaded as Set<(u: User) => void>).forEach((h) => h(oidcUser)));
    act(() => (mgr.listeners.signedOut as Set<() => void>).forEach((h) => h()));
    expect(screen.getByTestId('user')).toHaveTextContent('none');

    unmount();
    expect(mgr.listeners.loaded.size).toBe(0);
    expect(mgr.listeners.unloaded.size).toBe(0);
    expect(mgr.listeners.renewError.size).toBe(0);
    expect(mgr.listeners.signedOut.size).toBe(0);
  });
});
