import { Alert, Flex, Spin, Table, Tabs, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import {
  type AdminAccess,
  type AdminSuite,
  type AdminUser,
  listAdminAccess,
  listAdminSuites,
  listAdminUsers,
} from '../api/admin';
import { useMe } from '../auth/useMe';
import { Forbidden } from '../components/Forbidden';
import { formatTimestamp } from '../components/results/resultsFormat';
import { useAsyncData } from '../hooks/useAsyncData';

/**
 * Workspace-admin control centre (#173): all suites / users / access overview.
 *
 * Access is server-driven — gated on `/me`'s `is_workspace_admin` (the backend's
 * own determination, not a client role guess), and the endpoints themselves
 * re-enforce with a 403. A non-admin who deep-links here sees the Forbidden page.
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

  return (
    <Flex vertical gap={24}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Admin
      </Typography.Title>
      <Tabs
        defaultActiveKey="suites"
        items={[
          { key: 'suites', label: 'Suites', children: <SuitesTab /> },
          { key: 'users', label: 'Users', children: <UsersTab /> },
          { key: 'access', label: 'Access', children: <AccessTab /> },
        ]}
      />
    </Flex>
  );
}

/** Shared load/error/table boilerplate for the three admin overviews. */
function AdminTable<T extends object>({
  fetcher,
  columns,
  rowKey,
  loadingTip,
  errorMessage,
}: {
  fetcher: () => Promise<T[]>;
  columns: ColumnsType<T>;
  rowKey: (row: T) => string;
  loadingTip: string;
  errorMessage: string;
}) {
  const { state } = useAsyncData(fetcher);
  if (state.status === 'loading') return <Spin tip={loadingTip} size="large" />;
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

function SuitesTab() {
  const columns: ColumnsType<AdminSuite> = [
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
  return (
    <AdminTable
      fetcher={listAdminSuites}
      columns={columns}
      rowKey={(s) => s.id}
      loadingTip="Loading suites…"
      errorMessage="Failed to load suites"
    />
  );
}

function UsersTab() {
  const columns: ColumnsType<AdminUser> = [
    {
      title: 'User',
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
  return (
    <AdminTable
      fetcher={listAdminUsers}
      columns={columns}
      rowKey={(u) => u.id}
      loadingTip="Loading users…"
      errorMessage="Failed to load users"
    />
  );
}

function AccessTab() {
  const columns: ColumnsType<AdminAccess> = [
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
  return (
    <AdminTable
      fetcher={listAdminAccess}
      columns={columns}
      // A user appears once per suite (owner or a single share row), so suite+user is unique.
      rowKey={(a) => `${a.suite_id}:${a.user_id}`}
      loadingTip="Loading access…"
      errorMessage="Failed to load access overview"
    />
  );
}
