import { App as AntApp } from 'antd';
import { render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { Suspense } from 'react';
import { MemoryRouter, Routes } from 'react-router-dom';

import type {
  AdminAccess,
  AdminSuite,
  AdminUser,
  AdminWebhook,
  AuditEventPage,
  DeploymentPosture,
  WorkspaceRole,
} from '../../../src/api/admin';
import type { MeResponse } from '../../../src/api/me';
import { MeContext } from '../../../src/auth/meContext';
import type { AsyncState } from '../../../src/hooks/useAsyncData';
import { ADMIN_ROUTES, SETTINGS_REDIRECT_ROUTE } from '../../../src/pages/admin/routes';

/**
 * Route tests resolve TWO lazy chunks (the layout, then the sub-page) before any content
 * appears, and RTL's `findBy` default is 1s regardless of vitest's testTimeout — tight enough
 * on a slow shared CI runner to fail while the app is merely still loading.
 */
export const LAZY = { timeout: 10_000 };

export function meAt(role: WorkspaceRole): AsyncState<MeResponse> {
  return {
    status: 'ok',
    data: {
      id: 'u-1',
      aad_object_id: 'oid-1',
      email: `${role}@dataq.io`,
      display_name: 'Ada Admin',
      last_seen_at: null,
      role,
      is_workspace_admin: role === 'admin',
    },
  };
}

/** Render the REAL gated admin route tree (the one App mounts) at `path`. */
export function renderAdminAt(path: string, role: WorkspaceRole = 'admin') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AntApp>
        <MeContext.Provider value={meAt(role)}>
          <Suspense fallback={<div>loading admin</div>}>
            <Routes>
              {ADMIN_ROUTES}
              {SETTINGS_REDIRECT_ROUTE}
            </Routes>
          </Suspense>
        </MeContext.Provider>
      </AntApp>
    </MemoryRouter>,
  );
}

/** Render one sub-page directly, without the route gate. */
export function renderSubPage(node: ReactNode) {
  return render(
    <MemoryRouter>
      <AntApp>
        <MeContext.Provider value={meAt('admin')}>{node}</MeContext.Provider>
      </AntApp>
    </MemoryRouter>,
  );
}

export const SUITE: AdminSuite = {
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

export const USER: AdminUser = {
  id: 'u9',
  email: 'bob@x.io',
  display_name: null,
  last_seen_at: null,
  created_at: '2026-06-01T00:00:00Z',
  owned_suite_count: 3,
  shared_suite_count: 1,
  role: 'member',
  allowlist_admin: false,
};

export const ACCESS: AdminAccess[] = [
  {
    suite_id: 's1',
    suite_name: 'Finance DQ',
    user_id: 'o1',
    user_email: 'olive@x.io',
    user_name: 'Olive Owner',
    permission: 'owner',
    grant_id: null,
  },
  {
    suite_id: 's1',
    suite_name: 'Finance DQ',
    user_id: 'e1',
    user_email: 'ed@x.io',
    user_name: null,
    permission: 'edit',
    grant_id: 'g1',
  },
];

export const AUDIT_PAGE_1: AuditEventPage = {
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

export const AUDIT_PAGE_2: AuditEventPage = {
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

export const DEPLOYMENT_POSTURE: DeploymentPosture = {
  region: 'us-east-1',
  zero_sample_mode: false,
  external_transfers: [
    { name: 'alert_delivery', enabled: true, detail: 'Alerts go to a configured webhook.' },
  ],
};

export const WEBHOOKS: AdminWebhook[] = [
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
    auth: 'HMAC-SHA256 signature header (X-DataQ-Signature)',
    inbound_url: 'https://dataq.example.com/api/v1/orchestration/events/airflow',
    token_configured: true,
    signing_secret_name: 'airflow-webhook-secret',
    connection_names: ['airflow-prod'],
  },
  {
    provider: 'dbt',
    auth: 'HMAC-SHA256 signature header (X-DataQ-Signature)',
    inbound_url: 'https://dataq.example.com/api/v1/orchestration/events/dbt',
    token_configured: true,
    signing_secret_name: 'dbt-webhook-secret',
    connection_names: ['analytics-dbt'],
  },
];
