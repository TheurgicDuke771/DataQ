import { Alert, App, Badge, Button, Card, Empty, Flex, Spin, Tag, Typography } from 'antd';
import { useState } from 'react';

import {
  CONNECTION_TYPE_LABELS,
  CONNECTION_TYPES,
  type Connection,
  type ConnectionEnv,
  type ConnectionType,
  listConnections,
  testConnection,
} from '../api/connections';
import { useAsyncData } from '../hooks/useAsyncData';

const ENV_COLORS: Record<ConnectionEnv, string> = {
  dev: 'blue',
  qa: 'gold',
  uat: 'purple',
  prod: 'red',
};

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
  const state = useAsyncData(listConnections);

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
  return (
    <Flex vertical gap={24}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Connections
      </Typography.Title>
      {connections.length === 0 ? (
        <Empty description="No connections configured yet" />
      ) : (
        groupByType(connections).map(([type, group]) => (
          <ConnectionTypeSection key={type} type={type} connections={group} />
        ))
      )}
    </Flex>
  );
}

function ConnectionTypeSection({
  type,
  connections,
}: {
  type: ConnectionType;
  connections: Connection[];
}) {
  return (
    <Flex vertical gap={12}>
      <Typography.Title level={5} style={{ margin: 0 }}>
        {CONNECTION_TYPE_LABELS[type]}
      </Typography.Title>
      <Flex wrap gap={12}>
        {connections.map((connection) => (
          <ConnectionCard key={connection.id} connection={connection} />
        ))}
      </Flex>
    </Flex>
  );
}

function ConnectionCard({ connection }: { connection: Connection }) {
  const { message } = App.useApp();
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

  return (
    <Card size="small" style={{ minWidth: 280 }}>
      <Flex justify="space-between" align="center" gap={12}>
        <Flex vertical gap={6}>
          <Typography.Text strong>{connection.name}</Typography.Text>
          <Flex gap={8} align="center">
            <Tag color={ENV_COLORS[connection.env]}>{connection.env.toUpperCase()}</Tag>
            {connection.has_secret ? (
              <Badge status="success" text="credential set" />
            ) : (
              <Badge status="warning" text="no credential" />
            )}
          </Flex>
        </Flex>
        <Button size="small" loading={testing} onClick={onTest}>
          Test
        </Button>
      </Flex>
    </Card>
  );
}
