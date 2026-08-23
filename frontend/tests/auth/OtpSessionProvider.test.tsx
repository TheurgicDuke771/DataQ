import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';

/** The OTP session lifecycle (ADR 0032, #736). */

const probeSession = vi.fn();
const endSession = vi.fn();

beforeEach(() => {
  vi.resetModules();
  probeSession.mockReset();
  endSession.mockReset();
  // Safe-by-default, same treatment as endSession below: every test that cares about the resolved
  // value overrides it explicitly.
  probeSession.mockResolvedValue(null);
  endSession.mockResolvedValue(undefined);
  vi.doMock('../../src/auth/config', () => ({ authMode: 'otp' }));
  vi.doMock('../../src/auth/otpClient', () => ({ probeSession, endSession }));
});

afterEach(() => {
  vi.doUnmock('../../src/auth/config');
  vi.doUnmock('../../src/auth/otpClient');
});

const ME: MeResponse = {
  id: 'u-1',
  aad_object_id: null,
  email: 'ada@acme.io',
  display_name: 'Ada L',
  last_seen_at: null,
  role: 'member' as const,
  is_workspace_admin: false,
};

/** Renders the provider with a probe that prints the state + exposes the actions. */
async function renderProvider() {
  const { OtpSessionProvider } = await import('../../src/auth/OtpSessionProvider');
  const { useOtpSession } = await import('../../src/auth/otpSessionContext');

  function Probe() {
    const { state, adopt, signOut, retry } = useOtpSession();
    return (
      <div>
        <span data-testid="status">{state.status}</span>
        <span data-testid="detail">
          {state.status === 'signed_in'
            ? state.me.email
            : state.status === 'error'
              ? state.message
              : ''}
        </span>
        <button onClick={() => adopt({ ...ME, email: 'grace@acme.io' })}>adopt</button>
        <button onClick={signOut}>sign-out</button>
        <button onClick={retry}>retry</button>
      </div>
    );
  }

  render(
    <OtpSessionProvider>
      <Probe />
    </OtpSessionProvider>,
  );
  return { status: () => screen.getByTestId('status').textContent };
}

describe('OtpSessionProvider — the initial probe', () => {
  it('starts in `probing`, then resolves to signed_in with the /me body', async () => {
    let resolve!: (me: MeResponse) => void;
    probeSession.mockReturnValue(new Promise<MeResponse>((r) => (resolve = r)));
    const { status } = await renderProvider();
    // `probing` is a real state, not a loading nicety — the SPA genuinely cannot read the cookie,
    // so flashing the sign-in form here would make every page reload look like a sign-out.
    expect(status()).toBe('probing');
    resolve(ME);
    await waitFor(() => expect(status()).toBe('signed_in'));
    expect(screen.getByTestId('detail')).toHaveTextContent('ada@acme.io');
  });

  it('resolves to signed_out on a clean 401 (probe returned null)', async () => {
    probeSession.mockResolvedValue(null);
    const { status } = await renderProvider();
    await waitFor(() => expect(status()).toBe('signed_out'));
  });

  it('goes to `error`, NOT signed_out, when the probe throws', async () => {
    probeSession.mockRejectedValue(new Error('Network Error'));
    const { status } = await renderProvider();
    await waitFor(() => expect(status()).toBe('error'));
    expect(screen.getByTestId('detail')).toHaveTextContent('Network Error');
  });

  it('falls back to a readable message when the failure carries none', async () => {
    probeSession.mockRejectedValue({ nope: true });
    const { status } = await renderProvider();
    await waitFor(() => expect(status()).toBe('error'));
    expect(screen.getByTestId('detail')).toHaveTextContent('Could not reach the DataQ API.');
  });

  it('re-probes on retry(), so a transient outage is recoverable without a reload', async () => {
    probeSession.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce(ME);
    const { status } = await renderProvider();
    await waitFor(() => expect(status()).toBe('error'));
    await userEvent.click(screen.getByText('retry'));
    await waitFor(() => expect(status()).toBe('signed_in'));
    expect(probeSession).toHaveBeenCalledTimes(2);
  });
});

describe('OtpSessionProvider — mid-session revocation', () => {
  it('drops to signed_out when the axios layer reports a lost session', async () => {
    probeSession.mockResolvedValue(ME);
    const { notifySessionInvalidated } = await import('../../src/auth/sessionEvents');
    const { status } = await renderProvider();
    await waitFor(() => expect(status()).toBe('signed_in'));

    // What a server-side revoke actually looks like from here: the cookie is
    // still in the browser, so the ONLY signal is the next request's 401.
    notifySessionInvalidated();
    await waitFor(() => expect(status()).toBe('signed_out'));
  });

  it('unsubscribes on unmount — a stale listener must not setState on a dead tree', async () => {
    probeSession.mockResolvedValue(ME);
    const { OtpSessionProvider } = await import('../../src/auth/OtpSessionProvider');
    const { useOtpSession } = await import('../../src/auth/otpSessionContext');
    const { notifySessionInvalidated } = await import('../../src/auth/sessionEvents');

    function Probe() {
      return <span data-testid="status">{useOtpSession().state.status}</span>;
    }
    const { unmount } = render(
      <OtpSessionProvider>
        <Probe />
      </OtpSessionProvider>,
    );
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('signed_in'));
    unmount();
    // Would warn/throw if the subscription outlived the component.
    expect(() => notifySessionInvalidated()).not.toThrow();
  });
});

describe('OtpSessionProvider — sign out', () => {
  it('revokes server-side and drops the UI to signed_out', async () => {
    probeSession.mockResolvedValue(ME);
    const { status } = await renderProvider();
    await waitFor(() => expect(status()).toBe('signed_in'));

    await userEvent.click(screen.getByText('sign-out'));
    await waitFor(() => expect(status()).toBe('signed_out'));
    expect(endSession).toHaveBeenCalledTimes(1);
  });

  it('still signs the UI out when the revoke POST FAILS', async () => {
    // The alternative — waiting on the POST, or staying signed in when it fails — leaves the user
    // looking at an authenticated shell after clicking Sign out.
    probeSession.mockResolvedValue(ME);
    endSession.mockRejectedValue(new Error('network down'));
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { status } = await renderProvider();
    await waitFor(() => expect(status()).toBe('signed_in'));

    await userEvent.click(screen.getByText('sign-out'));
    await waitFor(() => expect(status()).toBe('signed_out'));
    await waitFor(() => expect(consoleError).toHaveBeenCalled());
    consoleError.mockRestore();
  });
});

describe('OtpSessionProvider — other auth modes', () => {
  // Drives the REAL config module via window.__DATAQ_CONFIG__ (the runtime- config contract, ADR
  // 0028 — same pattern as CurrentUserProvider.test.tsx) instead of a
  // doMock('../../src/auth/config', ...).
  afterEach(() => {
    delete (window as { __DATAQ_CONFIG__?: unknown }).__DATAQ_CONFIG__;
  });

  it.each([
    ['real', { authority: 'https://issuer.example/v2.0', clientId: 'spa-1' }],
    ['dev_bypass', { mode: 'bypass' }],
    ['unconfigured', {}],
  ] as const)('is a passthrough in %s mode and never probes /me', async (_mode, auth) => {
    // A probe here would race the OIDC token acquisition and 401 for no reason.
    vi.doUnmock('../../src/auth/config');
    (window as { __DATAQ_CONFIG__?: unknown }).__DATAQ_CONFIG__ = { auth };
    vi.doMock('../../src/auth/otpClient', () => ({ probeSession, endSession }));
    const { OtpSessionProvider } = await import('../../src/auth/OtpSessionProvider');
    render(
      <OtpSessionProvider>
        <span>child</span>
      </OtpSessionProvider>,
    );
    expect(screen.getByText('child')).toBeInTheDocument();
    expect(probeSession).not.toHaveBeenCalled();
  });
});
