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
