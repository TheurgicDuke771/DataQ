import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';

/**
 * The two-step email-code sign-in screen (ADR 0032, #736).
 *
 * The properties worth pinning here are mostly about *not lying*:
 *
 * - the send acknowledgement must stay conditional ("if this address can sign
 *   in"), because a confident "we sent you a code" re-opens the enumeration
 *   channel the backend's uniform response closed;
 * - the failure copy must be the SERVER's, because the backend collapses wrong /
 *   expired / used / out-of-attempts into one 401 on purpose and the SPA cannot
 *   tell them apart;
 * - nothing may be written to JS-readable storage, ever.
 */

const requestCode = vi.fn();
const verifyCode = vi.fn();

beforeEach(() => {
  vi.resetModules();
  requestCode.mockReset();
  verifyCode.mockReset();
  requestCode.mockResolvedValue(undefined);
  vi.doMock('../../src/auth/otpClient', async () => {
    const actual = await vi.importActual<typeof import('../../src/auth/otpClient')>(
      '../../src/auth/otpClient',
    );
    return { ...actual, requestCode, verifyCode };
  });
});

afterEach(() => {
  vi.doUnmock('../../src/auth/otpClient');
  vi.useRealTimers();
});

const ME: MeResponse = {
  id: 'u-1',
  aad_object_id: null,
  email: 'ada@acme.io',
  display_name: 'Ada L',
  last_seen_at: null,
  is_workspace_admin: false,
};

const httpError = (status: number, message: string) =>
  Object.assign(new Error(message), { response: { status } });

async function renderPage(onSignedIn = vi.fn(), cooldownSeconds?: number) {
  const { OtpSignInPage } = await import('../../src/auth/OtpSignInPage');
  render(<OtpSignInPage onSignedIn={onSignedIn} cooldownSeconds={cooldownSeconds} />);
  return onSignedIn;
}

/** Step 1: type the address and submit. */
async function submitEmail(user: ReturnType<typeof userEvent.setup>, email = 'ada@acme.io') {
  await user.type(screen.getByLabelText('Email address'), email);
  await user.click(screen.getByRole('button', { name: /send code/i }));
  await screen.findByLabelText('Sign-in code');
}

describe('step 1 — email', () => {
  it('starts on the email step with the code step not yet rendered', async () => {
    await renderPage();
    expect(screen.getByLabelText('Email address')).toBeInTheDocument();
    expect(screen.queryByLabelText('Sign-in code')).not.toBeInTheDocument();
  });

  it('cannot be submitted empty', async () => {
    await renderPage();
    expect(screen.getByRole('button', { name: /send code/i })).toBeDisabled();
  });

  it('sends the trimmed address and advances to the code step', async () => {
    const user = userEvent.setup();
    await renderPage();
    await user.type(screen.getByLabelText('Email address'), '  ada@acme.io  ');
    await user.click(screen.getByRole('button', { name: /send code/i }));
    await waitFor(() => expect(requestCode).toHaveBeenCalledWith('ada@acme.io'));
    expect(await screen.findByLabelText('Sign-in code')).toBeInTheDocument();
  });

  it('acknowledges CONDITIONALLY — never claims mail was sent', async () => {
    const user = userEvent.setup();
    await renderPage();
    await submitEmail(user);
    // The exact anti-enumeration property: the copy is identical for an eligible,
    // an ineligible and a throttled address because the response is.
    expect(screen.getByText(/can sign in to this workspace/i)).toBeInTheDocument();
    expect(screen.queryByText(/we sent you/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/check your email/i)).not.toBeInTheDocument();
  });

  it('shows the server error and STAYS on the email step when the mailer is down', async () => {
    const user = userEvent.setup();
    requestCode.mockRejectedValue(httpError(502, 'Could not send the sign-in code: no response.'));
    await renderPage();
    await user.type(screen.getByLabelText('Email address'), 'ada@acme.io');
    await user.click(screen.getByRole('button', { name: /send code/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not send the sign-in code/);
    // Advancing to a code step nobody can complete would be a dead end.
    expect(screen.queryByLabelText('Sign-in code')).not.toBeInTheDocument();
  });

  it('reports OTP-not-enabled (503) as the deployment problem it is', async () => {
    const user = userEvent.setup();
    requestCode.mockRejectedValue(
      httpError(503, 'Email sign-in is not enabled on this deployment.'),
    );
    await renderPage();
    await user.type(screen.getByLabelText('Email address'), 'ada@acme.io');
    await user.click(screen.getByRole('button', { name: /send code/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/not enabled on this deployment/);
  });

  it('falls back to its own copy — with the status — when the error carries no message', async () => {
    const user = userEvent.setup();
    requestCode.mockRejectedValue(httpError(502, 'Request failed with status code 502'));
    await renderPage();
    await user.type(screen.getByLabelText('Email address'), 'ada@acme.io');
    await user.click(screen.getByRole('button', { name: /send code/i }));
    // "Request failed with status code 502" is axios boilerplate, not an answer.
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Could not request a sign-in code/);
    expect(alert).toHaveTextContent(/HTTP 502/);
  });
});

describe('step 2 — code', () => {
  it('verifies and hands the /me body to the caller', async () => {
    const user = userEvent.setup();
    verifyCode.mockResolvedValue(ME);
    const onSignedIn = await renderPage();
    await submitEmail(user);

    await user.type(screen.getByLabelText('Sign-in code'), '123456');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));
    await waitFor(() => expect(verifyCode).toHaveBeenCalledWith('ada@acme.io', '123456'));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(ME));
  });

  it('shows the server’s uniform 401 message and KEEPS the address', async () => {
    const user = userEvent.setup();
    verifyCode.mockRejectedValue(
      httpError(401, 'That sign-in code is not valid. Request a new one.'),
    );
    await renderPage();
    await submitEmail(user);

    await user.type(screen.getByLabelText('Sign-in code'), '000000');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/not valid/);
    // Still on the code step, address intact — retyping the email after every
    // mistyped digit is what makes these forms hateful.
    expect(screen.getByLabelText('Sign-in code')).toBeInTheDocument();
    expect(screen.getByText(/ada@acme\.io/)).toBeInTheDocument();
    // …and the wrong code is cleared so the next attempt starts clean.
    await waitFor(() => expect(screen.getByLabelText('Sign-in code')).toHaveValue(''));
  });

  it('renders an EXPIRED code identically to a wrong one — no invented distinction', async () => {
    // The backend returns ONE message for wrong / expired / used / out-of-attempts,
    // deliberately (distinguishing them would be an enumeration oracle). This test
    // exists to stop a well-meaning "your code expired" branch being added here:
    // the SPA has no way to know that, and claiming it would be a fabrication.
    const user = userEvent.setup();
    verifyCode.mockRejectedValue(
      httpError(401, 'That sign-in code is not valid. Request a new one.'),
    );
    await renderPage();
    await submitEmail(user);
    await user.type(screen.getByLabelText('Sign-in code'), '654321');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('That sign-in code is not valid. Request a new one.');
    expect(alert).not.toHaveTextContent(/expired/i);
    expect(alert).not.toHaveTextContent(/attempts/i);
  });

  it('cannot be submitted empty', async () => {
    const user = userEvent.setup();
    await renderPage();
    await submitEmail(user);
    expect(screen.getByRole('button', { name: /verify and sign in/i })).toBeDisabled();
  });

  it('goes back to the email step and clears the code on "Use a different address"', async () => {
    const user = userEvent.setup();
    await renderPage();
    await submitEmail(user);
    await user.type(screen.getByLabelText('Sign-in code'), '123');

    await user.click(screen.getByRole('button', { name: /use a different address/i }));
    expect(screen.getByLabelText('Email address')).toBeInTheDocument();
    expect(screen.queryByLabelText('Sign-in code')).not.toBeInTheDocument();
  });
});

describe('resend + cooldown', () => {
  it('is disabled immediately after a send and counts down', async () => {
    const user = userEvent.setup();
    await renderPage();
    await submitEmail(user);
    const resend = screen.getByRole('button', { name: /resend code in \d+s/i });
    expect(resend).toBeDisabled();
    expect(resend).toHaveTextContent(/30s/);
  });

  it('re-arms once the cooldown elapses, and a resend clears the stale code', async () => {
    // Real timers on a 1-second cooldown, not fake ones: faking timers around the
    // in-flight submit promise deadlocked the test (the click never settled).
    const user = userEvent.setup();
    await renderPage(vi.fn(), 1);
    await submitEmail(user);
    await user.type(screen.getByLabelText('Sign-in code'), '111111');
    expect(screen.getByRole('button', { name: /resend code in 1s/i })).toBeDisabled();

    const resend = await screen.findByRole('button', { name: /^resend code$/i }, { timeout: 3000 });
    expect(resend).toBeEnabled();
    await user.click(resend);
    await waitFor(() => expect(requestCode).toHaveBeenCalledTimes(2));
    // A resend invalidates the previous code server-side, so leaving the old one
    // in the box would guarantee the next submit fails.
    await waitFor(() => expect(screen.getByLabelText('Sign-in code')).toHaveValue(''));
  });
});

describe('credential handling', () => {
  it('writes NOTHING to localStorage or sessionStorage across a full sign-in', async () => {
    // ADR 0032 decision 3: the session is an HttpOnly cookie precisely so an XSS
    // cannot exfiltrate it. A single stray `setItem` would undo that, so the
    // invariant is asserted on the real storage objects rather than trusted.
    const localSet = vi.spyOn(Storage.prototype, 'setItem');
    const user = userEvent.setup();
    verifyCode.mockResolvedValue(ME);
    await renderPage();
    await submitEmail(user);
    await user.type(screen.getByLabelText('Sign-in code'), '123456');
    await user.click(screen.getByRole('button', { name: /verify and sign in/i }));
    await waitFor(() => expect(verifyCode).toHaveBeenCalled());

    expect(localSet).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    localSet.mockRestore();
  });
});
