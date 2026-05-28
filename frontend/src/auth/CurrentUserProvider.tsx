import { useMsal } from '@azure/msal-react';
import { useMemo, type ReactNode } from 'react';

import { authMode, DEV_USER } from './config';
import { CurrentUserContext, type CurrentUser } from './currentUserContext';

/**
 * Provides the current user to the tree.
 *
 * - dev_bypass: static dev user.
 * - unconfigured: null (AuthGate shows the banner).
 * - real: subscribes to MSAL accounts via useMsal().
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
  const { accounts } = useMsal();
  const value = useMemo<CurrentUser | null>(() => {
    const account = accounts[0];
    if (!account) return null;
    return {
      name: account.name ?? '(unknown)',
      username: account.username,
      homeAccountId: account.homeAccountId,
      isDev: false,
    };
  }, [accounts]);
  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
}
