import { App as AntApp } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  type AdminAccess,
  type AdminSuite,
  type AdminUser,
  type AuditEventPage,
  type DeploymentPosture,
  getDeploymentPosture,
  listAdminAccess,
  listAdminSuites,
  listAdminUsers,
  listAuditEvents,
} from '../../src/api/admin';
import { getLlmConfig, type LlmConfig } from '../../src/api/llm';
import type { MeResponse } from '../../src/api/me';
import { MeContext } from '../../src/auth/meContext';
import type { AsyncState } from '../../src/hooks/useAsyncData';
import { Admin } from '../../src/pages/Admin';

vi.mock('../../src/api/admin', () => ({
  listAdminSuites: vi.fn(),
  listAdminUsers: vi.fn(),
  listAdminAccess: vi.fn(),
  setAdminUserRole: vi.fn(),
  listAuditEvents: vi.fn(),
  getDeploymentPosture: vi.fn(),
  // A VALUE export, not a function — the role editor iterates it to build its options, so omitting
  // it from the mock takes the whole page's render down (which is exactly what it did).
  WORKSPACE_ROLES: ['admin', 'member', 'viewer'],
}));

vi.mock('../../src/api/llm', () => ({
  getLlmConfig: vi.fn(),
  updateLlmConfig: vi.fn(),
  testLlmConfig: vi.fn(),
}));

const mockSuites = vi.mocked(listAdminSuites);
const mockUsers = vi.mocked(listAdminUsers);
const mockAccess = vi.mocked(listAdminAccess);
const mockAuditEvents = vi.mocked(listAuditEvents);
const mockDeploymentPosture = vi.mocked(getDeploymentPosture);
const mockLlmConfig = vi.mocked(getLlmConfig);

const LLM_CONFIG: LlmConfig = {
  configured: false,
  provider: null,
  base_url: null,
  model: null,
  structured_output: null,
  enabled: false,
  has_credential: false,
  updated_at: null,
};

const adminMe: AsyncState<MeResponse> = {
  status: 'ok',
  data: {
    id: 'u-1',
    aad_object_id: 'oid-1',
    email: 'admin@dataq.io',
    display_name: 'Ada Admin',
    last_seen_at: null,
    role: 'admin',
    is_workspace_admin: true,
  },
};

const SUITE: AdminSuite = {
  id: 's1',
  name: 'Finance DQ',
  connection_name: 'sf-prod',
  connection_type: 'snowflake',
  env: 'prod',
  owner_id: 'o1',
  owner_email: 'olive@x.io',
  owner_name: 'Olive Owner',
  check_count: 7,
  share_count: 2,
  created_at: '2026-06-10T10:00:00Z',
  updated_at: '2026-06-10T10:00:00Z',
};
const USER: AdminUser = {
  id: 'u9',
  email: 'bob@x.io',
  display_name: null,
  last_seen_at: null,
  created_at: '2026-06-01T00:00:00Z',
  owned_suite_count: 3,
  shared_suite_count: 1,
  role: 'member' as const,
  allowlist_admin: false,
};
const ACCESS: AdminAccess[] = [
  {
    suite_id: 's1',
    suite_name: 'Finance DQ',
    user_id: 'o1',
    user_email: 'olive@x.io',
    user_name: 'Olive Owner',
    permission: 'owner',
  },
  {
    suite_id: 's1',
    suite_name: 'Finance DQ',
    user_id: 'e1',
    user_email: 'ed@x.io',
    user_name: null,
    permission: 'edit',
  },
];

const AUDIT_PAGE_1: AuditEventPage = {
  events: [
    {
      id: 'ev1',
      occurred_at: '2026-08-20T10:00:00Z',
      action_class: 'config',
      action: 'check.update',
      entity_type: 'check',
      entity_id: 'c1',
      actor_user_id: 'u9',
      actor_kind: 'user',
      actor_label: 'olive@x.io',
      actor_display: 'olive@x.io',
      before: { threshold: 1 },
      after: { threshold: 2 },
      request_id: 'req-1',
    },
  ],
  total: 30,
  truncated: true,
  retention_days: 365,
  retained_since: '2025-08-20T10:00:00Z',
};
const AUDIT_PAGE_2: AuditEventPage = {
  events: [
    {
      id: 'ev2',
      occurred_at: '2026-08-01T10:00:00Z',
      action_class: 'access',
      action: 'run_results.read',
      entity_type: 'run',
      entity_id: 'r1',
      actor_user_id: 'u9',
      actor_kind: 'user',
      actor_label: 'olive@x.io',
      actor_display: 'olive@x.io',
      before: null,
      after: { exposed: false },
      request_id: null,
    },
  ],
  total: 30,
  truncated: false,
  retention_days: 365,
  retained_since: '2025-08-20T10:00:00Z',
};
const DEPLOYMENT_POSTURE: DeploymentPosture = {
  region: 'us-east-1',
  external_transfers: [
    { name: 'alert_delivery', enabled: true, detail: 'Alerts go to a configured webhook.' },
  ],
};

function renderAdmin(me: AsyncState<MeResponse>) {
  return render(
    <MemoryRouter>
      <AntApp>
        <MeContext.Provider value={me}>
          <Admin />
        </MeContext.Provider>
      </AntApp>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mockSuites.mockResolvedValue([SUITE]);
  mockUsers.mockResolvedValue([USER]);
  mockAccess.mockResolvedValue(ACCESS);
  mockAuditEvents.mockResolvedValue(AUDIT_PAGE_1);
  mockDeploymentPosture.mockResolvedValue(DEPLOYMENT_POSTURE);
  mockLlmConfig.mockResolvedValue(LLM_CONFIG);
});
afterEach(() => vi.clearAllMocks());

describe('Admin', () => {
  it('shows the Forbidden page for a non-admin and fetches nothing, incl. the LLM panel', () => {
    renderAdmin({ ...adminMe, data: { ...adminMe.data, is_workspace_admin: false } });
    expect(screen.getByText('403 — Forbidden')).toBeInTheDocument();
    expect(mockSuites).not.toHaveBeenCalled();
    expect(mockLlmConfig).not.toHaveBeenCalled();
    expect(screen.queryByText('LLM provider')).not.toBeInTheDocument();
  });

  it('renders KPI cards + all suites + members + access in one view (no tabs)', async () => {
    renderAdmin(adminMe);
    // No tabs in the reconciled layout.
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    // KPI labels.
    expect(screen.getByText('Suites')).toBeInTheDocument();
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('Access grants')).toBeInTheDocument();
    // All three tables render without any interaction. 'Finance DQ' appears in
    // both the suites table and the access rows (so use findAllByText).
    expect((await screen.findAllByText('Finance DQ')).length).toBeGreaterThan(0);
    expect(screen.getByText('bob@x.io')).toBeInTheDocument(); // members
    expect(screen.getByText('owner')).toBeInTheDocument(); // access permission tag
    expect(screen.getByText('edit')).toBeInTheDocument();
    // The LLM provider panel (#1511) renders for an admin.
    expect(await screen.findByText('LLM provider')).toBeInTheDocument();
    expect(mockLlmConfig).toHaveBeenCalled();
  });

  it('surfaces a load error for a failed dataset', async () => {
    mockSuites.mockRejectedValue(new Error('boom'));
    renderAdmin(adminMe);
    expect(await screen.findByText('Failed to load suites')).toBeInTheDocument();
  });

  it('renders the audit log with its honesty fields and the deployment posture table', async () => {
    renderAdmin(adminMe);
    expect(await screen.findByText('check.update')).toBeInTheDocument();
    expect(
      screen.getByText(/Retained 365 days .* events older than that have been swept/),
    ).toBeInTheDocument();
    expect(screen.getByTitle('2')).toBeInTheDocument(); // real total via antd's pager

    expect(screen.getByText('us-east-1')).toBeInTheDocument();
    expect(screen.getByText('alert_delivery')).toBeInTheDocument();
    expect(screen.getByText('live')).toBeInTheDocument();
  });

  it('Next actually refetches the next page — not just a local page-number bump', async () => {
    // Regression: page state bumped without calling reload(), so the fetch never fired.
    mockAuditEvents.mockResolvedValueOnce(AUDIT_PAGE_1).mockResolvedValueOnce(AUDIT_PAGE_2);
    const user = userEvent.setup();
    const { container } = renderAdmin(adminMe);
    expect(await screen.findByText('check.update')).toBeInTheDocument();

    const next = container.querySelector('.ant-pagination-next button');
    expect(next).not.toBeNull();
    await user.click(next as HTMLButtonElement);

    expect(await screen.findByText('run_results.read')).toBeInTheDocument();
    expect(screen.queryByText('check.update')).not.toBeInTheDocument();
    expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({ offset: 25 });
  });

  it('Search applies the pending filters and resets to the first page', async () => {
    const user = userEvent.setup();
    renderAdmin(adminMe);
    await screen.findByText('check.update');

    await user.type(screen.getByPlaceholderText('e.g. suite'), 'suite');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    });
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({
      entity_type: 'suite',
      offset: 0,
    });
  });

  it('Next does not apply an unsubmitted filter edit to the page fetch', async () => {
    // Regression: a collapsed filter state let Next read a typed-but-unsubmitted edit.
    mockAuditEvents.mockResolvedValueOnce(AUDIT_PAGE_1).mockResolvedValueOnce(AUDIT_PAGE_2);
    const user = userEvent.setup();
    const { container } = renderAdmin(adminMe);
    await screen.findByText('check.update');

    await user.type(screen.getByPlaceholderText('e.g. suite'), 'suite'); // not submitted
    const next = container.querySelector('.ant-pagination-next button');
    await user.click(next as HTMLButtonElement);

    await waitFor(() => {
      expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    });
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({
      entity_type: undefined,
      offset: 25,
    });
  });

  it('trims whitespace from the entity type filter before sending it', async () => {
    const user = userEvent.setup();
    renderAdmin(adminMe);
    await screen.findByText('check.update');

    await user.type(screen.getByPlaceholderText('e.g. suite'), '  suite  ');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => {
      expect(mockAuditEvents).toHaveBeenCalledTimes(2);
    });
    expect(mockAuditEvents.mock.calls[1][0]).toMatchObject({ entity_type: 'suite' });
  });

  it('names the disabled sweep instead of implying a normal retention window', async () => {
    mockAuditEvents.mockResolvedValue({ ...AUDIT_PAGE_1, retention_days: 0, retained_since: null });
    renderAdmin(adminMe);
    expect(
      await screen.findByText(/Retention sweep is disabled .* the log is unbounded/),
    ).toBeInTheDocument();
  });
});
