import { Alert, Button, Card, Descriptions, Flex, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { testAuthEmail } from '../../api/admin';
import { authMethodLabel } from '../../auth/config';
import { LlmSettingsPanel } from '../../components/admin/LlmSettingsPanel';
import { NotificationChannelsPanel } from '../../components/admin/NotificationChannelsPanel';
import { CoveragePanel } from './CoveragePanel';
import { PrivacyPanel } from './PrivacyPanel';
import { ScoringPanel } from './ScoringPanel';
import { TelemetryPanel } from './TelemetryPanel';
import { WorkspaceConfigPanels } from './WorkspaceConfigPanels';
import { docsUrl } from '../../utils/docs';
import { type FetchFailure, fetchFailure } from '../../utils/errors';

/** Workspace settings: general facts + SMTP pre-flight, notification channels, LLM provider,
 *  the privacy, scoring, automatic-coverage and usage-telemetry settings, the read-only sign-in
 *  and retention configuration, the secret-store notice and where destructive actions live. */
export function AdminSettings() {
  return (
    <Flex vertical gap={16}>
      <GeneralCard />
      <NotificationsCard />
      <LlmSettingsPanel />
      <PrivacyPanel />
      <WorkspaceConfigPanels />
      <ScoringPanel />
      <CoveragePanel />
      <TelemetryPanel />
      <SecretsCard />
      <DestructiveActionsCard />
    </Flex>
  );
}

/**
 * Authentication readout + the SMTP pre-flight test (#737, ADR 0032 decision 7): "send me a test
 * mail" so a misconfigured email OTP mailer is caught at install time.
 */
function GeneralCard() {
  // The outcome stays on the card rather than only in a toast: a failing
  // pre-flight is something an admin acts on, and the request ID is what the
  // server log is searched by.
  const [outcome, setOutcome] = useState<
    { ok: true; to: string } | { ok: false; failure: FetchFailure } | null
  >(null);
  const [loading, setLoading] = useState(false);

  const onTestAuthEmail = async () => {
    setLoading(true);
    setOutcome(null);
    try {
      const { to } = await testAuthEmail();
      setOutcome({ ok: true, to });
    } catch (err) {
      setOutcome({ ok: false, failure: fetchFailure(err) });
    } finally {
      setLoading(false);
    }
  };

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
        <Button
          onClick={() => void onTestAuthEmail()}
          loading={loading}
          style={{ alignSelf: 'flex-start' }}
        >
          Send test email
        </Button>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Sends a real message to your own address over the configured email sign-in mailer (
          <Typography.Text code>AUTH_EMAIL_*</Typography.Text>). If it doesn&apos;t arrive, the
          error names the transport stage that failed — connect, TLS, auth, or send.
        </Typography.Text>
        {outcome?.ok && (
          <Alert
            type="success"
            showIcon
            title={`Test email sent to ${outcome.to} — check your inbox.`}
            description="The mailer accepted the message. Delivery to the inbox is the relay's business from here, so an accepted send that never arrives points at the relay, not at this configuration."
          />
        )}
        {outcome && !outcome.ok && (
          <Alert
            type="error"
            showIcon
            title="SMTP pre-flight test failed"
            description={
              <Flex vertical gap={4}>
                <span>{outcome.failure.message}</span>
                {outcome.failure.requestId && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    Request ID: <Typography.Text code>{outcome.failure.requestId}</Typography.Text>
                  </Typography.Text>
                )}
              </Flex>
            }
          />
        )}
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
        title="Channels here, thresholds per suite, workspace defaults in the deployment"
        description={
          <span>
            Destinations are defined once below as channels; each suite then picks which of them it
            alerts through and at what threshold (fail / warn / always) — open a suite from{' '}
            <Link to="/suites" className="dq-text-link">
              Suites
            </Link>{' '}
            to do that. Workspace-wide default destinations are set by the deployment&apos;s
            configuration (<Typography.Text code>TEAMS_WEBHOOK_SECRET_NAME</Typography.Text>,{' '}
            <Typography.Text code>SLACK_WEBHOOK_SECRET_NAME</Typography.Text>,{' '}
            <Typography.Text code>EMAIL_*</Typography.Text>), not here; every suite alerts through
            them in addition to its linked channels. See the{' '}
            <a
              href={docsUrl('guides/notifications/#channels')}
              target="_blank"
              rel="noreferrer"
              className="dq-text-link"
            >
              notifications guide
            </a>{' '}
            for the variables.
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

/** Nothing destroys the workspace as a whole — it is the deployment — so each destructive action
 *  sits next to the thing it destroys. This card says where. */
function DestructiveActionsCard() {
  return (
    <Card title="Destructive actions" size="small">
      <Flex vertical gap={8}>
        <Typography.Text type="secondary">
          There is no workspace-level delete: the workspace is the deployment itself. Each
          destructive action lives next to what it removes, and every one is audited.
        </Typography.Text>
        <ul style={{ margin: 0, paddingInlineStart: 20 }}>
          <li>
            <Link to="/admin/suites" className="dq-text-link">
              Suites (admin)
            </Link>{' '}
            — delete any suite, with its runs and results, or transfer its ownership.
          </li>
          <li>
            <Link to="/connections" className="dq-text-link">
              Connections
            </Link>{' '}
            — delete a connection once no suite depends on it; rotate its credential with
            Re-authenticate.
          </li>
          <li>
            <Link to="/admin/members" className="dq-text-link">
              Members
            </Link>{' '}
            — remove or offboard a member; their keys and sessions stop working on their next
            request.
          </li>
          <li>
            <Link to="/admin/compliance" className="dq-text-link">
              Compliance
            </Link>{' '}
            — erase one person&apos;s values from stored results and incident evidence.
          </li>
          <li>
            Run history is never purged by hand: the retention clocks above remove failing-row
            samples on schedule and keep the rows.
          </li>
        </ul>
      </Flex>
    </Card>
  );
}
