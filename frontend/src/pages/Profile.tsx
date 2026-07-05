import { Alert, Avatar, Card, Descriptions, Flex, Spin, Tag, Typography } from 'antd';
import { TeamOutlined, UserOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';

import { useMe } from '../auth/useMe';
import { ApiKeysPanel } from '../components/profile/ApiKeysPanel';
import { Page } from '../components/layout/Page';
import { BRAND } from '../theme';

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

  if (me.status === 'loading') {
    return <Spin size="large" style={{ marginTop: 80 }} />;
  }
  if (me.status === 'error') {
    return (
      <Alert type="error" showIcon title="Failed to load your profile" description={me.error} />
    );
  }

  const { display_name, email, last_seen_at, is_workspace_admin } = me.data;
  const name = display_name ?? email;
  const initial = (name || '?').trim().charAt(0).toUpperCase();

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
            <Typography.Text strong style={{ fontSize: 18 }}>
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
          <Descriptions.Item label="Authentication">Azure AD (MSAL)</Descriptions.Item>
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
