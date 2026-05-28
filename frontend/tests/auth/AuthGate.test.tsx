import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Reset the config mock between tests so each can declare its own authMode.
beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.doUnmock('../../src/auth/config');
  vi.doUnmock('@azure/msal-react');
});

async function renderAuthGate() {
  const { AuthGate } = await import('../../src/auth/AuthGate');
  render(
    <AuthGate>
      <div>protected-content</div>
    </AuthGate>,
  );
}

describe('AuthGate', () => {
  it('renders children directly in dev_bypass mode', async () => {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'dev_bypass',
      authConfig: {},
      DEV_USER: {},
    }));
    await renderAuthGate();
    expect(screen.getByText('protected-content')).toBeInTheDocument();
  });

  it('renders a setup-needed banner in unconfigured mode', async () => {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'unconfigured',
      authConfig: {},
      DEV_USER: {},
    }));
    await renderAuthGate();
    expect(screen.getByText(/Authentication is not configured/)).toBeInTheDocument();
    expect(screen.queryByText('protected-content')).not.toBeInTheDocument();
  });

  it('renders sign-in button when real mode + unauthenticated', async () => {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'real',
      authConfig: { apiScopeUri: 'api://x/user_impersonation' },
      DEV_USER: {},
    }));
    vi.doMock('@azure/msal-react', () => ({
      useIsAuthenticated: () => false,
      useMsal: () => ({ instance: { loginRedirect: vi.fn() } }),
    }));
    await renderAuthGate();
    expect(screen.getByRole('button', { name: /Sign in with Microsoft/i })).toBeInTheDocument();
    expect(screen.queryByText('protected-content')).not.toBeInTheDocument();
  });

  it('renders children when real mode + authenticated', async () => {
    vi.doMock('../../src/auth/config', () => ({
      authMode: 'real',
      authConfig: { apiScopeUri: 'api://x/user_impersonation' },
      DEV_USER: {},
    }));
    vi.doMock('@azure/msal-react', () => ({
      useIsAuthenticated: () => true,
      useMsal: () => ({ instance: { loginRedirect: vi.fn() } }),
    }));
    await renderAuthGate();
    expect(screen.getByText('protected-content')).toBeInTheDocument();
  });
});
