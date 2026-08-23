import type { Cookie, Page } from '@playwright/test';

import { expect, freshEmail, SESSION_COOKIE, test } from './fixtures';

/** What happens to a signed-in OTP browser when the session stops being valid (ADR 0032, #736). */

test('a session revoked server-side drops the app to sign-in on the next navigation', async ({
  page,
  signIn,
}) => {
  const email = freshEmail('revoked');
  await page.goto('/');
  await signIn.complete(email);

  const session = await readSessionCookie(page);

  // Revoke it server-side, exactly as a `POST /auth/logout` from another device (or an admin
  // action) would.
  await revokeServerSide(page, session);

  // …then put the cookie BACK, so the browser still presents a credential the server has already
  // destroyed.
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
  // The nastier variant of the case above: the user is sitting on a page and the session dies under
  // them.
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
  // NOTE ON WHAT THIS DOES AND DOES NOT PROVE.
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
 * Revoke `session` server-side and don't return until the revocation is OBSERVABLE — not just
 * inferred from the 204 (#1160).
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
