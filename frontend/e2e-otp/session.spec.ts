import type { Cookie, Page } from '@playwright/test';

import { expect, freshEmail, SESSION_COOKIE, test } from './fixtures';

/**
 * What happens to a signed-in OTP browser when the session stops being valid
 * (ADR 0032, #736).
 *
 * This is the half a unit test cannot reach: the credential lives in a cookie the
 * SPA cannot read, so the *only* way the app learns its session died is a 401 on
 * the next request. Getting that wrong looks like an app that keeps rendering an
 * authenticated shell over a dead session until the tab is reloaded.
 *
 * The first two tests look similar and are not: a full page load re-mounts the app
 * and re-runs the `GET /me` PROBE, while an in-app click never remounts anything
 * and can only be caught by the 401 EVENT. Both were mutation-checked — deleting
 * the event wiring leaves the first passing and fails the second, which is exactly
 * why both are here.
 */

test('a session revoked server-side drops the app to sign-in on the next navigation', async ({
  page,
  signIn,
}) => {
  const email = freshEmail('revoked');
  await page.goto('/');
  await signIn.complete(email);

  const session = await readSessionCookie(page);

  // Revoke it server-side, exactly as a `POST /auth/logout` from another device
  // (or an admin action) would. `page.request` shares the browser context's
  // cookies, so this is the real revocation path — not a fabricated 401.
  await revokeServerSide(page, session);

  // …then put the cookie BACK, so the browser still presents a credential the
  // server has already destroyed. Without this step the test would only prove
  // "no cookie → signed out", which is a much weaker claim: the interesting case
  // is a browser that still believes it is signed in.
  await page.context().addCookies([session]);
  await expect
    .poll(async () => (await page.context().cookies()).some((c) => c.name === SESSION_COOKIE))
    .toBe(true);

  await page.goto('/suites');
  await expect(page.getByLabel('Email address')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Suites', level: 3 })).toHaveCount(0);
});

test('a revoked session drops the app WITHOUT a navigation, on the next API 401', async ({
  page,
  signIn,
}) => {
  // The nastier variant of the case above: the user is sitting on a page and the
  // session dies under them. The app must fall back to sign-in when a background
  // request 401s, not wait for a reload.
  const email = freshEmail('midsession');
  await page.goto('/');
  await signIn.complete(email);

  const session = await readSessionCookie(page);
  await revokeServerSide(page, session);
  await page.context().addCookies([session]);

  // A same-page action that hits the API. Client-side routing only — no reload.
  await page.getByRole('link', { name: 'Suites' }).click();
  await expect(page.getByLabel('Email address')).toBeVisible();
});

test('signing out revokes the session and the back button cannot undo it', async ({
  page,
  signIn,
}) => {
  const email = freshEmail('signout');
  await page.goto('/');
  await signIn.complete(email);

  await page.getByText(email, { exact: true }).first().click();
  await page.getByText('Sign out', { exact: true }).click();
  await expect(page.getByLabel('Email address')).toBeVisible();

  // The cookie is gone AND the session is dead server-side — going "back" must
  // not restore an authenticated view.
  await page.goBack();
  await page.goto('/dashboard');
  await expect(page.getByLabel('Email address')).toBeVisible();
});

test('a rejected code leaves the form exactly where it was', async ({ page, signIn }) => {
  // NOTE ON WHAT THIS DOES AND DOES NOT PROVE. It asserts the user-visible
  // behaviour: a 401 from verify leaves you on the code step with the address
  // intact. It is NOT a guard for the `/auth/*` exclusion in the axios 401
  // handler — this spec was checked against that mutation and passed, because a
  // wrong-code 401 can only occur while the session provider is already
  // signed-out, so the spurious event is a no-op. That exclusion is pinned by the
  // unit test in tests/api/client.test.ts, which does fail without it.
  const email = freshEmail('nolose');
  await page.goto('/');
  await signIn.requestCode(email);
  await signIn.submitCode('999999');

  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByLabel('Sign-in code')).toBeVisible();
  await expect(page.getByLabel('Email address')).toHaveCount(0);
});

/** The session cookie, failing the test loudly if sign-in did not set one. */
async function readSessionCookie(page: Page) {
  const session = (await page.context().cookies()).find((c) => c.name === SESSION_COOKIE);
  expect(session, 'expected a dataq_session cookie after sign-in').toBeDefined();
  // Narrowed by the assertion above; `expect` does not narrow for TypeScript.
  if (!session) throw new Error('unreachable');
  return session;
}

/**
 * Revoke `session` server-side and don't return until the revocation is
 * OBSERVABLE — not just inferred from the 204 (#1160).
 *
 * `session_service.revoke()` commits before the `204` is sent, so in
 * practice this resolves on the very first poll; it exists so these specs
 * never take the server's word for it and drive the UI against a revocation
 * that might not have landed yet. `page.request.post` shares the browser
 * context's cookies, so this is the real revocation path — not a fabricated
 * 401 — and the poll below presents the (now dead) token directly via a
 * `Cookie` header, independent of whatever the context's own cookie jar
 * currently holds (the `logout` response's `Set-Cookie` already cleared it).
 */
async function revokeServerSide(page: Page, session: Cookie): Promise<void> {
  const logout = await page.request.post('/api/v1/auth/logout');
  expect(logout.status()).toBe(204);

  await expect
    .poll(
      async () => {
        const res = await page.request.get('/api/v1/me', {
          headers: { Cookie: `${SESSION_COOKIE}=${session.value}` },
        });
        return res.status();
      },
      { message: 'revoked session token still authenticates GET /me' },
    )
    .toBe(401);
}
