import { Alert, Spin, Typography } from 'antd';

import { useMe } from '../auth/useMe';
import { Forbidden } from '../components/Forbidden';
import { Page } from '../components/layout/Page';

/**
 * Workspace Settings (`/settings`, ADR 0022). Phase 0.2 lands the routed shell +
 * the workspace-admin gate; Phase 5.2 fills in the tabs (General · Secrets ·
 * Notifications · Danger zone) — and only the controls a backend actually backs
 * (the rest stay labelled placeholders, KPI/feature honesty).
 *
 * Admin-only like the Admin page: gated on `/me`'s server-driven
 * `is_workspace_admin`; a non-admin who deep-links here sees Forbidden.
 */
export function Settings() {
  const me = useMe();

  if (me.status === 'loading') {
    return <Spin size="large" style={{ marginTop: 80 }} />;
  }
  if (me.status === 'error') {
    return (
      <Alert type="error" showIcon message="Couldn't verify your access" description={me.error} />
    );
  }
  if (!me.data.is_workspace_admin) {
    return <Forbidden message="Workspace settings are restricted to workspace admins." />;
  }

  return (
    <Page gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Settings
      </Typography.Title>
      <Alert
        type="info"
        showIcon
        message="Workspace settings are coming soon."
        description="General, secrets, notifications, and danger-zone controls land in a later Week-6 step — each wired only once a backend backs it."
      />
    </Page>
  );
}
