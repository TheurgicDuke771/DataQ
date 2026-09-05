import { EyeInvisibleOutlined, EyeOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Flex, Input, Spin, Tag, Typography } from 'antd';
import { useState } from 'react';

import { type AdminWebhook, listAdminWebhooks } from '../../api/admin';
import { PROVIDER_CALLBACK_NOUNS, PROVIDER_LABELS } from '../../api/triggerBindings';
import { useAsyncData } from '../../hooks/useAsyncData';
import {
  InventorySyncSection,
  PollingHealthSection,
  RegenerateSecretButton,
} from './IntegrationsPanels';

/**
 * Inbound orchestration-webhook URLs (#490) — one copy-paste target per orchestration provider
 * (ADF / Airflow / dbt) to notify DataQ on pipeline completion.
 */
export function AdminIntegrations() {
  return (
    <Flex vertical gap={16}>
      <WebhooksCard />
      <PollingHealthSection />
      <InventorySyncSection />
    </Flex>
  );
}

function WebhooksCard() {
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
          {/* The auth mode is what tells an operator whether a leaked URL is
              itself a credential, so it belongs in the header, not the footnote. */}
          <Tag color={secretBearing ? 'orange' : 'green'}>
            {secretBearing ? 'Auth: shared secret in URL' : 'Auth: HMAC signature'}
          </Tag>
          {!webhook.token_configured && <Tag color="error">webhook secret not set</Tag>}
        </Flex>
      }
      extra={<RegenerateSecretButton webhook={webhook} />}
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
            Common-Alert-Schema payload mapping.
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
