import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { type AdminWebhook, listAdminWebhooks } from '../../src/api/admin';
import type { MeResponse } from '../../src/api/me';
import { authMethodLabel } from '../../src/auth/config';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Settings } from '../../src/pages/Settings';

vi.mock('../../src/api/admin', () => ({ listAdminWebhooks: vi.fn() }));
const mockWebhooks = vi.mocked(listAdminWebhooks);

const WEBHOOKS: AdminWebhook[] = [
  {
    provider: 'adf',
    auth: 'Shared secret in the URL (?token=…)',
    inbound_url: 'https://dataq.example.com/api/v1/orchestration/events/adf?token=abc123',
    token_configured: true,
    signing_secret_name: null,
    connection_names: ['prod-factory'],
  },
];

beforeEach(() => mockWebhooks.mockResolvedValue(WEBHOOKS));

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
  it('renders the tabbed settings shell for a workspace admin', () => {
    renderSettings(adminMe);
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    for (const tab of ['General', 'Secrets', 'Webhooks', 'Notifications', 'Danger zone']) {
      expect(screen.getByRole('tab', { name: tab })).toBeInTheDocument();
    }
    // General tab is default-active: workspace facts visible.
    expect(screen.getByText('Single tenant')).toBeInTheDocument();
    // Provider-neutral auth label derived from the runtime authMode (ADR 0028 —
    // MSAL retired for generic OIDC; per-mode wording pinned in config.test.ts).
    expect(screen.getByText(authMethodLabel)).toBeInTheDocument();
  });

  it('shows the inbound-webhooks config on the Webhooks tab', async () => {
    renderSettings(adminMe);
    fireEvent.click(screen.getByRole('tab', { name: 'Webhooks' }));
    expect(await screen.findByText('Azure Data Factory')).toBeInTheDocument();
  });

  it('shows the Forbidden page for a non-admin (server-driven via /me)', () => {
    renderSettings({ ...adminMe, data: { ...adminMe.data, is_workspace_admin: false } });
    expect(screen.getByText('403 — Forbidden')).toBeInTheDocument();
  });
});
