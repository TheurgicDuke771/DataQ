import { useMemo, type ReactNode } from 'react';

import { useAuthUser } from './authContext';
import { authMode, DEV_USER } from './config';
import { CurrentUserContext, type CurrentUser } from './currentUserContext';
import { useOtpSession } from './otpSessionContext';

/**
 * Provides the current user to the tree. - dev_bypass: static dev user. - unconfigured: null
 * (AuthGate shows the banner). - real: derives from the OIDC user (useAuthUser). - otp: derives
 */
export function CurrentUserProvider({ children }: { children: ReactNode }) {
  if (authMode === 'dev_bypass') {
    return <CurrentUserContext.Provider value={DEV_USER}>{children}</CurrentUserContext.Provider>;
  }
  if (authMode === 'real') {
    return <RealCurrentUserProvider>{children}</RealCurrentUserProvider>;
  }
  if (authMode === 'otp') {
    return <OtpCurrentUserProvider>{children}</OtpCurrentUserProvider>;
  }
  return <CurrentUserContext.Provider value={null}>{children}</CurrentUserContext.Provider>;
}

/** The OTP identity. */
function OtpCurrentUserProvider({ children }: { children: ReactNode }) {
  const { state } = useOtpSession();
  const me = state.status === 'signed_in' ? state.me : null;
  const value = useMemo<CurrentUser | null>(() => {
    if (!me) return null;
    return {
      name: me.display_name ?? me.email,
      username: me.email,
      homeAccountId: me.id,
      isDev: false,
    };
  }, [me]);
  return <CurrentUserContext.Provider value={value}>{children}</CurrentUserContext.Provider>;
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
