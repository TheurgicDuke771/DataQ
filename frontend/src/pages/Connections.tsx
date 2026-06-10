import { MoreOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Dropdown,
  Empty,
  Flex,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { useState } from 'react';

import {
  CONNECTION_TYPE_LABELS,
  CONNECTION_TYPES,
  type Connection,
  type ConnectionEnv,
  type ConnectionType,
  deleteConnection,
  envLabel,
  listConnections,
  testConnection,
} from '../api/connections';
import { ConnectionDrawer } from '../components/connections/ConnectionDrawer';
import { ReauthModal } from '../components/connections/ReauthModal';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';

const ENV_COLORS: Record<ConnectionEnv, string> = {
  dev: 'blue',
  qa: 'gold',
  uat: 'purple',
  prod: 'red',
};

/** Per-card actions, threaded from the page so they can mutate shared state. */
interface ConnectionActions {
  onEdit: (connection: Connection) => void;
  onReauth: (connection: Connection) => void;
  onChanged: () => void;
}

/** Group connections by type in one pass, preserving canonical type order. */
function groupByType(connections: Connection[]): [ConnectionType, Connection[]][] {
  const byType = new Map<ConnectionType, Connection[]>();
  for (const c of connections) {
    const group = byType.get(c.type);
    if (group) group.push(c);
    else byType.set(c.type, [c]);
  }
  return CONNECTION_TYPES.filter((type) => byType.has(type)).map((type) => [
    type,
    byType.get(type) as Connection[],
  ]);
}

export function Connections() {
  const { state, reload } = useAsyncData(listConnections);
  // `drawer.connection === undefined` while open = create mode; a connection = edit.
  const [drawer, setDrawer] = useState<{ open: boolean; connection?: Connection }>({ open: false });
  const [reauthing, setReauthing] = useState<Connection | null>(null);

  const actions: ConnectionActions = {
    onEdit: (connection) => setDrawer({ open: true, connection }),
    onReauth: setReauthing,
    onChanged: reload,
  };

  return (
    <Flex vertical gap={24}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Connections
        </Typography.Title>
        <Button type="primary" onClick={() => setDrawer({ open: true })}>
          Add connection
        </Button>
      </Flex>
      <ConnectionsBody state={state} actions={actions} />
      <ConnectionDrawer
        open={drawer.open}
        connection={drawer.connection}
        onClose={() => setDrawer({ open: false })}
        onSaved={() => {
          setDrawer({ open: false });
          reload();
        }}
      />
      <ReauthModal
        connection={reauthing}
        onClose={() => setReauthing(null)}
        onDone={() => {
          setReauthing(null);
          reload();
        }}
      />
    </Flex>
  );
}

function ConnectionsBody({
  state,
  actions,
}: {
  state: AsyncState<Connection[]>;
  actions: ConnectionActions;
}) {
  if (state.status === 'loading') {
    return <Spin tip="Loading connections…" size="large" style={{ marginTop: 80 }} />;
  }
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        title="Failed to load connections"
        description={state.error}
        style={{ margin: 24 }}
      />
    );
  }
  const connections = state.data;
  if (connections.length === 0) {
    return <Empty description="No connections configured yet" />;
  }
  return (
    <>
      {groupByType(connections).map(([type, group]) => (
        <ConnectionTypeSection key={type} type={type} connections={group} actions={actions} />
      ))}
    </>
  );
}

function ConnectionTypeSection({
  type,
  connections,
  actions,
}: {
  type: ConnectionType;
  connections: Connection[];
  actions: ConnectionActions;
}) {
  return (
    <Flex vertical gap={12}>
      <Typography.Title level={5} style={{ margin: 0 }}>
        {CONNECTION_TYPE_LABELS[type]}
      </Typography.Title>
      <Flex wrap gap={12}>
        {connections.map((connection) => (
          <ConnectionCard key={connection.id} connection={connection} actions={actions} />
        ))}
      </Flex>
    </Flex>
  );
}

function ConnectionCard({
  connection,
  actions,
}: {
  connection: Connection;
  actions: ConnectionActions;
}) {
  const { message, modal } = App.useApp();
  const [testing, setTesting] = useState(false);

  const onTest = async () => {
    setTesting(true);
    try {
      const { ok } = await testConnection(connection.id);
      if (ok) message.success(`${connection.name}: connection OK`);
      else message.error(`${connection.name}: connection test failed`);
    } catch (err) {
      message.error(`${connection.name}: ${err instanceof Error ? err.message : 'test failed'}`);
    } finally {
      setTesting(false);
    }
  };

  const onDelete = () => {
    modal.confirm({
      title: `Delete “${connection.name}”?`,
      content: 'This removes the connection and its stored credential.',
      okText: 'Delete',
      okType: 'danger',
      onOk: async () => {
        try {
          await deleteConnection(connection.id);
          message.success(`${connection.name} deleted`);
          actions.onChanged();
        } catch (err) {
          message.error(`Delete failed: ${err instanceof Error ? err.message : 'unknown error'}`);
          throw err; // keep the confirm modal open on failure
        }
      },
    });
  };

  const menuItems = [
    { key: 'edit', label: 'Edit', onClick: () => actions.onEdit(connection) },
    { key: 'reauth', label: 'Re-authenticate', onClick: () => actions.onReauth(connection) },
    { type: 'divider' as const },
    { key: 'delete', label: 'Delete', danger: true, onClick: onDelete },
  ];

  return (
    <Card size="small" style={{ minWidth: 280 }}>
      <Flex justify="space-between" align="center" gap={12}>
        <Flex vertical gap={6}>
          <Typography.Text strong>{connection.name}</Typography.Text>
          <Flex gap={8} align="center">
            <Tag color={ENV_COLORS[connection.env]}>{envLabel(connection.env)}</Tag>
            {connection.has_secret ? (
              <Badge status="success" text="credential set" />
            ) : (
              <Badge status="warning" text="no credential" />
            )}
          </Flex>
        </Flex>
        <Flex gap={8} align="center">
          <Button size="small" loading={testing} onClick={onTest}>
            Test
          </Button>
          <Dropdown menu={{ items: menuItems }} trigger={['click']}>
            <Button
              size="small"
              icon={<MoreOutlined />}
              aria-label={`${connection.name} actions`}
            />
          </Dropdown>
        </Flex>
      </Flex>
    </Card>
  );
}
