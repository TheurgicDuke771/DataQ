import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  getDeploymentPosture,
  listAdminAccess,
  listAdminSuites,
  listAdminUsers,
  listAuditEvents,
} from '../../../src/api/admin';
import {
  ACCESS,
  AUDIT_PAGE_1,
  DEPLOYMENT_POSTURE,
  SUITE,
  USER,
  renderAdminAt,
} from './adminFixtures';

vi.mock('../../../src/api/admin', () => ({
  listAdminSuites: vi.fn(),
  listAdminUsers: vi.fn(),
  listAdminAccess: vi.fn(),
  listAdminWebhooks: vi.fn(),
  setAdminUserRole: vi.fn(),
  listAuditEvents: vi.fn(),
  getDeploymentPosture: vi.fn(),
  testAuthEmail: vi.fn(),
  WORKSPACE_ROLES: ['admin', 'member', 'viewer'],
}));

vi.mock('../../../src/api/llm', () => ({
  getLlmConfig: vi.fn(() => new Promise(() => {})),
  updateLlmConfig: vi.fn(),
  testLlmConfig: vi.fn(),
}));

vi.mock('../../../src/api/notificationChannels', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../src/api/notificationChannels')>()),
  listChannels: vi.fn(() => new Promise(() => {})),
  createChannel: vi.fn(),
  updateChannel: vi.fn(),
  deleteChannel: vi.fn(),
}));

const mockSuites = vi.mocked(listAdminSuites);
const mockUsers = vi.mocked(listAdminUsers);
const mockAccess = vi.mocked(listAdminAccess);
const mockAudit = vi.mocked(listAuditEvents);
const mockPosture = vi.mocked(getDeploymentPosture);

beforeEach(() => {
  mockSuites.mockResolvedValue([SUITE]);
  mockUsers.mockResolvedValue([USER]);
  mockAccess.mockResolvedValue(ACCESS);
  mockAudit.mockResolvedValue(AUDIT_PAGE_1);
  mockPosture.mockResolvedValue(DEPLOYMENT_POSTURE);
});
afterEach(() => vi.clearAllMocks());

describe('admin routing', () => {
  it('redirects /admin to the overview sub-page', async () => {
    renderAdminAt('/admin');
    expect(await screen.findByText('Access grants')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true');
  });

  it('deep-links straight into a sub-page with its tab selected', async () => {
    renderAdminAt('/admin/compliance');
    expect(await screen.findByText('Audit log')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Compliance' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('loads only the sub-page you are on — compliance fetches no member or suite data', async () => {
    renderAdminAt('/admin/compliance');
    await screen.findByText('check.update');
    expect(mockSuites).not.toHaveBeenCalled();
    expect(mockUsers).not.toHaveBeenCalled();
    expect(mockAccess).not.toHaveBeenCalled();
  });

  it('switching tabs navigates and loads the newly-shown page', async () => {
    const user = userEvent.setup();
    renderAdminAt('/admin/compliance');
    await screen.findByText('check.update');

    await user.click(screen.getByRole('tab', { name: 'Members' }));

    expect(await screen.findByText('bob@x.io')).toBeInTheDocument();
    await waitFor(() => expect(mockUsers).toHaveBeenCalled());
    // The compliance page unmounts with its tab; nothing re-fetches it.
    expect(mockSuites).not.toHaveBeenCalled();
  });

  it('renders an in-brand 404 for an unknown admin sub-page', async () => {
    renderAdminAt('/admin/nope');
    expect(await screen.findByText(/404/)).toBeInTheDocument();
  });

  it('gates every sub-page at the route, fetching nothing for a non-admin', async () => {
    renderAdminAt('/admin/compliance', 'member');
    expect(await screen.findByText('403 — Forbidden')).toBeInTheDocument();
    expect(screen.getByText(/restricted to workspace admins/i)).toBeInTheDocument();
    expect(mockAudit).not.toHaveBeenCalled();
    expect(mockPosture).not.toHaveBeenCalled();
    expect(mockSuites).not.toHaveBeenCalled();
    expect(mockUsers).not.toHaveBeenCalled();
  });

  it('a viewer is refused the members sub-page too', async () => {
    renderAdminAt('/admin/members', 'viewer');
    expect(await screen.findByText('403 — Forbidden')).toBeInTheDocument();
    expect(mockUsers).not.toHaveBeenCalled();
  });

  it('redirects the retired /settings URL into the admin settings tab', async () => {
    renderAdminAt('/settings');
    expect(await screen.findByRole('tab', { name: 'Settings' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('refuses /settings for a non-admin rather than bouncing them into /admin', async () => {
    renderAdminAt('/settings', 'member');
    expect(await screen.findByText('403 — Forbidden')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Settings' })).not.toBeInTheDocument();
  });
});
