import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Reset mocks between tests so each can declare its own authMode + auth user.
beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.doUnmock('../../src/auth/config');
  vi.doUnmock('../../src/auth/authContext');
  vi.doUnmock('../../src/auth/authClient');
});

async function renderAuthGate() {
  const { AuthGate } = await import('../../src/auth/AuthGate');
  render(
    <AuthGate>
      <div>protected-content</div>
    </AuthGate>,
  );
}

/** Mock the OIDC user hook + the login action for a real-mode render. */
function mockReal(user: unknown, login = vi.fn()) {
  vi.doMock('../../src/auth/config', () => ({ authMode: 'real', DEV_USER: {} }));
  vi.doMock('../../src/auth/authContext', () => ({ useAuthUser: () => user }));
  vi.doMock('../../src/auth/authClient', () => ({ login }));
  return login;
}

describe('AuthGate', () => {
  it('renders children directly in dev_bypass mode', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'dev_bypass', DEV_USER: {} }));
    await renderAuthGate();
    expect(screen.getByText('protected-content')).toBeInTheDocument();
  });

  it('renders a setup-needed banner in unconfigured mode', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'unconfigured', DEV_USER: {} }));
    await renderAuthGate();
    expect(screen.getByText(/Authentication is not configured/)).toBeInTheDocument();
    expect(screen.queryByText('protected-content')).not.toBeInTheDocument();
  });

  it('renders the sign-in page when real mode + signed out', async () => {
    mockReal(null);
    await renderAuthGate();
    expect(screen.getByRole('button', { name: /^Sign in$/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Sign in to DataQ' })).toBeInTheDocument();
    expect(screen.queryByText('protected-content')).not.toBeInTheDocument();
  });

  it('calls the OIDC login on click', async () => {
    const login = mockReal(null, vi.fn().mockResolvedValue(undefined));
    const { AuthGate } = await import('../../src/auth/AuthGate');
    const { default: userEvent } = await import('@testing-library/user-event');
    render(
      <AuthGate>
        <div>protected-content</div>
      </AuthGate>,
    );
    await userEvent.click(screen.getByRole('button', { name: /^Sign in$/i }));
    expect(login).toHaveBeenCalledOnce();
  });

  it('renders children when real mode + signed in', async () => {
    mockReal({ profile: { sub: 'u1' } });
    await renderAuthGate();
    expect(screen.getByText('protected-content')).toBeInTheDocument();
  });
});

/**
 * The `otp` gate (ADR 0032, #736).
 *
 * Four states, and the pair that is easy to conflate is the point of this block:
 * `probing` and `error` must BOTH stay off the sign-in form. Showing it while
 * probing makes every reload look like a sign-out; showing it during an API
 * outage invites the user to burn a single-use code at a server that cannot
 * check it.
 */
describe('AuthGate — otp mode', () => {
  async function renderOtpGate(state: unknown, actions: Record<string, unknown> = {}) {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'otp', DEV_USER: {} }));
    const noop = () => {};
    vi.doMock('../../src/auth/otpSessionContext', () => ({
      useOtpSession: () => ({ state, adopt: noop, signOut: noop, retry: noop, ...actions }),
    }));
    await renderAuthGate();
  }

  afterEach(() => {
    vi.doUnmock('../../src/auth/otpSessionContext');
  });

  it('renders children when the session resolves', async () => {
    await renderOtpGate({ status: 'signed_in', me: { id: 'u1', email: 'ada@acme.io' } });
    expect(screen.getByText('protected-content')).toBeInTheDocument();
  });

  it('renders the two-step code form when signed out — not the OIDC button', async () => {
    await renderOtpGate({ status: 'signed_out' });
    expect(screen.getByLabelText('Email address')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /send code/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Sign in$/i })).not.toBeInTheDocument();
    expect(screen.queryByText('protected-content')).not.toBeInTheDocument();
  });

  it('shows a spinner while probing — NOT the sign-in form', async () => {
    await renderOtpGate({ status: 'probing' });
    expect(screen.queryByLabelText('Email address')).not.toBeInTheDocument();
    expect(screen.queryByText('protected-content')).not.toBeInTheDocument();
  });

  it('shows the failure + a retry on error — NOT the sign-in form', async () => {
    const retry = vi.fn();
    await renderOtpGate({ status: 'error', message: 'Network Error' }, { retry });
    expect(screen.getByText(/Could not check your sign-in status/)).toBeInTheDocument();
    expect(screen.getByText('Network Error')).toBeInTheDocument();
    expect(screen.queryByLabelText('Email address')).not.toBeInTheDocument();

    const { default: userEvent } = await import('@testing-library/user-event');
    await userEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it('names otp in the unconfigured banner so an operator knows the mode exists', async () => {
    vi.doMock('../../src/auth/config', () => ({ authMode: 'unconfigured', DEV_USER: {} }));
    await renderAuthGate();
    expect(screen.getByText('DATAQ_AUTH_MODE=otp')).toBeInTheDocument();
  });
});
