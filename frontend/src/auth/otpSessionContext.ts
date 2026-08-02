import { createContext, useContext } from 'react';

import type { MeResponse } from '../api/me';

/**
 * The four honest answers to "is there a session?" in `otp` mode (ADR 0032).
 *
 * `probing` is a real state, not a loading nicety: the session lives in an
 * HttpOnly cookie, so the SPA genuinely cannot know until `GET /me` answers. And
 * `error` is separate from `signed_out` on purpose — an unreachable API is not a
 * signed-out user, and rendering the sign-in form for it would invite somebody to
 * type a code at a server that cannot verify it.
 */
export type OtpSessionState =
  | { status: 'probing' }
  | { status: 'signed_out' }
  | { status: 'signed_in'; me: MeResponse }
  | { status: 'error'; message: string };

export interface OtpSession {
  state: OtpSessionState;
  /** Adopt the identity `otp/verify` just returned — no second round trip. */
  adopt: (me: MeResponse) => void;
  /** Revoke server-side, then drop to signed-out whether or not that succeeded. */
  signOut: () => void;
  /** Re-run the probe after an `error` state. */
  retry: () => void;
}

const NOOP = () => {};

/**
 * Default is `signed_out` with inert actions: in `oidc`/`bypass`/`unconfigured`
 * modes no provider is mounted and nothing should be reading this. Fail-closed —
 * a stray consumer sees "no session", never a fabricated one.
 */
export const OtpSessionContext = createContext<OtpSession>({
  state: { status: 'signed_out' },
  adopt: NOOP,
  signOut: NOOP,
  retry: NOOP,
});

export function useOtpSession(): OtpSession {
  return useContext(OtpSessionContext);
}
