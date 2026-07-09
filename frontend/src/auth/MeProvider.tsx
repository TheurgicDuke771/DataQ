import { useEffect, useState, type ReactNode } from 'react';

import { fetchMe, type MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { errorMessage } from '../utils/errors';
import { MeContext } from './meContext';
import { useCurrentUser } from './useCurrentUser';

/**
 * Fetches `/me` once the user is authenticated and shares it via `MeContext`.
 *
 * The fetch is gated on `useCurrentUser()` (not done on bare mount) so that in
 * real-auth mode we wait until the OIDC client has a signed-in user — otherwise
 * the request would race ahead of the bearer token and 401. In dev-bypass the user is present
 * immediately. Re-runs if the signed-in identity changes.
 */
export function MeProvider({ children }: { children: ReactNode }) {
  const user = useCurrentUser();
  const [state, setState] = useState<AsyncState<MeResponse>>({ status: 'loading' });

  // Reset to loading the instant the signed-in identity changes — including
  // sign-out (user→null) — so the previous user's /me (and its
  // is_workspace_admin) can never linger and keep admin UI visible. Render-phase
  // adjustment, not an effect (an effect can't setState synchronously, and the
  // reset must land before children read the context this render).
  const userId = user?.homeAccountId ?? null;
  const [seenUserId, setSeenUserId] = useState(userId);
  if (userId !== seenUserId) {
    setSeenUserId(userId);
    setState({ status: 'loading' });
  }

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    fetchMe()
      .then((data) => {
        if (!cancelled) setState({ status: 'ok', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: 'error', error: errorMessage(err, String(err)) });
        }
      });
    return () => {
      cancelled = true;
    };
    // `user` is memoized by CurrentUserProvider (stable unless the signed-in
    // identity actually changes), so this refetches on real identity change only.
  }, [user]);

  return <MeContext.Provider value={state}>{children}</MeContext.Provider>;
}
