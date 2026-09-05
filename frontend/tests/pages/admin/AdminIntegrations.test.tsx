import { screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { listAdminWebhooks } from '../../../src/api/admin';
import { AdminIntegrations } from '../../../src/pages/admin/AdminIntegrations';
import { WEBHOOKS, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({ listAdminWebhooks: vi.fn() }));
const mockWebhooks = vi.mocked(listAdminWebhooks);

beforeEach(() => mockWebhooks.mockResolvedValue(WEBHOOKS));
afterEach(() => vi.clearAllMocks());

describe('AdminIntegrations', () => {
  it('shows the inbound orchestration webhook config', async () => {
    renderSubPage(<AdminIntegrations />);
    expect(await screen.findByText('Azure Data Factory')).toBeInTheDocument();
  });

  it('renders per-provider labels and callback copy — dbt is post-build, not DAG', async () => {
    renderSubPage(<AdminIntegrations />);
    // 'Apache Airflow' differs from the raw code 'airflow', so this genuinely
    // asserts the shared-PROVIDER_LABELS path (dbt's label equals its code).
    expect(await screen.findByText('Apache Airflow')).toBeInTheDocument();
    expect(screen.getByText('dbt')).toBeInTheDocument();
    expect(screen.getByText('dbt-webhook-secret')).toBeInTheDocument();
    expect(screen.getByText(/post-build callback snippet/)).toBeInTheDocument();
    expect(screen.getByText(/DAG callback snippet/)).toBeInTheDocument();
  });

  it('masks the ADF token until it is revealed', async () => {
    renderSubPage(<AdminIntegrations />);
    await screen.findByText('Azure Data Factory');
    const adfInput = screen.getAllByRole('textbox')[0] as HTMLInputElement;
    expect(adfInput.value).toContain('token=••••••••');
    expect(adfInput.value).not.toContain('abc123');
  });

  it('flags a webhook row whose secret is not provisioned', async () => {
    mockWebhooks.mockResolvedValue([{ ...WEBHOOKS[1], token_configured: false }]);
    renderSubPage(<AdminIntegrations />);
    expect(await screen.findByText('webhook secret not set')).toBeInTheDocument();
  });

  it('says so when no orchestration connection exists', async () => {
    mockWebhooks.mockResolvedValue([]);
    renderSubPage(<AdminIntegrations />);
    expect(await screen.findByText('No orchestration connections configured.')).toBeInTheDocument();
  });

  it('surfaces a load error inline', async () => {
    // `…Once` — a persistent rejection resurfaces unhandled in RTL's cleanup.
    mockWebhooks.mockRejectedValueOnce(new Error('boom'));
    renderSubPage(<AdminIntegrations />);
    expect(await screen.findByText('Failed to load webhook config')).toBeInTheDocument();
  });
});
