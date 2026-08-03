import { expect, test as base, type Page } from '@playwright/test';

/**
 * Shared plumbing for the email-OTP browser lane (ADR 0032, #736).
 *
 * Two things every spec needs:
 *
 * 1. **The auth mode**, injected as `window.__DATAQ_CONFIG__` before any page
 *    script runs. That is the *production* contract — nginx renders exactly this
 *    global from `DATAQ_AUTH_*` env (ADR 0028) — so the lane exercises the shipped
 *    runtime-config path instead of a build-time flag that only tests use.
 *
 * 2. **The code**, read back from the local SMTP sink
 *    (`backend/scripts/e2e_otp_smtp_sink.py`). The api performs a real
 *    STARTTLS + AUTH + `send_message` against it, so the mailer under test is the
 *    production one — nothing is stubbed on the app side of the wire.
 */

/** Where the sink's capture API lives (see scripts/e2e-otp-stack.sh). */
const SINK_URL = process.env.E2E_OTP_SINK_URL || 'http://127.0.0.1:1080';

/**
 * The signup-allowlisted domain the lane's api is configured with.
 *
 * Specs mint a UNIQUE address each, which matters: the per-mailbox request cap
 * (`AUTH_OTP_REQUEST_PER_EMAIL_PER_10MIN`, default 3) is active even when the
 * rate-limit middleware is off — by design, since a mail-bomb control a test
 * harness switches off is not a control. Sharing one address across specs would
 * exhaust it and produce failures that look like bugs in the UI.
 */
export const OTP_DOMAIN = 'dataq.local';

/** The address `WORKSPACE_ADMIN_EMAILS` names on the lane's api. */
export const OTP_ADMIN_EMAIL = `otp-admin@${OTP_DOMAIN}`;

/** A unique allow-listed address for one spec. */
export function freshEmail(label: string): string {
  return `otp-${label}-${Date.now().toString(36)}@${OTP_DOMAIN}`;
}

export const test = base.extend<{ signIn: SignIn }>({
  page: async ({ page }, use) => {
    await page.addInitScript(() => {
      (window as unknown as { __DATAQ_CONFIG__: unknown }).__DATAQ_CONFIG__ = {
        auth: { mode: 'otp' },
      };
    });
    await use(page);
  },
  signIn: async ({ page }, use) => {
    await use(makeSignIn(page));
  },
});

export { expect };

export interface SignIn {
  /** Step 1 only: submit the address and land on the code step. */
  requestCode: (email: string) => Promise<void>;
  /** The latest code the sink captured for this address (polls until it lands). */
  readCode: (email: string) => Promise<string>;
  /** The latest code, once it differs from `previous` — for resend/supersede. */
  readNewCode: (email: string, previous: string) => Promise<string>;
  /** Step 2 only: type a code and submit. */
  submitCode: (code: string) => Promise<void>;
  /**
   * The whole flow, ending inside the app. Dismisses the first-login
   * profile-completion prompt (#1139) by default — pass
   * `dismissProfilePrompt: false` for a spec that asserts on the sign-in
   * flow's OWN footprint (e.g. storage cleanliness) and must not pick up the
   * dismissal's own `sessionStorage` write as a false positive.
   */
  complete: (email: string, opts?: { dismissProfilePrompt?: boolean }) => Promise<string>;
}

function makeSignIn(page: Page): SignIn {
  const requestCode = async (email: string) => {
    await page.getByLabel('Email address').fill(email);
    await page.getByRole('button', { name: /send code/i }).click();
    await expect(page.getByLabel('Sign-in code')).toBeVisible();
  };

  /** The sink's newest captured code for `email`, or '' if nothing yet. */
  const peek = async (email: string): Promise<string> => {
    const res = await page.request.get(`${SINK_URL}/code?email=${encodeURIComponent(email)}`);
    if (!res.ok()) return '';
    return ((await res.json()) as { code: string }).code;
  };

  const readCode = async (email: string) => {
    let code = '';
    // Poll rather than sleep: the send is synchronous on the request path, but
    // the sink writes on its own thread, so there is a small real race.
    await expect
      .poll(
        async () => {
          code = await peek(email);
          return code;
        },
        { message: `no OTP code reached the sink for ${email}`, timeout: 15_000 },
      )
      .toMatch(/^\d{6}$/);
    return code;
  };

  const readNewCode = async (email: string, previous: string) => {
    let code = '';
    await expect
      .poll(
        async () => {
          code = await peek(email);
          return code;
        },
        { message: `no NEW code reached the sink for ${email}`, timeout: 15_000 },
      )
      .not.toBe(previous);
    return code;
  };

  const submitCode = async (code: string) => {
    await page.getByLabel('Sign-in code').fill(code);
    await page.getByRole('button', { name: /verify and sign in/i }).click();
  };

  return {
    requestCode,
    readCode,
    readNewCode,
    submitCode,
    complete: async (email: string, opts?: { dismissProfilePrompt?: boolean }) => {
      const dismissProfilePrompt = opts?.dismissProfilePrompt ?? true;
      await requestCode(email);
      const code = await readCode(email);
      await submitCode(code);

      // The app shell is the proof: AuthGate only renders children for a
      // resolved session.
      await expect(page.getByRole('link', { name: 'Connections' }).first()).toBeVisible();

      if (!dismissProfilePrompt) return code;

      // Every fresh OTP signup starts with display_name: NULL, so the
      // first-login profile-completion prompt (#1139) fires here for every
      // freshEmail() address and for the fixed OTP_ADMIN_EMAIL the first
      // time it signs in during a run, and its mask intercepts pointer
      // events over the nav, so leaving it up breaks every spec's next
      // click. It depends on a SEPARATE `/me` fetch (MeProvider) than the
      // one the OTP verify response already carries, so it can render a
      // beat after the shell above.
      //
      // A page.waitForResponse() sync point on that fetch hung outright
      // (#1153). A single waitFor(visible)+click+waitFor(hidden) with no
      // sub-budget (#1160 round 1) does NOT hang on visibility — CI
      // artifacts from round 2 (#1161, run 30786514095) show all three
      // session specs reaching the SAME point every time: the prompt
      // renders, gets clicked, and then `waitFor({state:'hidden'})` polls
      // 177 times over 90s with the SAME button reported visible throughout
      // — i.e. the click does not reliably close the modal on a shared CI
      // runner. Retrying the click (not just the wait) is the fix that
      // matches the evidence: `expect(...).toPass()` re-runs the whole
      // click-then-assert body on a poll interval until the modal is
      // OBSERVABLY gone, so a click that lands on a stale/about-to-be-
      // replaced node gets a fresh attempt against the CURRENT DOM instead
      // of the test waiting forever on a locator that only ever resolves
      // the untouched original.
      const skipPrompt = page.getByRole('button', { name: 'Skip for now' });
      await skipPrompt.waitFor({ state: 'visible' });
      // NOTE: clicking "Skip for now" writes
      // `dataq:profileCompletionPrompt:skipped` to sessionStorage (by
      // design — see the component) — a spec asserting on the sign-in
      // flow's OWN storage footprint must pass `dismissProfilePrompt:
      // false` instead of dismissing and then filtering the key back out,
      // so the assertion still proves what its name says. Re-clicking is
      // idempotent (dismiss() just re-sets the same two things), so a
      // retry that lands on an ALREADY-dismissed button is harmless. The
      // inner assertion's OWN timeout is short and deliberate: it's what
      // makes toPass() re-click every ~500ms instead of waiting a full
      // expect.timeout per attempt, which matters because the failure
      // window this is defending against (a stale DOM node) looks like it
      // is measured in a beat, not seconds.
      await expect(async () => {
        await skipPrompt.click();
        await expect(skipPrompt).toBeHidden({ timeout: 500 });
      }).toPass();
      return code;
    },
  };
}

/** The DataQ session cookie name (`session_service.COOKIE_NAME`). */
export const SESSION_COOKIE = 'dataq_session';
