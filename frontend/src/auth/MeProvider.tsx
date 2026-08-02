import { useCallback, useEffect, useState, type ReactNode } from 'react';

import { retryAfterSeconds } from '../api/client';
import { fetchMe, type MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { fetchFailure } from '../utils/errors';
import { MeContext, MeUpdateContext } from './meContext';
import { useCurrentUser } from './useCurrentUser';

/**
 * Fetches `/me` once the user is authenticated and shares it via `MeContext`.
 *
 * The fetch is gated on `useCurrentUser()` (not done on bare mount) so that in
 * real-auth mode we wait until the OIDC client has a signed-in user — otherwise
 * the request would race ahead of the bearer token and 401. In dev-bypass the user is present
 * immediately. Re-runs if the signed-in identity changes.
 */
/** How many times a throttled first `/me` retries before giving up and showing
 *  the error. Bounded so a persistent 429 can't spin forever (#788). */
const ME_429_RETRIES = 3;

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
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attemptsLeft = ME_429_RETRIES;

    const load = (): void => {
      void fetchMe()
        .then((data) => {
          if (!cancelled) setState({ status: 'ok', data });
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            // A throttled FIRST /me is the reachable case (#788). The per-IP
            // unauth bucket is shared across endpoints, so a burst anywhere can
            // 429 this one — and painting PageError for it is wrong twice over:
            // the app is fine, and the answer arrives on its own a few seconds
            // later. Stay in `loading` (a spinner is honest here — we genuinely
            // don't know yet) and retry when the server said to.
            //
            // Deliberately NOT the issue's "cache the last-known is_workspace_admin"
            // sketch: after a successful load this provider only refetches via a
            // timer a 429 itself sets, so a cached `ok` is never the state a later
            // 429 lands on. That path doesn't exist to protect.
            const retryAfter = retryAfterSeconds(err);
            if (retryAfter !== undefined && attemptsLeft > 0) {
              attemptsLeft -= 1;
              retryTimer = setTimeout(load, Math.max(1, retryAfter) * 1000);
              return;
            }
            // Bounded, so a persistent 429 still lands on a real error rather
            // than spinning forever — an infinite spinner is the one outcome
            // worse than an error page.

            // Classify like every other page fetch (#910/#930): Admin, Profile
            // and Settings render PageError straight off this state, so without
            // the HTTP facts a 403 or a 500 here would both paint the same page.
            const failure = fetchFailure(err, String(err));
            setState({
              status: 'error',
              error: failure.message,
              kind: failure.kind,
              httpStatus: failure.status,
              requestId: failure.requestId,
            });
          }
        });
    };
    load();
    return () => {
      cancelled = true;
      if (retryTimer !== undefined) clearTimeout(retryTimer);
    };
    // `user` is memoized by CurrentUserProvider (stable unless the signed-in
    // identity actually changes), so this refetches on real identity change only.
  }, [user]);

  // Adopt a fresh `/me` body a PATCH already returned (#1139) — a plain
  // `setState`, not a refetch, so a save is one request, not two.
  const updateMe = useCallback((me: MeResponse) => {
    setState({ status: 'ok', data: me });
  }, []);

  return (
    <MeContext.Provider value={state}>
      <MeUpdateContext.Provider value={updateMe}>{children}</MeUpdateContext.Provider>
    </MeContext.Provider>
  );
}
