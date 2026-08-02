import { App as AntApp } from 'antd';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { type AdminWebhook, listAdminWebhooks, testAuthEmail } from '../../src/api/admin';
import type { MeResponse } from '../../src/api/me';
import { authMethodLabel } from '../../src/auth/config';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Settings } from '../../src/pages/Settings';

vi.mock('../../src/api/admin', () => ({
  listAdminWebhooks: vi.fn(),
  testAuthEmail: vi.fn(),
}));
const mockWebhooks = vi.mocked(listAdminWebhooks);
const mockTestAuthEmail = vi.mocked(testAuthEmail);

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
    provider: 'airflow',
    auth: 'HMAC-SHA256 signature header (X-DataQ-Signature) — ADR 0007',
    inbound_url: 'https://dataq.example.com/api/v1/orchestration/events/airflow',
    token_configured: true,
    signing_secret_name: 'airflow-webhook-secret',
    connection_names: ['airflow-prod'],
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

beforeEach(() => {
  mockWebhooks.mockResolvedValue(WEBHOOKS);
  mockTestAuthEmail.mockReset();
});

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

// GeneralTab uses antd's App.useApp() for the "Send test email" toasts → wrap
// in <AntApp> (same pattern as Connections.test.tsx).
function renderSettings(me: AsyncState<MeResponse>) {
  return render(
    <MemoryRouter>
      <AntApp>
        <MeContext.Provider value={me}>
          <Settings />
        </MeContext.Provider>
      </AntApp>
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

  it('renders per-provider labels and callback copy — dbt is post-build, not DAG (#652/#647)', async () => {
    renderSettings(adminMe);
    fireEvent.click(screen.getByRole('tab', { name: 'Webhooks' }));
    // 'Apache Airflow' differs from the raw code 'airflow', so this genuinely
    // asserts the shared-PROVIDER_LABELS path (dbt's label equals its code).
    expect(await screen.findByText('Apache Airflow')).toBeInTheDocument();
    expect(screen.getByText('dbt')).toBeInTheDocument();
    expect(screen.getByText('dbt-webhook-secret')).toBeInTheDocument();
    // Per-provider callback noun: dbt is a post-build callback (ADR 0029),
    // Airflow a DAG callback — dbt must not inherit the Airflow wording.
    expect(screen.getByText(/post-build callback snippet/)).toBeInTheDocument();
    expect(screen.getByText(/DAG callback snippet/)).toBeInTheDocument();
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

  // ── SMTP pre-flight test (#737, ADR 0032 decision 7) ──────────────────────

  it('sends a real test email to the caller and toasts success', async () => {
    mockTestAuthEmail.mockResolvedValue({ status: 'ok', to: 'admin@dataq.io' });
    renderSettings(adminMe);

    fireEvent.click(screen.getByRole('button', { name: 'Send test email' }));

    expect(await screen.findByText(/Test email sent to admin@dataq\.io/)).toBeInTheDocument();
    expect(mockTestAuthEmail).toHaveBeenCalledTimes(1);
  });

  it('surfaces the failing SMTP stage when the pre-flight test fails', async () => {
    // Shape the axios response interceptor already produces (client.ts folds
    // the error-envelope's message into `err.message`) — the frontend never
    // parses `error.detail.stage` itself; the backend's message already names it.
    mockTestAuthEmail.mockRejectedValue(
      new Error(
        "SMTP pre-flight failed at the 'auth' stage — see the server log for the underlying error type.",
      ),
    );
    renderSettings(adminMe);

    fireEvent.click(screen.getByRole('button', { name: 'Send test email' }));

    expect(await screen.findByText(/'auth' stage/)).toBeInTheDocument();
    await waitFor(() => expect(mockTestAuthEmail).toHaveBeenCalledTimes(1));
  });
});
