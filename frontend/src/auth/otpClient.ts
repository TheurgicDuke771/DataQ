/**
 * Email-OTP sign-in transport (ADR 0032, #736).
 *
 * Three calls, all POST — `SameSite=Lax` only protects mutations that are not
 * GETs, and the backend pins that invariant over the whole route table.
 *
 * **There is no token here, deliberately.** `otp/verify` sets an HttpOnly cookie;
 * JavaScript cannot read it, so this module has nothing to store and nothing to
 * attach. Every later call rides the cookie same-origin through the nginx proxy,
 * which is why `api/client.ts`'s bearer interceptor is a no-op in this mode. If
 * you ever find yourself wanting a `getSessionToken()` here, the design has been
 * broken: the point of the cookie is that an XSS cannot exfiltrate the session.
 *
 * Because the SPA cannot read the cookie, it also cannot tell whether it holds a
 * valid session by inspection — `GET /me` is the only probe, and its 401 is the
 * signed-out signal.
 */

import { api } from '../api/client';
import type { MeResponse } from '../api/me';

/** HTTP status the backend uses for "not signed in" / "that code is not valid". */
const UNAUTHENTICATED = 401;

/**
 * Ask for a code.
 *
 * Resolves for an eligible address, an ineligible one, AND a throttled one — the
 * backend answers `{"status":"ok"}` for all three by design (anti-enumeration,
 * ADR 0032 decision 4). So a resolved promise means "the request was accepted",
 * NOT "mail was sent", and the UI must not claim otherwise. A rejection is a real
 * fault: mail transport down (502), OTP not enabled on this deployment (503), or
 * the per-IP limiter (429).
 */
export async function requestCode(email: string): Promise<void> {
  await api.post('/auth/otp/request', { email });
}

/**
 * Exchange a code for a session cookie; resolves with the same body as `GET /me`.
 *
 * A wrong code, an expired code, an already-used code and an out-of-attempts code
 * are ONE 401 with one message — the backend refuses to distinguish them, because
 * telling them apart would turn verify into the enumeration oracle that request
 * was built not to be. Callers must render the server's message rather than
 * inventing a more specific one.
 */
export async function verifyCode(email: string, code: string): Promise<MeResponse> {
  const { data } = await api.post<MeResponse>('/auth/otp/verify', { email, code });
  return data;
}

/**
 * Revoke the session server-side and clear the cookie.
 *
 * The endpoint is idempotent and NOT behind the auth dependency, so this also
 * works when the session already expired — which is the case that matters: a 401
 * here would leave a stale cookie in the browser forever.
 */
export async function endSession(): Promise<void> {
  await api.post('/auth/logout');
}

/**
 * Probe the current cookie: the user when signed in, `null` on a clean 401.
 *
 * Anything else (500, a network failure, a 429 from the shared unauthenticated
 * bucket) RE-THROWS. Collapsing those into `null` would paint the sign-in page
 * during an outage and invite the user to type a code at a server that cannot
 * check it — "signed out" is a specific server answer, not a fallback.
 */
export async function probeSession(): Promise<MeResponse | null> {
  try {
    const { data } = await api.get<MeResponse>('/me');
    return data;
  } catch (err) {
    if (statusOf(err) === UNAUTHENTICATED) return null;
    throw err;
  }
}

/** The HTTP status of an axios-shaped error, or undefined if it has none. */
export function statusOf(err: unknown): number | undefined {
  const response = (err as { response?: { status?: number } } | undefined)?.response;
  return response?.status;
}
