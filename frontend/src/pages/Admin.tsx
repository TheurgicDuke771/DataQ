import {
  AppstoreOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
  KeyOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { Alert, Button, Card, Col, Flex, Input, Row, Spin, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useState } from 'react';

import {
  type AdminAccess,
  type AdminSuite,
  type AdminUser,
  type AdminWebhook,
  listAdminAccess,
  listAdminSuites,
  listAdminUsers,
  listAdminWebhooks,
} from '../api/admin';
import { useMe } from '../auth/useMe';
import { MetricCard } from '../components/dashboard/MetricCard';
import { Forbidden } from '../components/Forbidden';
import { Page } from '../components/layout/Page';
import { formatTimestamp } from '../components/results/resultsFormat';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';

/**
 * Workspace-admin control centre (#173): all suites / members / access overview.
 *
 * Layout reconciled to the prototype (ADR 0022 AdminScreen): KPI MetricCards over
 * stacked tables, **no tabs**. Presentation only — same `/admin/{suites,users,
 * access}` endpoints. Access is server-driven (gated on `/me`'s
 * `is_workspace_admin`; the endpoints re-enforce with 403); a non-admin deep-link
 * sees the Forbidden page and no data is fetched.
 */
export function Admin() {
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
    return <Forbidden message="The admin overview is restricted to workspace admins." />;
  }

  return <AdminOverview />;
}

/** Hooks live here so they only run for an admin (Admin renders this after the gate). */
function AdminOverview() {
  const suites = useAsyncData(listAdminSuites);
  const users = useAsyncData(listAdminUsers);
  const access = useAsyncData(listAdminAccess);

  return (
    <Page>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Admin
      </Typography.Title>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Suites"
            value={count(suites.state)}
            loading={suites.state.status === 'loading'}
            icon={<AppstoreOutlined />}
          />
        </Col>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Members"
            value={count(users.state)}
            loading={users.state.status === 'loading'}
            icon={<TeamOutlined />}
          />
        </Col>
        <Col xs={24} sm={8}>
          <MetricCard
            label="Access grants"
            value={count(access.state)}
            loading={access.state.status === 'loading'}
            icon={<KeyOutlined />}
          />
        </Col>
      </Row>

      <Section title="All suites">
        <DataTable
          state={suites.state}
          columns={SUITE_COLUMNS}
          rowKey={(s) => s.id}
          errorMessage="Failed to load suites"
        />
      </Section>

      <Section title="Members & access">
        <DataTable
          state={users.state}
          columns={USER_COLUMNS}
          rowKey={(u) => u.id}
          errorMessage="Failed to load members"
        />
        <DataTable
          state={access.state}
          columns={ACCESS_COLUMNS}
          // A user appears once per suite (owner or a single share row).
          rowKey={(a) => `${a.suite_id}:${a.user_id}`}
          errorMessage="Failed to load access overview"
        />
      </Section>

      <WebhooksSection />
    </Page>
  );
}

/** Inbound orchestration-webhook URLs (#490) — copy-paste targets for ADF / Airflow
 *  to notify DataQ on pipeline completion. Its own fetch so it loads independently. */
function WebhooksSection() {
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
            message="Failed to load webhook config"
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

const PROVIDER_LABELS: Record<string, string> = { adf: 'Azure Data Factory', airflow: 'Airflow' };

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
          <Tag color={secretBearing ? 'geekblue' : 'cyan'}>
            {PROVIDER_LABELS[webhook.provider] ?? webhook.provider}
          </Tag>
          {secretBearing && !webhook.token_configured && (
            <Tag color="error">webhook secret not set</Tag>
          )}
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
            Configured in the DAG callback snippet (HMAC); signing key in Key Vault:{' '}
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

function count<T>(state: AsyncState<T[]>): number | null {
  return state.status === 'ok' ? state.data.length : null;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card title={title} size="small">
      <Flex vertical gap={16}>
        {children}
      </Flex>
    </Card>
  );
}

/** Load/error/table boilerplate for an already-fetched admin dataset. */
function DataTable<T extends object>({
  state,
  columns,
  rowKey,
  errorMessage,
}: {
  state: AsyncState<T[]>;
  columns: ColumnsType<T>;
  rowKey: (row: T) => string;
  errorMessage: string;
}) {
  if (state.status === 'loading') return <Spin size="large" />;
  if (state.status === 'error') {
    return <Alert type="error" showIcon message={errorMessage} description={state.error} />;
  }
  return (
    <Table
      dataSource={state.data}
      columns={columns}
      rowKey={rowKey}
      size="small"
      pagination={{ pageSize: 20, hideOnSinglePage: true }}
    />
  );
}

/** Name over email, falling back to the email alone when no display name. */
function Identity({ name, email }: { name: string | null; email: string }) {
  if (!name) return <Typography.Text>{email}</Typography.Text>;
  return (
    <Flex vertical>
      <Typography.Text>{name}</Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {email}
      </Typography.Text>
    </Flex>
  );
}

const PERMISSION_COLORS: Record<string, string> = {
  owner: 'gold',
  admin: 'volcano',
  edit: 'blue',
  view: 'default',
};

const SUITE_COLUMNS: ColumnsType<AdminSuite> = [
  { title: 'Suite', dataIndex: 'name' },
  {
    title: 'Owner',
    key: 'owner',
    render: (_, s) => <Identity name={s.owner_name} email={s.owner_email} />,
  },
  {
    title: 'Datasource',
    key: 'datasource',
    render: (_, s) => (
      <Flex align="center" gap={6}>
        <Typography.Text>{s.connection_name}</Typography.Text>
        <Tag>{s.connection_type}</Tag>
      </Flex>
    ),
  },
  { title: 'Env', dataIndex: 'env', render: (env: string) => <Tag>{env}</Tag> },
  { title: 'Checks', dataIndex: 'check_count', align: 'right' },
  { title: 'Shared with', dataIndex: 'share_count', align: 'right' },
  { title: 'Created', dataIndex: 'created_at', render: (v: string) => formatTimestamp(v) },
];

const USER_COLUMNS: ColumnsType<AdminUser> = [
  {
    title: 'Member',
    key: 'user',
    render: (_, u) => <Identity name={u.display_name} email={u.email} />,
  },
  { title: 'Suites owned', dataIndex: 'owned_suite_count', align: 'right' },
  { title: 'Shared with them', dataIndex: 'shared_suite_count', align: 'right' },
  {
    title: 'Last seen',
    dataIndex: 'last_seen_at',
    render: (v: string | null) => formatTimestamp(v),
  },
];

const ACCESS_COLUMNS: ColumnsType<AdminAccess> = [
  { title: 'Suite', dataIndex: 'suite_name' },
  {
    title: 'User',
    key: 'user',
    render: (_, a) => <Identity name={a.user_name} email={a.user_email} />,
  },
  {
    title: 'Permission',
    dataIndex: 'permission',
    render: (p: string) => <Tag color={PERMISSION_COLORS[p] ?? 'default'}>{p}</Tag>,
  },
];
