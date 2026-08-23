import { ErrorResponse, UserManager, WebStorageStateStore, type User } from 'oidc-client-ts';

import { authConfig, authMode } from './config';

// OAuth error codes from a failed silent renew that genuinely need the user back at the IdP
// (expired session / dead refresh token / fresh consent or MFA).
const INTERACTION_REQUIRED_ERRORS = new Set([
  'login_required',
  'interaction_required',
  'consent_required',
  'account_selection_required',
  'invalid_grant',
]);

/** Generic OIDC auth client (ADR 0028 / #504) — replaces the Azure-specific MSAL client. */

let _mgr: UserManager | null = null;

/** The UserManager singleton in real auth mode; null in dev_bypass / unconfigured. */
export function getUserManager(): UserManager | null {
  if (authMode !== 'real') return null;
  if (_mgr) return _mgr;

  const { authority, clientId, apiScope, scope: scopeOverride } = authConfig;
  if (!authority || !clientId) {
    // authMode='real' guarantees both are set; defensive guard satisfies the type checker.
    throw new Error(
      'Real auth mode requires an authority + clientId (DATAQ_AUTH_* runtime config)',
    );
  }

  // openid/profile/email → id token; offline_access → refresh token for silent renew; apiScope
  // (when set) makes the access token audience the DataQ API.
  const scope =
    scopeOverride ||
    ['openid', 'profile', 'email', 'offline_access', apiScope].filter(Boolean).join(' ');

  _mgr = new UserManager({
    authority,
    client_id: clientId,
    // Trailing slash matches the registered SPA redirect URI (Azure AD requires a
    // trailing slash when the URI has no path segment — see deploy/terraform/azure/sso.tf).
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
  if (!mgr) return;
  if (authConfig.logoutStyle === 'cognito') {
    // Cognito's /logout is not RP-Initiated-Logout-conformant (#1364): it needs client_id +
    // logout_uri (exactly matching a registered logout URL) and 400s "Client does not exist" on
    // the standard id_token_hint / post_logout_redirect_uri that signoutRedirect sends — leaving
    // the user on a raw Cognito error page with the hosted-UI session still alive (a sign-in right
    // after silently re-authenticates the old user).
    await mgr.removeUser();
    let endSession: string | undefined;
    try {
      endSession = await mgr.metadataService.getEndSessionEndpoint();
    } catch (err) {
      console.error('OIDC end-session discovery failed; local session cleared only', err);
      return;
    }
    if (!endSession) return; // nothing discoverable to redirect to; local session is gone
    const url = new URL(endSession);
    url.searchParams.set('client_id', authConfig.clientId ?? '');
    url.searchParams.set('logout_uri', `${window.location.origin}/`);
    window.location.assign(url.toString());
    return;
  }
  await mgr.signoutRedirect();
}

/** True when the current URL is the IdP redirect back. */
function isSigninRedirect(): boolean {
  const params = new URLSearchParams(window.location.search);
  return params.has('state') && (params.has('code') || params.has('error'));
}

/**
 * Bootstrap step: if this load is the sign-in redirect back, complete the code exchange and scrub
 * the redirect params from the URL (so a reload can't replay them).
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

/** A currently-valid API access token, or null when not signed in. */
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
