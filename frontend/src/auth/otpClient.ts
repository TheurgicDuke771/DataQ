/** Email-OTP sign-in transport (ADR 0032, #736). */

import { api } from '../api/client';
import type { MeResponse } from '../api/me';

/** HTTP status the backend uses for "not signed in" / "that code is not valid". */
const UNAUTHENTICATED = 401;

/** Ask for a code. */
export async function requestCode(email: string): Promise<void> {
  await api.post('/auth/otp/request', { email });
}

/** Exchange a code for a session cookie; resolves with the same body as `GET /me`. */
export async function verifyCode(email: string, code: string): Promise<MeResponse> {
  const { data } = await api.post<MeResponse>('/auth/otp/verify', { email, code });
  return data;
}

/** Revoke the session server-side and clear the cookie. */
export async function endSession(): Promise<void> {
  await api.post('/auth/logout');
}

/** Probe the current cookie: the user when signed in, `null` on a clean 401. */
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
