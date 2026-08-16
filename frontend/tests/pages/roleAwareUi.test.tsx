import { App } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { WorkspaceRole } from '../../src/api/admin';
import { listConnections } from '../../src/api/connections';
import { listSuites } from '../../src/api/suites';
import { Connections } from '../../src/pages/Connections';
import { Suites } from '../../src/pages/Suites';

/**
 * Role-aware UI — ADR 0033 slice #743.
 *
 * Every assertion here is about what is *offered*, never about what is
 * permitted: the server re-enforces each of these with a 403 (#741), and hiding
 * a control is presentation. The reason to hide it anyway is honesty — offering
 * an action that will be refused is a worse experience than not offering it.
 *
 * Both directions are asserted for every control. A test that only checks the
 * control is hidden for a Viewer passes just as happily against a page that
 * renders nothing at all.
 */

// Spread the real modules and override only the fetchers: a hand-listed mock
// omits whatever else the page imports, and the omission surfaces as the whole
// component throwing rather than as a missing-export message.
vi.mock('../../src/api/connections', async () => ({
  ...(await vi.importActual<typeof import('../../src/api/connections')>(
    '../../src/api/connections',
  )),
  listConnections: vi.fn(),
}));
vi.mock('../../src/api/suites', async () => ({
  ...(await vi.importActual<typeof import('../../src/api/suites')>('../../src/api/suites')),
  listSuites: vi.fn(),
}));

let role: WorkspaceRole | null = 'admin';
vi.mock('../../src/auth/useMe', () => ({
  useWorkspaceRole: () => role,
  useCanMutateConnections: () => role === 'admin',
  useCanAuthor: () => role === 'admin' || role === 'member',
  useMe: () => ({ status: 'ok', data: { id: 'u-1', role } }),
  useUpdateMe: () => vi.fn(),
  useIsWorkspaceAdmin: () => role === 'admin',
}));

const CONNECTION = {
  id: 'c-1',
  name: 'finance-dev',
  type: 'snowflake',
  env: 'dev',
  config: {},
  has_secret: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function renderPage(node: React.ReactElement) {
  render(
    <App>
      <MemoryRouter>{node}</MemoryRouter>
    </App>,
  );
}

describe('Connections — role-aware controls', () => {
  beforeEach(() => {
    vi.mocked(listConnections).mockResolvedValue([CONNECTION] as never);
  });

  it('offers Add connection to an admin', async () => {
    role = 'admin';
    renderPage(<Connections />);
    expect(await screen.findByRole('button', { name: 'Add connection' })).toBeInTheDocument();
  });

  it.each(['member', 'viewer'] as const)('hides Add connection from a %s', async (r) => {
    role = r;
    renderPage(<Connections />);
    // Wait for the page to actually render before asserting an absence —
    // otherwise this passes against a blank screen.
    expect(await screen.findByText('Connections')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add connection' })).not.toBeInTheDocument();
  });

  it.each(['member', 'viewer'] as const)('tells a %s the page is managed by admins', async (r) => {
    role = r;
    renderPage(<Connections />);
    // The missing buttons must read as intentional, not as a broken page.
    expect(await screen.findByText(/managed by workspace admins/i)).toBeInTheDocument();
  });

  it('hides the per-connection actions menu from a non-admin', async () => {
    role = 'member';
    renderPage(<Connections />);
    await screen.findByText('finance-dev');
    // Rendered as nothing rather than an empty dropdown: every entry in that
    // menu mutates, so a trigger that opens to nothing would read as broken.
    expect(screen.queryByRole('button', { name: /finance-dev actions/i })).not.toBeInTheDocument();
  });

  it('shows the per-connection actions menu to an admin', async () => {
    role = 'admin';
    renderPage(<Connections />);
    expect(await screen.findByRole('button', { name: /finance-dev actions/i })).toBeInTheDocument();
  });

  it('offers Test to a member but not to a viewer', async () => {
    role = 'member';
    const { unmount } = render(
      <App>
        <MemoryRouter>
          <Connections />
        </MemoryRouter>
      </App>,
    );
    expect(await screen.findByRole('button', { name: 'Test' })).toBeInTheDocument();
    unmount();

    role = 'viewer';
    renderPage(<Connections />);
    await screen.findByText('finance-dev');
    // A viewer's probe would open an outbound connection with stored
    // credentials — Member+ on the server, so hidden here.
    expect(screen.queryByRole('button', { name: 'Test' })).not.toBeInTheDocument();
  });

  it('renders nothing role-dependent while /me is still loading', async () => {
    // `null` is "not yet known", NOT "no permission". Showing the read-only hint
    // during the fetch and then popping controls in is worse than a beat of
    // nothing.
    role = null;
    renderPage(<Connections />);
    await screen.findByText('Connections');
    expect(screen.queryByText(/managed by workspace admins/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add connection' })).not.toBeInTheDocument();
  });
});

describe('Suites — role-aware controls', () => {
  beforeEach(() => {
    vi.mocked(listSuites).mockResolvedValue([] as never);
    // The page loads connections too (it needs one before a suite can be
    // created); without this the authoring buttons never leave their loading
    // state and the assertions below would fail for an unrelated reason.
    vi.mocked(listConnections).mockResolvedValue([CONNECTION] as never);
  });

  it.each(['admin', 'member'] as const)('offers New suite + Import to a %s', async (r) => {
    role = r;
    renderPage(<Suites />);
    // Regex, not an exact string: antd folds a transient loading icon (with its
    // own "loading" aria-label) into the button's accessible name while the
    // connection list settles, so an exact match is a race.
    expect(await screen.findByRole('button', { name: /New suite/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Import/ })).toBeInTheDocument();
  });

  it('hides both authoring controls from a viewer', async () => {
    role = 'viewer';
    renderPage(<Suites />);
    expect(await screen.findByText('Read-only access')).toBeInTheDocument();
    // Hidden, not disabled: a disabled primary button invites a hunt for the
    // precondition that would enable it, and there isn't one.
    expect(screen.queryByRole('button', { name: /New suite/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Import/ })).not.toBeInTheDocument();
  });

  it('shows no read-only hint while /me is loading', async () => {
    role = null;
    renderPage(<Suites />);
    await waitFor(() => expect(screen.getByText('Suites')).toBeInTheDocument());
    expect(screen.queryByText('Read-only access')).not.toBeInTheDocument();
  });
});
