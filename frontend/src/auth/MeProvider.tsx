import { useCallback, useEffect, useState, type ReactNode } from 'react';

import { retryAfterSeconds } from '../api/client';
import { fetchMe, type MeResponse } from '../api/me';
import type { AsyncState } from '../hooks/useAsyncData';
import { fetchFailure } from '../utils/errors';
import { MeContext, MeUpdateContext } from './meContext';
import { useCurrentUser } from './useCurrentUser';

/** Fetches `/me` once the user is authenticated and shares it via `MeContext`. */
/** How many times a throttled first `/me` retries before giving up and showing
 *  the error. Bounded so a persistent 429 can't spin forever (#788). */
const ME_429_RETRIES = 3;

export function MeProvider({ children }: { children: ReactNode }) {
  const user = useCurrentUser();
  const [state, setState] = useState<AsyncState<MeResponse>>({ status: 'loading' });

  // Reset to loading the instant the signed-in identity changes — including sign-out (user→null).
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
            // A throttled FIRST /me is the reachable case (#788).
            const retryAfter = retryAfterSeconds(err);
            if (retryAfter !== undefined && attemptsLeft > 0) {
              attemptsLeft -= 1;
              retryTimer = setTimeout(load, Math.max(1, retryAfter) * 1000);
              return;
            }
            // Bounded, so a persistent 429 still lands on a real error rather than spinning forever
            // — an infinite spinner is the one outcome worse than an error page.

            // Classify like every other page fetch (#910/#930): Admin, Profile and Settings render
            // PageError straight off this state.
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
