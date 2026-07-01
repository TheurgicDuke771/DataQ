import { useEffect, useState, type ReactNode } from 'react';

import { getUserManager, type User } from './authClient';
import { AuthContext } from './authContext';

/**
 * Subscribes to the OIDC UserManager and exposes the signed-in user to the tree
 * (the generic-client replacement for msal-react's MsalProvider + useMsal). In
 * dev_bypass / unconfigured modes getUserManager() is null, so this is a
 * passthrough with a null user — CurrentUserProvider and AuthGate dispatch by
 * auth mode, so nothing reads the user in those modes.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const mgr = getUserManager();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    if (!mgr) return;
    let active = true;
    // Seed from any persisted session, then track load/unload (sign-in, silent
    // renew, sign-out) so the tree re-renders on session changes.
    void mgr.getUser().then((u) => {
      if (active) setUser(u);
    });
    const onLoaded = (u: User) => setUser(u);
    const onCleared = () => setUser(null);
    mgr.events.addUserLoaded(onLoaded);
    mgr.events.addUserUnloaded(onCleared);
    // A failed background silent-renew or an IdP-side sign-out doesn't fire
    // userUnloaded — clear the user on those too, so the tree drops to the
    // sign-in page instead of showing a stale, half-broken authenticated UI.
    mgr.events.addSilentRenewError(onCleared);
    mgr.events.addUserSignedOut(onCleared);
    return () => {
      active = false;
      mgr.events.removeUserLoaded(onLoaded);
      mgr.events.removeUserUnloaded(onCleared);
      mgr.events.removeSilentRenewError(onCleared);
      mgr.events.removeUserSignedOut(onCleared);
    };
  }, [mgr]);

  return <AuthContext.Provider value={{ user }}>{children}</AuthContext.Provider>;
}
