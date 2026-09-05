import { Alert, App, Button, Card, Descriptions, Flex, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';

import { testAuthEmail } from '../../api/admin';
import { authMethodLabel } from '../../auth/config';
import { LlmSettingsPanel } from '../../components/admin/LlmSettingsPanel';
import { NotificationChannelsPanel } from '../../components/admin/NotificationChannelsPanel';
import { useAsyncAction } from '../../hooks/useAsyncAction';

/** Workspace settings: general facts + SMTP pre-flight, notification channels, LLM provider,
 *  the secret-store notice and the danger zone (folded in from `/settings`, #1694). */
export function AdminSettings() {
  return (
    <Flex vertical gap={16}>
      <GeneralCard />
      <NotificationsCard />
      <LlmSettingsPanel />
      <SecretsCard />
      <DangerCard />
    </Flex>
  );
}

/**
 * Authentication readout + the SMTP pre-flight test (#737, ADR 0032 decision 7): "send me a test
 * mail" so a misconfigured email OTP mailer is caught at install time.
 */
function GeneralCard() {
  const { message } = App.useApp();
  const { run, loading } = useAsyncAction('SMTP pre-flight test failed');

  const onTestAuthEmail = () =>
    run(async () => {
      const { to } = await testAuthEmail();
      message.success(`Test email sent to ${to} — check your inbox.`);
    });

  return (
    <Card title="General" size="small">
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Workspace">DataQ</Descriptions.Item>
        <Descriptions.Item label="Tenancy">
          <Tag>Single tenant</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Authentication">{authMethodLabel}</Descriptions.Item>
      </Descriptions>
      <Flex vertical gap={4} style={{ marginTop: 16 }}>
        <Button onClick={onTestAuthEmail} loading={loading} style={{ alignSelf: 'flex-start' }}>
          Send test email
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Sends a real message to your own address over the configured email sign-in mailer (
          <Typography.Text code>AUTH_EMAIL_*</Typography.Text>). If it doesn&apos;t arrive, the
          error names the transport stage that failed — connect, TLS, auth, or send.
        </Typography.Text>
      </Flex>
    </Card>
  );
}

function NotificationsCard() {
  return (
    <Flex vertical gap={16}>
      <Alert
        type="info"
        showIcon
        title="Alerts are configured per suite"
        description={
          <span>
            Teams, Slack and email alerts (webhook/recipient + fail / warn / always threshold) are
            set on each suite so the owning team is notified for their data, falling back to the
            workspace-wide default configured for the deployment when a suite sets none. Open a
            suite from <Link to="/suites">Suites</Link> to configure its notifications — including
            linking the reusable channels managed below.
          </span>
        }
      />
      <NotificationChannelsPanel />
    </Flex>
  );
}

function SecretsCard() {
  return (
    <Alert
      type="info"
      showIcon
      title="Credentials live in the secret store, never the database"
      description="Connection credentials and notification webhooks are written through the configured secret store (Azure Key Vault, AWS Secrets Manager, or OpenBao, depending on the deployment) and referenced only by key. There's nothing to edit here — secrets are rotated from the connection's Re-authenticate action."
    />
  );
}

function DangerCard() {
  return (
    <Alert
      type="warning"
      showIcon
      title="No destructive workspace actions in v1"
      description="Workspace-level danger-zone actions (transfer ownership, purge run history, delete workspace) aren't available yet. Per-entity delete lives on each connection / suite."
    />
  );
}
