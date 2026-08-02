import { expect, freshEmail, OTP_ADMIN_EMAIL, test } from './fixtures';

/**
 * Email-OTP sign-in, end to end in a real browser (ADR 0032, #736).
 *
 * Everything below the browser is production code: a real api in `otp` mode, a
 * real `OtpMailer` doing a real STARTTLS + AUTH submission, a real `Set-Cookie`.
 * The only test-owned pieces are the SMTP server that catches the mail and the
 * `window.__DATAQ_CONFIG__` injection — which is itself the production contract.
 */

test('signs in with a mailed code and lands in the app', async ({ page, signIn }) => {
  const email = freshEmail('happy');
  await page.goto('/');

  // Signed out → the code form, not the app.
  await expect(page.getByLabel('Email address')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Connections' })).toHaveCount(0);

  await signIn.requestCode(email);
  // The acknowledgement is CONDITIONAL — it must never confirm that mail was
  // sent, because the api answers identically for an address that cannot sign in.
  await expect(page.getByText(/can sign in to this workspace/i)).toBeVisible();

  const code = await signIn.readCode(email);
  await signIn.submitCode(code);

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('img', { name: 'DataQ logo' })).toBeVisible();
  // The identity the api provisioned from the mailbox, in the header.
  await expect(page.getByText(email, { exact: true }).first()).toBeVisible();
});

test('keeps no token in JS-readable storage — the session is the cookie only', async ({
  page,
  signIn,
}) => {
  // ADR 0032 decision 3's central property, asserted in a real browser rather
  // than inferred from the source: an XSS must find nothing to exfiltrate.
  await page.goto('/');
  await signIn.complete(freshEmail('storage'));

  const stored = await page.evaluate(() => ({
    local: Object.entries({ ...window.localStorage }),
    session: Object.entries({ ...window.sessionStorage }),
  }));
  expect(stored.local).toEqual([]);
  expect(stored.session).toEqual([]);

  // …and the cookie that DOES exist is invisible to script.
  const viaScript = await page.evaluate(() => document.cookie);
  expect(viaScript).not.toContain('dq_sess_');
  const cookies = await page.context().cookies();
  const session = cookies.find((c) => c.name === 'dataq_session');
  expect(session, 'expected the api to have set dataq_session').toBeTruthy();
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe('Lax');
});

test('rejects a wrong code without losing the address', async ({ page, signIn }) => {
  const email = freshEmail('wrong');
  await page.goto('/');
  await signIn.requestCode(email);
  await signIn.submitCode('000000');

  await expect(page.getByRole('alert')).toContainText(/not valid/i);
  // Still on the code step: forcing a re-type of the email after every mistyped
  // digit is the thing that makes these forms hateful.
  await expect(page.getByLabel('Sign-in code')).toBeVisible();
  await expect(page.getByText(email, { exact: false }).first()).toBeVisible();

  // …and the right code still works afterwards (the failure consumed an attempt,
  // not the code).
  const code = await signIn.readCode(email);
  await signIn.submitCode(code);
  await expect(page).toHaveURL(/\/dashboard$/);
});

test('rejects a superseded code — a new request kills the previous one', async ({
  page,
  signIn,
}) => {
  // This is the reachable, deterministic form of "the code is no longer valid".
  //
  // A genuinely TIME-expired code would need a 10-minute wait or a clock hack, and
  // the api answers a single uniform 401 for wrong / expired / already-used /
  // out-of-attempts — so at the boundary this exercises the same response and the
  // same UI. What it additionally proves, which a plain wrong-code case cannot, is
  // that re-requesting really does invalidate the earlier code server-side
  // (ADR 0032 decision 4). The TTL arithmetic itself is backend-tested (#1134).
  const email = freshEmail('superseded');
  await page.goto('/');
  await signIn.requestCode(email);
  const first = await signIn.readCode(email);

  // Resend is cooldown-gated, so drive the second request from the email step.
  await page.getByRole('button', { name: /use a different address/i }).click();
  await signIn.requestCode(email);
  await signIn.readNewCode(email, first);

  await signIn.submitCode(first);
  await expect(page.getByRole('alert')).toContainText(/not valid/i);
  await expect(page).not.toHaveURL(/\/dashboard$/);
});

test('answers identically for an address that cannot sign in (anti-enumeration)', async ({
  page,
  signIn,
}) => {
  // The whole point of ADR 0032 decision 4: nothing a stranger can see here
  // distinguishes an allow-listed mailbox from one that is not.
  await page.goto('/');
  await signIn.requestCode('stranger@example.invalid');
  await expect(page.getByText(/can sign in to this workspace/i)).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByLabel('Sign-in code')).toBeVisible();
});

test('a deep link while signed out lands on sign-in, not on the page', async ({ page }) => {
  await page.goto('/results');
  await expect(page.getByLabel('Email address')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Results', level: 3 })).toHaveCount(0);
});

test('an OTP user gets the same role-gated nav as any other identity', async ({ page, signIn }) => {
  // Issue AC 4: `useMe`/`MeProvider` are untouched by this mode, so the
  // admin-nav gating must work off `/me` exactly as it does under SSO. The lane's
  // api names this address in WORKSPACE_ADMIN_EMAILS.
  await page.goto('/');
  await signIn.complete(OTP_ADMIN_EMAIL);
  await expect(page.getByRole('link', { name: 'Admin' })).toBeVisible();

  await page.getByRole('link', { name: 'Profile' }).click();
  await expect(page.getByRole('heading', { name: 'Profile', level: 3 })).toBeVisible();
  // The Profile page names the mode it is actually running in (#618).
  await expect(page.getByText('Email one-time code')).toBeVisible();
});
