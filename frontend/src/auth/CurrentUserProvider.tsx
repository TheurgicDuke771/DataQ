import { useMemo, type ReactNode } from 'react';

import { useAuthUser } from './authContext';
import { authMode, DEV_USER } from './config';
import { CurrentUserContext, type CurrentUser } from './currentUserContext';

/**
 * Provides the current user to the tree.
 *
 * - dev_bypass: static dev user.
 * - unconfigured: null (AuthGate shows the banner).
 * - real: derives from the OIDC user (useAuthUser).
 */
export function CurrentUserProvider({ children }: { children: ReactNode }) {
  if (authMode === 'dev_bypass') {
    return <CurrentUserContext.Provider value={DEV_USER}>{children}</CurrentUserContext.Provider>;
  }
  if (authMode === 'real') {
    return <RealCurrentUserProvider>{children}</RealCurrentUserProvider>;
  }
  return <CurrentUserContext.Provider value={null}>{children}</CurrentUserContext.Provider>;
}

function RealCurrentUserProvider({ children }: { children: ReactNode }) {
  const user = useAuthUser();
  const value = useMemo<CurrentUser | null>(() => {
    if (!user) return null;
    const profile = user.profile;
    const username = profile.preferred_username ?? profile.email ?? profile.sub;
    return {
      name: typeof profile.name === 'string' ? profile.name : '(unknown)',
      username,
      homeAccountId: profile.sub,
      isDev: false,
    };
  }, [user]);
  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}
