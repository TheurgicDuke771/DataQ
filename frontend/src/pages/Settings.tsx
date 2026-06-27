import { Alert, Card, Descriptions, Spin, Tabs, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { useMe } from '../auth/useMe';
import { Forbidden } from '../components/Forbidden';
import { Page } from '../components/layout/Page';

/**
 * Workspace Settings (`/settings`, ADR 0022 SettingsScreen). A tabbed shell —
 * General · Secrets · Notifications · Danger zone.
 *
 * There is **no settings/preferences backend** in v1, so this ships the shell +
 * only the controls a real backend backs (notifications are configured per
 * suite; the rest are clearly-labelled placeholders — feature honesty). No
 * hardcoded Azure resource names: the secret store is described generically
 * (Key Vault is one impl behind the seam — ADR 0010/0013).
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
      <Tabs
        defaultActiveKey="general"
        items={[
          { key: 'general', label: 'General', children: <GeneralTab /> },
          { key: 'secrets', label: 'Secrets', children: <SecretsTab /> },
          { key: 'notifications', label: 'Notifications', children: <NotificationsTab /> },
          { key: 'danger', label: 'Danger zone', children: <DangerTab /> },
        ]}
      />
    </Page>
  );
}

function GeneralTab() {
  return (
    <Card size="small">
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Workspace">DataQ</Descriptions.Item>
        <Descriptions.Item label="Tenancy">
          <Tag>Single tenant</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Authentication">Azure AD (MSAL)</Descriptions.Item>
      </Descriptions>
    </Card>
  );
}

function SecretsTab() {
  return (
    <Alert
      type="info"
      showIcon
      message="Credentials live in the secret store, never the database"
      description="Connection credentials and notification webhooks are written through the configured secret store (Azure Key Vault in production) and referenced only by key. There's nothing to edit here — secrets are rotated from the connection's Re-authenticate action."
    />
  );
}

function NotificationsTab() {
  return (
    <Alert
      type="info"
      showIcon
      message="Alerts are configured per suite"
      description={
        <span>
          Microsoft Teams alerts (webhook + fail / warn / always threshold) are set on each suite so
          the owning team is notified for their data. Open a suite from{' '}
          <Link to="/suites">Suites</Link> to configure its notifications. A workspace-wide default
          channel is a post-v1 follow-up.
        </span>
      }
    />
  );
}

function DangerTab() {
  return (
    <Alert
      type="warning"
      showIcon
      message="No destructive workspace actions in v1"
      description="Workspace-level danger-zone actions (transfer ownership, purge run history, delete workspace) aren't available yet. Per-entity delete lives on each connection / suite."
    />
  );
}
