import { Alert, App, Badge, Button, Card, Empty, Flex, Spin, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';

import {
  CONNECTION_TYPE_LABELS,
  CONNECTION_TYPES,
  type Connection,
  type ConnectionType,
  listConnections,
  testConnection,
} from '../api/connections';

const ENV_COLORS: Record<string, string> = {
  dev: 'blue',
  qa: 'gold',
  uat: 'purple',
  prod: 'red',
};

type LoadState =
  | { status: 'loading' }
  | { status: 'ok'; connections: Connection[] }
  | { status: 'error'; error: string };

export function Connections() {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    listConnections()
      .then((connections) => {
        if (!cancelled) setState({ status: 'ok', connections });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({ status: 'error', error: err instanceof Error ? err.message : String(err) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  const { connections } = state;
  return (
    <Flex vertical gap={24}>
      <Typography.Title level={3} style={{ margin: 0 }}>
        Connections
      </Typography.Title>
      {connections.length === 0 ? (
        <Empty description="No connections configured yet" />
      ) : (
        // One section per type that has connections, in canonical type order.
        CONNECTION_TYPES.filter((type) => connections.some((c) => c.type === type)).map((type) => (
          <ConnectionTypeSection
            key={type}
            type={type}
            connections={connections.filter((c) => c.type === type)}
          />
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
