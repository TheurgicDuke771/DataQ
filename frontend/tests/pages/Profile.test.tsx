import { App as AntApp } from 'antd';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Profile } from '../../src/pages/Profile';

// The ApiKeysPanel on the profile fetches the user's PATs on mount; stub the
// client so the page tests don't hit the network (its own behaviour is covered
// in ApiKeysPanel.test.tsx).
vi.mock('../../src/api/apiKeys', () => ({
  listApiKeys: vi.fn().mockResolvedValue([]),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
  PAT_DEFAULT_EXPIRY_DAYS: 90,
  PAT_MAX_EXPIRY_DAYS: 365,
}));

const me: AsyncState<MeResponse> = {
  status: 'ok',
  data: {
    id: 'u-1',
    aad_object_id: 'oid-1',
    email: 'ada@dataq.io',
    display_name: 'Ada Lovelace',
    last_seen_at: '2026-06-26T10:00:00Z',
    is_workspace_admin: false,
  },
};

function renderProfile(state: AsyncState<MeResponse>) {
  return render(
    <MemoryRouter>
      <AntApp>
        <MeContext.Provider value={state}>
          <Profile />
        </MeContext.Provider>
      </AntApp>
    </MemoryRouter>,
  );
}

describe('Profile', () => {
  it('renders identity + workspace facts from /me', () => {
    renderProfile(me);
    expect(screen.getByRole('heading', { name: 'Profile' })).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText('ada@dataq.io')).toBeInTheDocument();
    expect(screen.getByText('Azure AD (MSAL)')).toBeInTheDocument();
    expect(screen.getByText('2026-06-26T10:00:00Z')).toBeInTheDocument();
    // Member, not admin.
    expect(screen.getAllByText('Member').length).toBeGreaterThan(0);
  });

  it('tags a workspace admin', () => {
    renderProfile({ ...me, data: { ...me.data, is_workspace_admin: true } });
    expect(screen.getAllByText('Workspace admin').length).toBeGreaterThan(0);
  });

  it('points alerting config at suites (per-suite, not per-user)', () => {
    renderProfile(me);
    expect(screen.getByText('DQ alerts are configured per suite')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Suites' })).toHaveAttribute('href', '/suites');
  });

  it('shows an error state when /me fails', () => {
    renderProfile({ status: 'error', error: 'boom' });
    expect(screen.getByText('Failed to load your profile')).toBeInTheDocument();
  });
});
