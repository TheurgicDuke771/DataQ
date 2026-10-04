import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getOnboardingStatus, type OnboardingStatus } from '../../src/api/dashboard';
import * as me from '../../src/auth/useMe';
import { GetStartedPanel } from '../../src/components/dashboard/GetStartedPanel';

vi.mock('../../src/api/dashboard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/dashboard')>()),
  getOnboardingStatus: vi.fn(),
}));
const mockGet = vi.mocked(getOnboardingStatus);

const NOTHING: OnboardingStatus = {
  has_datasource: false,
  has_suite: false,
  has_check: false,
  has_run: false,
  complete: false,
};

function asRole(role: 'admin' | 'member' | 'viewer') {
  vi.spyOn(me, 'useWorkspaceRole').mockReturnValue(role);
  vi.spyOn(me, 'useCanMutateConnections').mockReturnValue(role === 'admin');
  vi.spyOn(me, 'useCanAuthor').mockReturnValue(role !== 'viewer');
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <Routes>
        <Route path="/dashboard" element={<GetStartedPanel />} />
        <Route path="/connections/new" element={<p>new connection page</p>} />
        <Route path="/suites/new" element={<p>new suite page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.restoreAllMocks();
  mockGet.mockReset();
});

describe('GetStartedPanel', () => {
  it('offers an admin the first step and takes them there', async () => {
    asRole('admin');
    mockGet.mockResolvedValue(NOTHING);
    renderPanel();

    expect(await screen.findByText('Get started — 0 of 4 done')).toBeInTheDocument();
    // Only the next step has an action: the later ones depend on it.
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Hide',
      'Add a connection',
    ]);
    await userEvent.click(screen.getByRole('button', { name: 'Add a connection' }));
    expect(screen.getByText('new connection page')).toBeInTheDocument();
  });

  it('tells a member who adds connections instead of offering a button that would 403', async () => {
    asRole('member');
    mockGet.mockResolvedValue(NOTHING);
    renderPanel();

    expect(await screen.findByText('A workspace admin adds connections.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add a connection' })).not.toBeInTheDocument();
  });

  it('moves to the next step once one is done', async () => {
    asRole('member');
    mockGet.mockResolvedValue({ ...NOTHING, has_datasource: true });
    renderPanel();

    expect(await screen.findByText('Get started — 1 of 4 done')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'New suite' }));
    expect(screen.getByText('new suite page')).toBeInTheDocument();
  });

  it('tells a viewer who can take a later step', async () => {
    asRole('viewer');
    mockGet.mockResolvedValue({ ...NOTHING, has_datasource: true, has_suite: true });
    renderPanel();

    expect(
      await screen.findByText('Someone with edit access to a suite adds checks.'),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Hide']);
  });

  it('is not shown once every step is done', async () => {
    asRole('admin');
    mockGet.mockResolvedValue({
      has_datasource: true,
      has_suite: true,
      has_check: true,
      has_run: true,
      complete: true,
    });
    const { container } = renderPanel();

    await vi.waitFor(() => expect(mockGet).toHaveBeenCalled());
    await Promise.resolve();
    expect(container.querySelector('[data-testid="get-started-panel"]')).toBeNull();
  });

  it('is not shown when the status cannot be loaded', async () => {
    asRole('admin');
    mockGet.mockRejectedValue(new Error('down'));
    const { container } = renderPanel();

    await vi.waitFor(() => expect(mockGet).toHaveBeenCalled());
    await Promise.resolve();
    expect(container.querySelector('[data-testid="get-started-panel"]')).toBeNull();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('stays hidden after Hide, across a remount', async () => {
    asRole('admin');
    mockGet.mockResolvedValue(NOTHING);
    const first = renderPanel();
    await userEvent.click(await screen.findByRole('button', { name: 'Hide' }));
    expect(screen.queryByTestId('get-started-panel')).not.toBeInTheDocument();
    first.unmount();

    renderPanel();
    await vi.waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    await Promise.resolve();
    expect(screen.queryByTestId('get-started-panel')).not.toBeInTheDocument();
  });
});
