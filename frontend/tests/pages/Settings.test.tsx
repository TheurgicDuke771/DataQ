import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Settings } from '../../src/pages/Settings';

const adminMe: AsyncState<MeResponse> = {
  status: 'ok',
  data: {
    id: 'u-1',
    aad_object_id: 'oid-1',
    email: 'admin@dataq.io',
    display_name: 'Ada Admin',
    last_seen_at: null,
    is_workspace_admin: true,
  },
};

function renderSettings(me: AsyncState<MeResponse>) {
  return render(
    <MemoryRouter>
      <MeContext.Provider value={me}>
        <Settings />
      </MeContext.Provider>
    </MemoryRouter>,
  );
}

describe('Settings', () => {
  it('renders the settings shell for a workspace admin', () => {
    renderSettings(adminMe);
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByText('Workspace settings are coming soon.')).toBeInTheDocument();
  });

  it('shows the Forbidden page for a non-admin (server-driven via /me)', () => {
    renderSettings({ ...adminMe, data: { ...adminMe.data, is_workspace_admin: false } });
    expect(screen.getByText('403 — Forbidden')).toBeInTheDocument();
  });
});
