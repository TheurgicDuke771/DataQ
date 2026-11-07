import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from '../src/App';
import { api } from '../src/api/client';
import { useIsWorkspaceAdmin } from '../src/auth/useMe';
import { useCurrentUser } from '../src/auth/useCurrentUser';
import { ThemeModeProvider } from '../src/themeMode/ThemeModeProvider';

vi.mock('../src/auth/config', () => ({ authMode: 'dev_bypass', authMethodLabel: 'Dev bypass' }));
vi.mock('../src/auth/authClient', () => ({ login: vi.fn(), logout: vi.fn() }));
vi.mock('../src/auth/useMe', () => ({
  useIsWorkspaceAdmin: vi.fn(),
  useMe: vi.fn(() => ({
    status: 'ok',
    data: { id: 'u-1', email: 'a@dataq.io', role: 'admin', is_workspace_admin: true },
  })),
  useUpdateMe: vi.fn(() => vi.fn()),
}));
vi.mock('../src/auth/useCurrentUser', () => ({ useCurrentUser: vi.fn() }));
vi.mock('../src/api/client', () => ({
  api: {
    get: vi.fn(() => new Promise(() => {})),
    post: vi.fn(() => new Promise(() => {})),
    put: vi.fn(() => new Promise(() => {})),
    delete: vi.fn(() => new Promise(() => {})),
  },
}));

const JUNK = 'not-a-uuid';
const GOOD = '3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b';

// Every route that takes an id from the URL and fetches by it (App.tsx), by path.
const ID_ROUTES = [
  `/connections/${JUNK}/edit`,
  `/suites/${JUNK}`,
  `/suites/${JUNK}/edit`,
  `/suites/${JUNK}/checks/new`,
  `/suites/${GOOD}/checks/${JUNK}/edit`,
  `/assets/${JUNK}`,
  `/contracts/${JUNK}`,
  `/results/${JUNK}`,
  `/results/${GOOD}/compare/${JUNK}`,
];

afterEach(() => vi.clearAllMocks());

describe('App id routes are guarded individually', () => {
  it.each(ID_ROUTES)('%s renders not-found and requests nothing', async (path) => {
    vi.mocked(useIsWorkspaceAdmin).mockReturnValue(true);
    vi.mocked(useCurrentUser).mockReturnValue({
      name: 'Dev',
      username: 'dev@dataq.local',
      homeAccountId: 'acc',
      isDev: true,
    });
    render(
      <ThemeModeProvider>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </ThemeModeProvider>,
    );
    expect(await screen.findByText('404 — Not found', {}, { timeout: 10_000 })).toBeInTheDocument();
    for (const method of ['get', 'post', 'put', 'delete'] as const) {
      for (const call of vi.mocked(api[method]).mock.calls) {
        expect(String(call[0])).not.toContain(JUNK);
      }
    }
  });
});
