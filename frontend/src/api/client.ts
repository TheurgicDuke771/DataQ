import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios';

import { authConfig, authMode } from '../auth/config';
import { getMsalInstance } from '../auth/msalInstance';

/**
 * Shared axios instance for DataQ API calls.
 *
 * baseURL is relative (/api/v1); vite dev proxy forwards to the FastAPI
 * backend on :8000, and production same-origin deploy needs no CORS.
 *
 * Request interceptor attaches an Azure AD bearer token in real auth mode.
 * In dev_bypass / unconfigured modes the interceptor is a no-op (backend
 * dev-bypass resolves the user without a token).
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
  const instance = getMsalInstance();
  if (!instance) return config;
  const account = instance.getAllAccounts()[0];
  if (!account || !authConfig.apiScopeUri) return config;

  const result = await instance.acquireTokenSilent({
    account,
    scopes: [authConfig.apiScopeUri],
  });
  config.headers.set('Authorization', `Bearer ${result.accessToken}`);
  return config;
}
