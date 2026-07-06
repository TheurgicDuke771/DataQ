import { EyeInvisibleOutlined, EyeOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Descriptions, Flex, Input, Spin, Tabs, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { type AdminWebhook, listAdminWebhooks } from '../api/admin';
import { PROVIDER_CALLBACK_NOUNS, PROVIDER_LABELS } from '../api/triggerBindings';
import { useMe } from '../auth/useMe';
import { Forbidden } from '../components/Forbidden';
import { Page } from '../components/layout/Page';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Workspace Settings (`/settings`, ADR 0022 SettingsScreen). A tabbed shell —
 * General · Secrets · Webhooks · Notifications · Danger zone.
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
      <Alert type="error" showIcon title="Couldn't verify your access" description={me.error} />
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
          { key: 'webhooks', label: 'Webhooks', children: <WebhooksTab /> },
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
      title="Credentials live in the secret store, never the database"
      description="Connection credentials and notification webhooks are written through the configured secret store (Azure Key Vault in production) and referenced only by key. There's nothing to edit here — secrets are rotated from the connection's Re-authenticate action."
    />
  );
}

function NotificationsTab() {
  return (
    <Alert
      type="info"
      showIcon
      title="Alerts are configured per suite"
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
      title="No destructive workspace actions in v1"
      description="Workspace-level danger-zone actions (transfer ownership, purge run history, delete workspace) aren't available yet. Per-entity delete lives on each connection / suite."
    />
  );
}

/** Inbound orchestration-webhook URLs (#490) — one copy-paste target per
 *  orchestration provider (ADF / Airflow / dbt) to notify DataQ on pipeline
 *  completion. Admin-only (the page is already gated). */
function WebhooksTab() {
  const { state } = useAsyncData(listAdminWebhooks);
  return (
    <Card title="Inbound webhooks (orchestration)" size="small">
      <Flex vertical gap={12}>
        <Typography.Text type="secondary">
          Ready-to-paste URLs for an orchestrator to notify DataQ on pipeline/DAG completion. The
          ADF URL carries a shared secret in the query string — treat it as a credential.
        </Typography.Text>
        {state.status === 'loading' && <Spin size="large" />}
        {state.status === 'error' && (
          <Alert
            type="error"
            showIcon
            title="Failed to load webhook config"
            description={state.error}
          />
        )}
        {state.status === 'ok' && state.data.length === 0 && (
          <Typography.Text type="secondary">
            No orchestration connections configured.
          </Typography.Text>
        )}
        {state.status === 'ok' &&
          state.data.map((wh) => <WebhookRow key={wh.provider} webhook={wh} />)}
      </Flex>
    </Card>
  );
}

/** One provider's webhook URL. ADF embeds a secret, so it's masked behind a reveal
 *  toggle; copy always copies the real URL. */
function WebhookRow({ webhook }: { webhook: AdminWebhook }) {
  const [revealed, setRevealed] = useState(false);
  const secretBearing = webhook.provider === 'adf';
  // Mask only the token value, keeping the rest of the URL legible.
  const display =
    secretBearing && !revealed
      ? webhook.inbound_url.replace(/token=[^&]*/i, 'token=••••••••')
      : webhook.inbound_url;
  return (
    <Card
      size="small"
      type="inner"
      title={
        <Flex align="center" gap={8}>
          <Tag color={secretBearing ? 'geekblue' : 'cyan'}>{PROVIDER_LABELS[webhook.provider]}</Tag>
          {!webhook.token_configured && <Tag color="error">webhook secret not set</Tag>}
        </Flex>
      }
    >
      <Flex vertical gap={8}>
        <Flex align="center" gap={8}>
          <Input readOnly value={display} style={{ fontFamily: 'monospace' }} />
          {secretBearing && (
            <Button
              icon={revealed ? <EyeInvisibleOutlined /> : <EyeOutlined />}
              onClick={() => setRevealed((r) => !r)}
              title={revealed ? 'Hide token' : 'Reveal token'}
            />
          )}
          <Typography.Text copyable={{ text: webhook.inbound_url }} />
        </Flex>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {webhook.auth}
        </Typography.Text>
        {secretBearing ? (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Paste into Azure Monitor → Action Group → Webhook. Live delivery also needs the
            Common-Alert-Schema payload mapping (#492).
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Configured in the {PROVIDER_CALLBACK_NOUNS[webhook.provider]} callback snippet (HMAC);
            signing key in the secret store:{' '}
            <Typography.Text code>{webhook.signing_secret_name}</Typography.Text>.
          </Typography.Text>
        )}
        <Flex gap={4} wrap align="center">
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Connections:
          </Typography.Text>
          {webhook.connection_names.map((name) => (
            <Tag key={name}>{name}</Tag>
          ))}
        </Flex>
      </Flex>
    </Card>
  );
}
