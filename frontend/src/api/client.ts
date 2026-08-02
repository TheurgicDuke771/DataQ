import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';

import { getApiToken } from '../auth/authClient';
import { authMode } from '../auth/config';
import { notifySessionInvalidated } from '../auth/sessionEvents';

/**
 * Shared axios instance for DataQ API calls.
 *
 * baseURL is relative (/api/v1); vite dev proxy forwards to the FastAPI
 * backend on :8000, and production same-origin deploy needs no CORS.
 *
 * Request interceptor attaches the OIDC access token in real auth mode. In
 * dev_bypass / unconfigured modes the interceptor is a no-op (backend dev-bypass
 * resolves the user without a token). Silent renew and the interactive-redirect
 * fallback (when the session needs the user again — expired / revoked consent /
 * fresh MFA) live in getApiToken(); on that redirect the in-flight request is
 * aborted (rejected) and re-issues cleanly after the handshake (was #168).
 *
 * Response interceptor surfaces the DataQ error envelope's human message
 * (`{ error: { code, message, detail } }`) as `error.message`, so callers'
 * `err.message` shows the actionable backend reason instead of axios's generic
 * "Request failed with status code 4xx".
 */
export const api = axios.create({
  baseURL: '/api/v1',
});

api.interceptors.request.use(attachBearerToken);

/** Seconds to wait after a 429, read off an axios error — `undefined` if absent. */
export function retryAfterSeconds(error: unknown): number | undefined {
  const response = (error as AxiosError<RateLimitEnvelope>)?.response;
  if (response?.status !== 429) return undefined;
  // Prefer the envelope's own field; fall back to the standard header. Both are
  // emitted (ADR 0035), but the header is what survives a proxy rewriting the body.
  const fromBody = response.data?.error?.detail?.retry_after_seconds;
  const fromHeader = Number(response.headers?.['retry-after']);
  const seconds = typeof fromBody === 'number' ? fromBody : fromHeader;
  return Number.isFinite(seconds) && seconds >= 0 ? Math.ceil(seconds) : undefined;
}

interface RateLimitEnvelope {
  error?: { message?: string; detail?: { retry_after_seconds?: number } };
}

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<RateLimitEnvelope>) => {
    const apiMessage = error.response?.data?.error?.message;
    if (apiMessage) error.message = apiMessage;
    // A throttled user was told "Too many requests" and nothing else — no sense of
    // whether to retry now or in a minute (#788). The backend has always sent the
    // answer; nothing read it. Folded into the message so every existing call site
    // shows it without each one having to learn about rate limiting.
    const retryAfter = retryAfterSeconds(error);
    if (retryAfter !== undefined) {
      error.message = `${error.message} Try again in ${retryAfter}s.`;
    }
    if (isLostOtpSession(error)) notifySessionInvalidated();
    return Promise.reject(error);
  },
);

/**
 * A 401 that means "your session is gone", as opposed to one that is a normal
 * answer on the sign-in path (ADR 0032, #736).
 *
 * The exclusion is the whole point. `POST /auth/otp/verify` answers 401 for a
 * wrong code — treating that as session loss would knock the user back to the
 * email step every time they fat-finger a digit, destroying the code they were
 * mid-way through entering. `/auth/*` is where 401 is an expected outcome;
 * everywhere else it means the cookie expired, was revoked, or was cleared, and
 * the app must drop to the sign-in screen on the next navigation.
 *
 * Scoped to `otp` mode: an OIDC 401 belongs to the token layer (silent renew /
 * interactive redirect in `authClient.ts`), and dev-bypass has no session at all.
 */
function isLostOtpSession(error: AxiosError): boolean {
  if (authMode !== 'otp' || error.response?.status !== 401) return false;
  // `url` is what the caller passed (baseURL is applied later), so these are the
  // '/auth/...' paths as written in otpClient.ts.
  const url = error.config?.url ?? '';
  return !url.startsWith('/auth/');
}

async function attachBearerToken(
  config: InternalAxiosRequestConfig,
): Promise<InternalAxiosRequestConfig> {
  if (authMode !== 'real') return config;
  const token = await getApiToken();
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  return config;
}
