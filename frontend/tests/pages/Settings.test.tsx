import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { type AdminWebhook, listAdminWebhooks } from '../../src/api/admin';
import type { MeResponse } from '../../src/api/me';
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
  {
    provider: 'dbt',
    auth: 'HMAC-SHA256 signature header (X-DataQ-Signature) — ADR 0029',
    inbound_url: 'https://dataq.example.com/api/v1/orchestration/events/dbt',
    token_configured: true,
    signing_secret_name: 'dbt-webhook-secret',
    connection_names: ['analytics-dbt'],
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
  });

  it('shows the inbound-webhooks config on the Webhooks tab', async () => {
    renderSettings(adminMe);
    fireEvent.click(screen.getByRole('tab', { name: 'Webhooks' }));
    expect(await screen.findByText('Azure Data Factory')).toBeInTheDocument();
  });

  it('renders a dbt webhook row with its own label and post-build copy (#652/#647)', async () => {
    renderSettings(adminMe);
    fireEvent.click(screen.getByRole('tab', { name: 'Webhooks' }));
    // Labeled via the shared PROVIDER_LABELS (not the raw provider fallback).
    expect(await screen.findByText('dbt')).toBeInTheDocument();
    expect(screen.getByText('dbt-webhook-secret')).toBeInTheDocument();
    // dbt is a post-build callback (ADR 0029), not an Airflow DAG callback.
    expect(screen.getByText(/post-build callback snippet/)).toBeInTheDocument();
  });

  it('flags a webhook row whose secret is not provisioned', async () => {
    mockWebhooks.mockResolvedValue([
      { ...WEBHOOKS[1], token_configured: false }, // HMAC rows flag too, not just ADF
    ]);
    renderSettings(adminMe);
    fireEvent.click(screen.getByRole('tab', { name: 'Webhooks' }));
    expect(await screen.findByText('webhook secret not set')).toBeInTheDocument();
  });

  it('shows the Forbidden page for a non-admin (server-driven via /me)', () => {
    renderSettings({ ...adminMe, data: { ...adminMe.data, is_workspace_admin: false } });
    expect(screen.getByText('403 — Forbidden')).toBeInTheDocument();
  });
});
