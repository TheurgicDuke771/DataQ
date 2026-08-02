import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import type { MeResponse } from '../api/me';
import { endSession, probeSession } from './otpClient';
import { authMode } from './config';
import { OtpSessionContext, type OtpSession, type OtpSessionState } from './otpSessionContext';
import { onSessionInvalidated } from './sessionEvents';

/**
 * Owns the email-OTP session (ADR 0032, #736).
 *
 * The session token is an HttpOnly cookie the SPA cannot read, so this provider
 * never holds a credential — only the *answer* to whether one currently works.
 * That answer comes from exactly two places:
 *
 *  1. `GET /me` on mount (the only probe available), and
 *  2. the 401 event the shared axios client publishes for any non-`/auth/` call.
 *
 * (2) is what makes a server-side revocation visible: the cookie is still in the
 * browser and the SPA has no way to notice on its own, so the next request that
 * comes back 401 is the notification, and the UI drops to the sign-in screen.
 *
 * Mounted only in `otp` mode — the other modes get a passthrough, so no `/me`
 * probe races the OIDC token acquisition.
 */
export function OtpSessionProvider({ children }: { children: ReactNode }) {
  if (authMode !== 'otp') return <>{children}</>;
  return <ActiveOtpSessionProvider>{children}</ActiveOtpSessionProvider>;
}

function ActiveOtpSessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<OtpSessionState>({ status: 'probing' });
  // Bumping this re-runs the probe effect; `retry()` is the only thing that does.
  const [probeAttempt, setProbeAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void probeSession()
      .then((me) => {
        if (cancelled) return;
        setState(me ? { status: 'signed_in', me } : { status: 'signed_out' });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Deliberately NOT collapsed into signed_out — see probeSession().
        setState({ status: 'error', message: messageOf(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [probeAttempt]);

  useEffect(
    () =>
      onSessionInvalidated(() => {
        // Unconditional: `probing` can't be interrupted by a 401 it didn't cause
        // (the probe's own 401 resolves to signed_out through the normal path),
        // and re-setting an already-signed_out state is a no-op render.
        setState({ status: 'signed_out' });
      }),
    [],
  );

  const adopt = useCallback((me: MeResponse) => {
    setState({ status: 'signed_in', me });
  }, []);

  const signOut = useCallback(() => {
    // Drop the UI to signed-out immediately and unconditionally. Waiting on the
    // POST would leave the app rendering an authenticated shell while the request
    // is in flight, and a FAILED revoke must not strand the user inside the app
    // believing they signed out — the cookie may be gone from the browser either
    // way, and the seam re-checks revocation on every request regardless.
    setState({ status: 'signed_out' });
    void endSession().catch((err: unknown) => {
      console.error('Sign-out did not complete cleanly', err);
    });
  }, []);

  const retry = useCallback(() => {
    setState({ status: 'probing' });
    setProbeAttempt((n) => n + 1);
  }, []);

  const value = useMemo<OtpSession>(
    () => ({ state, adopt, signOut, retry }),
    [state, adopt, signOut, retry],
  );
  return <OtpSessionContext.Provider value={value}>{children}</OtpSessionContext.Provider>;
}

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : 'Could not reach the DataQ API.';
}
