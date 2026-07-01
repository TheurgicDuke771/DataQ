import { MoreOutlined } from '@ant-design/icons';
import {
  Alert,
  App,
  Badge,
  Button,
  Card,
  Divider,
  Dropdown,
  Empty,
  Flex,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  CONNECTION_KIND,
  CONNECTION_KIND_LABELS,
  CONNECTION_KINDS,
  CONNECTION_TYPE_LABELS,
  type Connection,
  deleteConnection,
  ENV_COLORS,
  envLabel,
  listConnections,
  testConnection,
} from '../api/connections';
import { ConnectionTypeAvatar } from '../components/connections/connectionVisuals';
import { ReauthModal } from '../components/connections/ReauthModal';
import { Page } from '../components/layout/Page';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';

/** Live connectivity state for a card — the health-page badge. */
type HealthState = 'idle' | 'testing' | 'ok' | 'failed';

/** Per-card actions, threaded from the page so they can mutate shared state. */
interface ConnectionActions {
  onEdit: (connection: Connection) => void;
  onReauth: (connection: Connection) => void;
  onChanged: () => void;
  /** Run a connectivity test and reflect the result on the card's health badge. */
  onTest: (connection: Connection) => Promise<boolean>;
  /** Drop a connection's stale health entry (after delete / edit / re-auth). */
  onClearHealth: (id: string) => void;
}

export function Connections() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { state, reload } = useAsyncData(listConnections);
  const [reauthing, setReauthing] = useState<Connection | null>(null);
  // Per-connection live connectivity status (the bulk health view).
  const [health, setHealth] = useState<Record<string, HealthState>>({});
  const [testingAll, setTestingAll] = useState(false);

  const testOne = useCallback(async (connection: Connection): Promise<boolean> => {
    setHealth((h) => ({ ...h, [connection.id]: 'testing' }));
    try {
      const { ok } = await testConnection(connection.id);
      setHealth((h) => ({ ...h, [connection.id]: ok ? 'ok' : 'failed' }));
      return ok;
    } catch {
      setHealth((h) => ({ ...h, [connection.id]: 'failed' }));
      return false;
    }
  }, []);

  // Drop a stale health result when the connection changes underneath it —
  // after a re-auth, an edit, or a delete the prior pass/fail no longer holds,
  // so the badge returns to idle until re-tested (and the map can't leak).
  const clearHealth = useCallback((id: string) => {
    setHealth((h) =>
      id in h ? Object.fromEntries(Object.entries(h).filter(([key]) => key !== id)) : h,
    );
  }, []);

  const connections = state.status === 'ok' ? state.data : [];

  const testAll = async () => {
    setTestingAll(true);
    const results = await Promise.all(connections.map(testOne));
    setTestingAll(false);
    const failed = results.filter((ok) => !ok).length;
    if (failed === 0) message.success(`All ${results.length} connections healthy`);
    else message.warning(`${failed} of ${results.length} connections unreachable`);
  };

  const actions: ConnectionActions = {
    // Editing is a dedicated page (create + edit pages replace the drawer, ADR 0022).
    onEdit: (connection) => navigate(`/connections/${connection.id}/edit`),
    onReauth: setReauthing,
    onChanged: reload,
    onTest: testOne,
    onClearHealth: clearHealth,
  };

  return (
    <Page>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Connections
        </Typography.Title>
        <Flex gap={8}>
          <Button loading={testingAll} disabled={connections.length === 0} onClick={testAll}>
            Test all
          </Button>
          <Button type="primary" onClick={() => navigate('/connections/new')}>
            Add connection
          </Button>
        </Flex>
      </Flex>
      <ConnectionsBody state={state} actions={actions} health={health} />
      <ReauthModal
        connection={reauthing}
        onClose={() => setReauthing(null)}
        onDone={() => {
          // Credential rotated → the old unreachable verdict no longer holds.
          if (reauthing) clearHealth(reauthing.id);
          setReauthing(null);
          reload();
        }}
      />
    </Page>
  );
}

function ConnectionsBody({
  state,
  actions,
  health,
}: {
  state: AsyncState<Connection[]>;
  actions: ConnectionActions;
  health: Record<string, HealthState>;
}) {
  if (state.status === 'loading') {
    return <Spin description="Loading connections…" size="large" style={{ marginTop: 80 }} />;
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
  // Two top-level sections (Data sources / Orchestration) — the load-bearing
  // distinction in DataQ (CLAUDE.md §4). Each renders as one even card grid (the
  // per-type avatar already identifies the source), so cards stay large and
  // evenly spaced rather than fragmenting into ragged per-type rows.
  const sections = CONNECTION_KINDS.map((kind) => ({
    kind,
    ofKind: connections.filter((c) => CONNECTION_KIND[c.type] === kind),
  })).filter((s) => s.ofKind.length > 0);

  return (
    <Flex vertical gap={24}>
      {sections.map(({ kind, ofKind }, i) => (
        <Flex key={kind} vertical gap={16}>
          {i > 0 && <Divider style={{ margin: '0 0 4px' }} />}
          <Typography.Title level={4} style={{ margin: 0 }}>
            {CONNECTION_KIND_LABELS[kind]}
          </Typography.Title>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
              gap: 16,
            }}
          >
            {ofKind.map((connection) => (
              <ConnectionCard
                key={connection.id}
                connection={connection}
                actions={actions}
                health={health[connection.id] ?? 'idle'}
              />
            ))}
          </div>
        </Flex>
      ))}
    </Flex>
  );
}

/** Health badge per connectivity state (the bulk health view's signal). */
function HealthBadge({ health }: { health: HealthState }) {
  switch (health) {
    case 'testing':
      return <Badge status="processing" text="testing…" />;
    case 'ok':
      return <Badge status="success" text="healthy" />;
    case 'failed':
      return <Badge status="error" text="unreachable" />;
    case 'idle':
      return null;
  }
}

function ConnectionCard({
  connection,
  actions,
  health,
}: {
  connection: Connection;
  actions: ConnectionActions;
  health: HealthState;
}) {
  const { message, modal } = App.useApp();

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
          actions.onClearHealth(connection.id);
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

  const isOrchestration = CONNECTION_KIND[connection.type] === 'orchestration';

  return (
    <Card size="small" className="dq-card--interactive" styles={{ body: { padding: 20 } }}>
      <Flex vertical gap={14}>
        {/* Avatar left; live health badge + actions menu top-right. */}
        <Flex justify="space-between" align="flex-start">
          <ConnectionTypeAvatar type={connection.type} size={44} />
          <Flex gap={4} align="center">
            <HealthBadge health={health} />
            <Dropdown menu={{ items: menuItems }} trigger={['click']}>
              <Button
                size="small"
                type="text"
                icon={<MoreOutlined />}
                aria-label={`${connection.name} actions`}
              />
            </Dropdown>
          </Flex>
        </Flex>

        {/* Identity: name + type (· Orchestration for providers). */}
        <Flex vertical gap={2} style={{ minWidth: 0 }}>
          <Typography.Text strong ellipsis style={{ fontSize: 15 }}>
            {connection.name}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {CONNECTION_TYPE_LABELS[connection.type]}
            {isOrchestration ? ' · Orchestration' : ''}
          </Typography.Text>
        </Flex>

        {/* Footer: env + credential state on the left, Test on the right. */}
        <Flex justify="space-between" align="center" gap={8}>
          <Flex gap={8} align="center" wrap style={{ minWidth: 0 }}>
            <Tag color={ENV_COLORS[connection.env]} style={{ marginInlineEnd: 0 }}>
              {envLabel(connection.env)}
            </Tag>
            {connection.has_secret ? (
              <Badge status="success" text="credential set" />
            ) : (
              <Badge status="warning" text="no credential" />
            )}
          </Flex>
          <Button
            size="small"
            loading={health === 'testing'}
            onClick={() => actions.onTest(connection)}
          >
            Test
          </Button>
        </Flex>

        {health === 'failed' && (
          <Button
            type="link"
            size="small"
            style={{ padding: 0, height: 'auto', alignSelf: 'flex-start' }}
            onClick={() => actions.onReauth(connection)}
          >
            Re-authenticate
          </Button>
        )}
      </Flex>
    </Card>
  );
}
