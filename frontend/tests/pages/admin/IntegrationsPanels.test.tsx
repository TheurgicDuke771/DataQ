import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type AdminHealth,
  getAdminHealth,
  type InventorySyncRow,
  listInventorySync,
  pollNow,
  regenerateWebhookSecret,
  runInventorySync,
  setInventorySync,
} from '../../../src/api/admin';
import {
  InventorySyncSection,
  PollingHealthSection,
  RegenerateSecretButton,
} from '../../../src/pages/admin/IntegrationsPanels';
import { WEBHOOKS, renderSubPage } from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  getAdminHealth: vi.fn(),
  listInventorySync: vi.fn(),
  pollNow: vi.fn(),
  regenerateWebhookSecret: vi.fn(),
  setInventorySync: vi.fn(),
  runInventorySync: vi.fn(),
}));

const HEALTH = {
  polling: [
    {
      connection_id: 'c1',
      name: 'harness-airflow-qa',
      provider: 'airflow',
      last_polled_at: null,
      cadence_seconds: 600,
      next_expected_at: null,
      status: 'unknown',
      last_error: null,
    },
    {
      connection_id: 'c2',
      name: 'harness-adf-prod',
      provider: 'adf',
      last_polled_at: '2026-09-05T10:00:00Z',
      cadence_seconds: 600,
      next_expected_at: '2026-09-05T10:10:00Z',
      status: 'failing',
      last_error: 'The provider rejected the credential.',
    },
  ],
  beat: { last_tick_at: null, status: 'not_monitored' },
  queues: null,
  queues_error: null,
  credentials: [],
  generated_at: '2026-09-05T10:00:00Z',
} as unknown as AdminHealth;

const SYNC: InventorySyncRow[] = [
  {
    connection_id: 'w1',
    name: 'conn-snowflake-orders',
    type: 'snowflake',
    env: 'qa',
    enabled: false,
    last_attempted_at: null,
    failing_since: null,
    last_error: null,
    tables_discovered: null,
    unmonitored: null,
    status: 'never_synced',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PollingHealthSection', () => {
  it('renders unknown and failing honestly and queues a poll-all', async () => {
    vi.mocked(getAdminHealth).mockResolvedValue(HEALTH);
    vi.mocked(pollNow).mockResolvedValue({ dispatched: [], requested_at: '2026-09-05T10:00:00Z' });
    renderSubPage(<PollingHealthSection />);
    expect(await screen.findByText('Unknown — never polled')).toBeInTheDocument();
    expect(screen.getByText('Failing')).toBeInTheDocument();
    expect(screen.getByText('The provider rejected the credential.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Poll all now' }));
    await waitFor(() => expect(pollNow).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('No orchestration connections to poll.')).toBeInTheDocument();
  });
});

describe('InventorySyncSection', () => {
  it('shows blanks for a never-synced connection and toggles through the API', async () => {
    vi.mocked(listInventorySync).mockResolvedValue(SYNC);
    vi.mocked(setInventorySync).mockResolvedValue({ ...SYNC[0], enabled: true });
    renderSubPage(<InventorySyncSection />);
    expect(await screen.findByText('Never synced')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
    fireEvent.click(
      screen.getByRole('switch', { name: 'Inventory sync for conn-snowflake-orders' }),
    );
    await waitFor(() => expect(setInventorySync).toHaveBeenCalledWith('w1', true));
    expect(runInventorySync).not.toHaveBeenCalled();
  });
});

describe('RegenerateSecretButton', () => {
  it('reveals the new value once with the grace window', async () => {
    vi.mocked(regenerateWebhookSecret).mockResolvedValue({
      provider: 'airflow',
      secret_name: 'airflow-webhook-hmac',
      auth_mode: 'hmac',
      value: 'new-key-value',
      grace_until: '2026-09-05T10:15:00Z',
      inbound_url: null,
    });
    renderSubPage(<RegenerateSecretButton webhook={WEBHOOKS[1]} />);
    fireEvent.click(screen.getByRole('button', { name: /Regenerate/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Regenerate' }));
    await waitFor(() => expect(regenerateWebhookSecret).toHaveBeenCalledWith(WEBHOOKS[1].provider));
    expect(await screen.findByText('new-key-value')).toBeInTheDocument();
    expect(screen.getByText(/keeps working until/)).toBeInTheDocument();
  });
});
