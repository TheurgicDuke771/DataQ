import { App as AntApp } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type Connection,
  deleteConnection,
  listConnections,
  reauthConnection,
  testConnection,
} from '../../src/api/connections';
import { Connections } from '../../src/pages/Connections';

// Keep the real CONNECTION_TYPES / labels; mock only the network functions.
vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return {
    ...actual,
    listConnections: vi.fn(),
    testConnection: vi.fn(),
    deleteConnection: vi.fn(),
    reauthConnection: vi.fn(),
  };
});

const mockList = vi.mocked(listConnections);
const mockTest = vi.mocked(testConnection);
const mockDelete = vi.mocked(deleteConnection);
const mockReauth = vi.mocked(reauthConnection);

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

// ConnectionCard uses antd's App.useApp() for messages → wrap in <AntApp>; the
// page navigates to /connections/new → wrap in a router.
function renderPage() {
  return render(
    <MemoryRouter>
      <AntApp>
        <Connections />
      </AntApp>
    </MemoryRouter>,
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

  it('runs a connectivity test from a card and shows a healthy badge', async () => {
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'sf-dev' })]);
    mockTest.mockResolvedValue({ ok: true });

    renderPage();
    await screen.findByText('sf-dev');
    await userEvent.click(screen.getByRole('button', { name: 'Test' }));

    expect(mockTest).toHaveBeenCalledWith('c1');
    expect(await screen.findByText('healthy')).toBeInTheDocument();
  });

  it('bulk-tests every connection via "Test all" and flags failures with a re-auth link', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([
      conn({ id: 'c1', name: 'sf-dev' }),
      conn({ id: 'c2', name: 's3-prod', type: 's3' }),
    ]);
    // c1 reachable, c2 unreachable.
    mockTest.mockImplementation((id: string) => Promise.resolve({ ok: id === 'c1' }));

    renderPage();
    await screen.findByText('sf-dev');
    await user.click(screen.getByRole('button', { name: 'Test all' }));

    // Both tested; one healthy, one unreachable + a re-auth affordance.
    await waitFor(() => expect(mockTest).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('healthy')).toBeInTheDocument();
    expect(await screen.findByText('unreachable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Re-authenticate' })).toBeInTheDocument();
  });

  it('clears the unreachable badge after a successful re-authentication', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'sf-dev' })]);
    mockTest.mockResolvedValue({ ok: false });
    mockReauth.mockResolvedValue({ ok: true });

    renderPage();
    await screen.findByText('sf-dev');

    // Fail a test → unreachable badge + inline re-auth link.
    await user.click(screen.getByRole('button', { name: 'Test' }));
    expect(await screen.findByText('unreachable')).toBeInTheDocument();

    // Re-auth via the inline link, rotate the credential successfully.
    await user.click(screen.getByRole('button', { name: 'Re-authenticate' }));
    await user.type(await screen.findByLabelText('New: Password'), 'fresh-secret');
    await user.click(screen.getByRole('button', { name: 'Rotate credential' }));

    // The stale verdict is dropped — badge + link gone until re-tested.
    await waitFor(() => expect(mockReauth).toHaveBeenCalledWith('c1', 'fresh-secret'));
    await waitFor(() => expect(screen.queryByText('unreachable')).not.toBeInTheDocument());
  });

  it('surfaces a load error', async () => {
    mockList.mockRejectedValue(new Error('boom'));

    renderPage();

    expect(await screen.findByText('Failed to load connections')).toBeInTheDocument();
    expect(screen.getByText('boom')).toBeInTheDocument();
  });

  it('deletes a connection via the actions menu after confirming', async () => {
    const user = userEvent.setup();
    mockList.mockResolvedValue([conn({ id: 'c1', name: 'sf-dev' })]);
    mockDelete.mockResolvedValue();

    renderPage();
    await screen.findByText('sf-dev');

    // Open the card's actions menu and choose Delete.
    await user.click(screen.getByRole('button', { name: 'sf-dev actions' }));
    await user.click(await screen.findByText('Delete'));

    // Confirm in the modal (its OK button is also labelled "Delete").
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('c1'));
  });
});
