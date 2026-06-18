import { useEffect, useState, type ReactNode } from 'react';

import { fetchMe, type MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { MeContext } from './meContext';
import { useCurrentUser } from './useCurrentUser';

/**
 * Fetches `/me` once the user is authenticated and shares it via `MeContext`.
 *
 * The fetch is gated on `useCurrentUser()` (not done on bare mount) so that in
 * real-auth mode we wait until MSAL has an account — otherwise the request would
 * race ahead of the bearer token and 401. In dev-bypass the user is present
 * immediately. Re-runs if the signed-in identity changes.
 */
export function MeProvider({ children }: { children: ReactNode }) {
  const user = useCurrentUser();
  const [state, setState] = useState<AsyncState<MeResponse>>({ status: 'loading' });

  useEffect(() => {
    if (!user) return;
    let cancelled = false;
    // No synchronous setState('loading') here — the initial value is already
    // loading, and on an identity change we keep the prior data visible until the
    // refetch resolves (same "no flash to loading" behaviour as useAsyncData).
    fetchMe()
      .then((data) => {
        if (!cancelled) setState({ status: 'ok', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: 'error', error: err instanceof Error ? err.message : String(err) });
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
