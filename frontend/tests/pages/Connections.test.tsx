import { App as AntApp } from 'antd';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { type Connection, listConnections, testConnection } from '../../src/api/connections';
import { Connections } from '../../src/pages/Connections';

// Keep the real CONNECTION_TYPES / labels; mock only the network functions.
vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return { ...actual, listConnections: vi.fn(), testConnection: vi.fn() };
});

const mockList = vi.mocked(listConnections);
const mockTest = vi.mocked(testConnection);

function conn(overrides: Partial<Connection>): Connection {
  return {
    id: 'c1',
    name: 'sf-dev',
    type: 'snowflake',
    env: 'dev',
    config: {},
    has_secret: true,
    created_by: 'u1',
    ...overrides,
  };
}

// ConnectionCard uses antd's App.useApp() for messages → wrap in <AntApp>.
function renderPage() {
  return render(
    <AntApp>
      <Connections />
    </AntApp>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe('Connections', () => {
  it('groups connections by type with env + credential badges', async () => {
    mockList.mockResolvedValue([
      conn({ id: 'c1', name: 'sf-dev', type: 'snowflake', env: 'dev', has_secret: true }),
      conn({ id: 'c2', name: 's3-prod', type: 's3', env: 'prod', has_secret: false }),
    ]);

    renderPage();

    expect(await screen.findByText('sf-dev')).toBeInTheDocument();
    expect(screen.getByText('Snowflake')).toBeInTheDocument();
    expect(screen.getByText('AWS S3')).toBeInTheDocument();
    expect(screen.getByText('DEV')).toBeInTheDocument();
    expect(screen.getByText('PROD')).toBeInTheDocument();
    expect(screen.getByText('credential set')).toBeInTheDocument();
    expect(screen.getByText('no credential')).toBeInTheDocument();
  });

  it('shows an empty state when there are no connections', async () => {
    mockList.mockResolvedValue([]);

    renderPage();

    expect(await screen.findByText('No connections configured yet')).toBeInTheDocument();
  });

  it('runs a connectivity test from a card', async () => {
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'sf-dev' })]);
    mockTest.mockResolvedValue({ ok: true });

    renderPage();
    await screen.findByText('sf-dev');
    await userEvent.click(screen.getByRole('button', { name: 'Test' }));

    expect(mockTest).toHaveBeenCalledWith('c1');
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));

    renderPage();

    expect(await screen.findByText('Failed to load connections')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });
});
