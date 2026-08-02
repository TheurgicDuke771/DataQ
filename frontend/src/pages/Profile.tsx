import { Alert, App, Avatar, Card, Descriptions, Flex, Spin, Tag, Typography } from 'antd';
import { TeamOutlined, UserOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { authMethodLabel } from '../auth/config';
import { useMe } from '../auth/useMe';
import { useSaveDisplayName } from '../auth/useSaveDisplayName';
import { ApiKeysPanel } from '../components/profile/ApiKeysPanel';
import { Page } from '../components/layout/Page';
import { BRAND } from '../theme';
import { PageError } from '../components/feedback/PageError';
import { errorMessage } from '../utils/errors';

/**
 * Profile (`/profile`, ADR 0022 ProfileScreen). The account screen: an identity
 * card + workspace facts, both rendered only from `/me` (KPI honesty — no
 * fabricated fields), plus an Alert-channels card.
 *
 * Alerting is configured **per suite** (the W6 `ResultPublisher` + per-suite
 * notification config), not per user, so this card states that honestly and
 * links to the suites rather than showing per-user toggles no backend backs.
 */
export function Profile() {
  const me = useMe();
  const saveDisplayName = useSaveDisplayName();
  const { message } = App.useApp();
  const [savingName, setSavingName] = useState(false);

  if (me.status === 'loading') {
    return <Spin size="large" style={{ marginTop: 80 }} />;
  }
  if (me.status === 'error') {
    return (
      <PageError
        error={me.error}
        kind={me.kind}
        httpStatus={me.httpStatus}
        requestId={me.requestId}
      />
    );
  }

  const { display_name, email, last_seen_at, is_workspace_admin } = me.data;
  const name = display_name ?? email;
  const initial = (name || '?').trim().charAt(0).toUpperCase();

  // Self-service override (#1139) — editable in EVERY mode, not just `otp`. An
  // AAD user's header name still comes straight off the token (unaffected by
  // this), but the stored row is what shares/admin lists render, so being able
  // to set it here is not otp-specific.
  //
  // Compared against `name` (the RENDERED value — display_name, falling back
  // to email), not the raw `display_name`: antd's editable textarea starts
  // from what's on screen, so for a null-display_name user that's the email.
  // Comparing against the nullable `display_name` instead meant an unchanged
  // blur (no edit at all — just focus-then-leave) always looked "changed"
  // for that user and PATCHed their own email address in as their name.
  const onNameChange = async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed || trimmed === name) return;
    setSavingName(true);
    try {
      await saveDisplayName(trimmed);
      message.success('Name updated.');
    } catch (err) {
      message.error(`Could not update your name: ${errorMessage(err)}`);
    } finally {
      setSavingName(false);
    }
  };

  return (
    <Page width="form" gap={16}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Profile
      </Typography.Title>

      <Card>
        <Flex gap={16} align="center">
          <Avatar size={56} style={{ backgroundColor: BRAND.primary }}>
            {initial}
          </Avatar>
          <Flex vertical gap={2}>
            <Typography.Text
              strong
              style={{ fontSize: 18 }}
              editable={{
                onChange: (value) => void onNameChange(value),
                tooltip: 'Edit your display name',
                maxLength: 256,
                triggerType: ['icon', 'text'],
              }}
              aria-busy={savingName}
            >
              {name}
            </Typography.Text>
            <Typography.Text type="secondary">{email}</Typography.Text>
            <span>
              <Tag color={is_workspace_admin ? 'gold' : 'default'}>
                {is_workspace_admin ? 'Workspace admin' : 'Member'}
              </Tag>
            </span>
          </Flex>
        </Flex>
      </Card>

      <Card title="Workspace" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="Authentication">{authMethodLabel}</Descriptions.Item>
          <Descriptions.Item label="Role">
            {is_workspace_admin ? 'Workspace admin' : 'Member'}
          </Descriptions.Item>
          <Descriptions.Item label="Last seen">{last_seen_at ?? '—'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        title={
          <Flex gap={8} align="center">
            <TeamOutlined /> Alert channels
          </Flex>
        }
        size="small"
      >
        <Alert
          type="info"
          showIcon
          icon={<UserOutlined />}
          title="DQ alerts are configured per suite"
          description={
            <span>
              Microsoft Teams alerts (webhook + fail / warn / always threshold) are set on each
              suite, so the right team is notified for the data they own. Open a suite from{' '}
              <Link to="/suites">Suites</Link> to configure its notifications.
            </span>
          }
        />
      </Card>

      <ApiKeysPanel />
    </Page>
  );
}
