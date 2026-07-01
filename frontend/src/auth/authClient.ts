import { ErrorResponse, UserManager, WebStorageStateStore, type User } from 'oidc-client-ts';

import { authConfig, authMode } from './config';

// OAuth error codes from a failed silent renew that genuinely need the user back
// at the IdP (expired session / dead refresh token / fresh consent or MFA) — as
// opposed to a transient network error, which must NOT trigger a redirect (#168).
const INTERACTION_REQUIRED_ERRORS = new Set([
  'login_required',
  'interaction_required',
  'consent_required',
  'account_selection_required',
  'invalid_grant',
]);

/**
 * Generic OIDC auth client (ADR 0028 / #504) — replaces the Azure-specific MSAL
 * client. Any standards-compliant IdP works: Azure AD, Cognito, GCP Identity
 * Platform, Keycloak, or a local dev OIDC — the provider is just the injected
 * `authority`. Authorization-code + PKCE (oidc-client-ts default), refresh-token
 * silent renew (offline_access), session-storage cache (matches the old MSAL
 * choice so a tab reload keeps the session but a closed tab drops it).
 */

let _mgr: UserManager | null = null;

/**
 * The UserManager singleton in real auth mode; null in dev_bypass / unconfigured.
 * Caller awaits nothing to construct it — oidc-client-ts fetches the IdP metadata
 * lazily on first sign-in/callback.
 */
export function getUserManager(): UserManager | null {
  if (authMode !== 'real') return null;
  if (_mgr) return _mgr;

  const { authority, clientId, apiScope } = authConfig;
  if (!authority || !clientId) {
    // authMode='real' guarantees both are set; defensive guard satisfies the type checker.
    throw new Error(
      'Real auth mode requires an authority + clientId (DATAQ_AUTH_* runtime config)',
    );
  }

  // openid/profile/email → id token; offline_access → refresh token for silent
  // renew; apiScope (when set) makes the access token audience the DataQ API.
  const scope = ['openid', 'profile', 'email', 'offline_access', apiScope]
    .filter(Boolean)
    .join(' ');

  _mgr = new UserManager({
    authority,
    client_id: clientId,
    // Trailing slash matches the registered SPA redirect URI (Azure AD requires a
    // trailing slash when the URI has no path segment — see deploy/terraform/sso.tf).
    redirect_uri: `${window.location.origin}/`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    scope,
    automaticSilentRenew: true,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  });
  return _mgr;
}

/** Begin an interactive sign-in (full-page redirect to the IdP). */
export async function login(): Promise<void> {
  const mgr = getUserManager();
  if (mgr) await mgr.signinRedirect();
}

/** End the session (redirect to the IdP's logout, then back). */
export async function logout(): Promise<void> {
  const mgr = getUserManager();
  if (mgr) await mgr.signoutRedirect();
}

/**
 * True when the current URL is the IdP redirect back. `state` is always present;
 * a success carries `code`, a failure (cancelled consent / MFA, denied) carries
 * `error` — both must be processed (the error case was a silent no-op before).
 */
function isSigninRedirect(): boolean {
  const params = new URLSearchParams(window.location.search);
  return params.has('state') && (params.has('code') || params.has('error'));
}

/**
 * Bootstrap step: if this load is the sign-in redirect back, complete the code
 * exchange and scrub the redirect params from the URL (so a reload can't replay
 * them). No-op otherwise. Must run before React renders so the first paint is
 * post-login. On an IdP error redirect (or a failed exchange), log and fall
 * through — AuthGate then shows the sign-in page so the user can retry, rather
 * than crashing the app.
 */
export async function completeSigninIfCallback(): Promise<void> {
  const mgr = getUserManager();
  if (!mgr || !isSigninRedirect()) return;
  try {
    await mgr.signinRedirectCallback();
  } catch (err) {
    console.error('OIDC sign-in did not complete', err);
  } finally {
    window.history.replaceState({}, document.title, window.location.pathname);
  }
}

let _inflightToken: Promise<string | null> | null = null;

/**
 * A currently-valid API access token, or null when not signed in. Refreshes
 * silently when the cached token is expired; if that needs user interaction
 * (expired session / revoked consent / fresh MFA), falls back to an interactive
 * redirect and rejects so the in-flight request doesn't fire tokenless — it
 * re-issues cleanly once the redirect completes (was #168). Concurrent callers
 * share one in-flight acquisition (single-flight), so a dashboard mounting N
 * requests at once triggers at most one silent renew / one redirect.
 */
export function getApiToken(): Promise<string | null> {
  if (_inflightToken) return _inflightToken;
  _inflightToken = acquireApiToken().finally(() => {
    _inflightToken = null;
  });
  return _inflightToken;
}

async function acquireApiToken(): Promise<string | null> {
  const mgr = getUserManager();
  if (!mgr) return null;
  const user = await mgr.getUser();
  // Not signed in → no token; the request 401s quietly (AuthGate gates the UI).
  // Do NOT signinSilent here: with no session it hits the iframe path and, with
  // no silent_redirect_uri configured, throws — or worse, redirects mid-request.
  if (!user) return null;
  if (!user.expired) return user.access_token;
  try {
    const renewed = await mgr.signinSilent();
    return renewed?.access_token ?? null;
  } catch (err) {
    // Only hand off to an interactive redirect when the silent renew actually
    // needs the user; transient errors re-throw untouched (no spurious redirect).
    if (err instanceof ErrorResponse && INTERACTION_REQUIRED_ERRORS.has(err.error ?? '')) {
      await mgr.signinRedirect();
    }
    throw err;
  }
}

/** Test-only: drop the cached UserManager so the next call rebuilds it. */
export function resetAuthClientCache(): void {
  _mgr = null;
}

export type { User };
