import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import type { MeResponse } from '../api/me';
import { endSession, probeSession } from './otpClient';
import { authMode } from './config';
import { OtpSessionContext, type OtpSession, type OtpSessionState } from './otpSessionContext';
import { onSessionInvalidated } from './sessionEvents';

/** Owns the email-OTP session (ADR 0032, #736). */
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
        // Unconditional: `probing` can't be interrupted by a 401 it didn't cause (the probe's own
        // 401 resolves to signed_out through the normal path).
        setState({ status: 'signed_out' });
      }),
    [],
  );

  const adopt = useCallback((me: MeResponse) => {
    setState({ status: 'signed_in', me });
  }, []);

  const signOut = useCallback(() => {
    // Drop the UI to signed-out immediately and unconditionally.
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
