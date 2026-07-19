import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  type Connection,
  type ConnectionVersion,
  getConnection,
  listConnectionVersions,
  updateConnection,
} from '../../src/api/connections';
import { ConnectionEdit } from '../../src/pages/ConnectionEdit';

vi.mock('../../src/api/connections', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/connections')>();
  return {
    ...actual,
    getConnection: vi.fn(),
    updateConnection: vi.fn(),
    listConnectionVersions: vi.fn(),
  };
});

const mockGet = vi.mocked(getConnection);
const mockUpdate = vi.mocked(updateConnection);
const mockVersions = vi.mocked(listConnectionVersions);

const existing: Connection = {
  id: 'c1',
  name: 'sf-dev',
  type: 'snowflake',
  env: 'dev',
  config: {
    account: 'acc1',
    user: 'svc',
    database: 'DB',
    schema: 'SC',
    warehouse: 'WH',
    auth_type: 'password',
  },
  has_secret: true,
  created_by: 'u1',
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/connections/c1/edit']}>
      <AntApp>
        <Routes>
          <Route path="/connections/:connectionId/edit" element={<ConnectionEdit />} />
          <Route path="/connections" element={<div>Connections list</div>} />
        </Routes>
      </AntApp>
    </MemoryRouter>,
  );
}

afterEach(() => vi.clearAllMocks());

describe('ConnectionEdit', () => {
  it('shows type + env read-only (immutable) and omits the secret', async () => {
    mockGet.mockResolvedValue(existing);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    expect(screen.getByText('Snowflake')).toBeInTheDocument();
    expect(screen.getByText('DEV')).toBeInTheDocument();
    // Type/Environment are display-only (no editable control); secret is omitted
    // (rotation is the Re-auth flow).
    expect(screen.queryByLabelText('Type')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Environment')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
  });

  it('prefills, submits a PATCH, and navigates back to the list', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(existing);
    mockUpdate.mockResolvedValue({ ...existing, name: 'sf-dev-2' });
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    expect(screen.getByLabelText('Name')).toHaveValue('sf-dev');

    await user.clear(screen.getByLabelText('Name'));
    await user.type(screen.getByLabelText('Name'), 'sf-dev-2');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1));
    expect(mockUpdate).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({
        name: 'sf-dev-2',
        config: expect.objectContaining({ account: 'acc1', auth_type: 'password' }),
      }),
    );
    expect(await screen.findByText('Connections list')).toBeInTheDocument();
  });

  it('opens the version-history drawer from the History button (#654)', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(existing);
    const versions: ConnectionVersion[] = [
      {
        version_no: 2,
        name: 'sf-dev',
        type: 'snowflake',
        env: 'dev',
        config: { account: 'acc1' },
        changed_by: 'u1',
        changed_by_name: 'Ada Lovelace',
        created_at: '2026-07-01T10:00:00Z',
      },
      {
        version_no: 1,
        name: 'sf-dev-old',
        type: 'snowflake',
        env: 'dev',
        config: { account: 'acc0' },
        changed_by: null,
        changed_by_name: null,
        created_at: '2026-06-01T10:00:00Z',
      },
    ];
    mockVersions.mockResolvedValue(versions);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    await user.click(screen.getByRole('button', { name: /History/ }));

    expect(await screen.findByText('History — “sf-dev”')).toBeInTheDocument();
    expect(mockVersions).toHaveBeenCalledWith('c1');
    // Newest first: v2 is tagged Current; the older snapshot shows its author gap.
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('Current')).toBeInTheDocument();
    expect(screen.getByText('sf-dev-old')).toBeInTheDocument();
    expect(screen.getByText(/Unknown/)).toBeInTheDocument();
    // Snapshots are credential-free — config renders as JSON.
    expect(screen.getByText(/"account": "acc0"/)).toBeInTheDocument();
  });

  it('surfaces a history load error inside the drawer (#654)', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(existing);
    mockVersions.mockRejectedValue(new Error('versions down'));
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    await user.click(screen.getByRole('button', { name: /History/ }));

    expect(await screen.findByText('Failed to load history')).toBeInTheDocument();
    expect(screen.getByText('versions down')).toBeInTheDocument();
  });

  it('shows an empty history state for a pre-versioning connection (#654)', async () => {
    const user = userEvent.setup();
    mockGet.mockResolvedValue(existing);
    mockVersions.mockResolvedValue([]);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    await user.click(screen.getByRole('button', { name: /History/ }));

    expect(
      await screen.findByText('No history yet — recording starts from the next save.'),
    ).toBeInTheDocument();
  });

  it('surfaces a load error', async () => {
    mockGet.mockRejectedValue(new Error('not found'));
    renderPage();

    // #910: a page-level fetch failure renders the dedicated error page. A plain
    // Error carries no HTTP status (the server never answered) → the 503 page.
    expect(await screen.findByText('500 — Something went wrong')).toBeInTheDocument();
  });

  it('refetches + reseeds when the route param changes (no stale prior connection)', async () => {
    const user = userEvent.setup();
    const other: Connection = {
      ...existing,
      id: 'c2',
      name: 'sf-qa',
      config: { ...existing.config, account: 'acc2' },
    };
    mockGet.mockImplementation(async (id: string) => (id === 'c2' ? other : existing));

    render(
      <MemoryRouter initialEntries={['/connections/c1/edit']}>
        <AntApp>
          {/* A param-only link → react-router reuses the element (no unmount). */}
          <Link to="/connections/c2/edit">go c2</Link>
          <Routes>
            <Route path="/connections/:connectionId/edit" element={<ConnectionEdit />} />
          </Routes>
        </AntApp>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc1'));
    await user.click(screen.getByRole('link', { name: 'go c2' }));

    // The key remounts the view → c2 is fetched and the form reseeds.
    await waitFor(() => expect(screen.getByLabelText('Account')).toHaveValue('acc2'));
    expect(screen.getByLabelText('Name')).toHaveValue('sf-qa');
  });
});
