import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';

import { getApiToken } from '../auth/authClient';
import { authMode } from '../auth/config';

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

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ error?: { message?: string } }>) => {
    const apiMessage = error.response?.data?.error?.message;
    if (apiMessage) error.message = apiMessage;
    return Promise.reject(error);
  },
);

async function attachBearerToken(
  config: InternalAxiosRequestConfig,
): Promise<InternalAxiosRequestConfig> {
  if (authMode !== 'real') return config;
  const token = await getApiToken();
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  return config;
}
