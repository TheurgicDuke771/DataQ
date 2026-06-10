import { App, Alert, Button, Card, Empty, Flex, List, Spin, Tag, Typography } from 'antd';
import { useState } from 'react';

import {
  CONNECTION_TYPE_LABELS,
  type Connection,
  ENV_COLORS,
  envLabel,
  listConnections,
} from '../api/connections';
import {
  type Check,
  deleteCheck,
  deleteSuite,
  listChecks,
  listSuites,
  type Suite,
} from '../api/suites';
import { CheckDrawer } from '../components/checks/CheckDrawer';
import { SuiteDrawer } from '../components/suites/SuiteDrawer';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';

export function Suites() {
  const { state, reload } = useAsyncData(listSuites);
  const { state: connState } = useAsyncData(listConnections);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // `drawer.suite === undefined` while open = create mode; a suite = edit.
  const [drawer, setDrawer] = useState<{ open: boolean; suite?: Suite }>({ open: false });

  const connections = connState.status === 'ok' ? connState.data : [];

  return (
    <Flex vertical gap={24}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Suites
        </Typography.Title>
        <Button
          type="primary"
          loading={connState.status === 'loading'}
          disabled={connections.length === 0}
          onClick={() => setDrawer({ open: true })}
        >
          New suite
        </Button>
      </Flex>
      {connState.status === 'error' && (
        // Suites can still be viewed/deleted, but creating one needs the
        // connection list — surface the failure rather than silently disabling.
        <Alert
          type="warning"
          showIcon
          title="Couldn’t load connections"
          description={`Creating a suite is unavailable until connections load. ${connState.error}`}
        />
      )}
      <SuitesBody
        state={state}
        connections={connections}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onEdit={(suite) => setDrawer({ open: true, suite })}
        onDeleted={() => {
          setSelectedId(null);
          reload();
        }}
      />
      <SuiteDrawer
        open={drawer.open}
        suite={drawer.suite}
        connections={connections}
        onClose={() => setDrawer({ open: false })}
        onSaved={() => {
          setDrawer({ open: false });
          reload();
        }}
      />
    </Flex>
  );
}

function SuitesBody({
  state,
  connections,
  selectedId,
  onSelect,
  onEdit,
  onDeleted,
}: {
  state: AsyncState<Suite[]>;
  connections: Connection[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onEdit: (suite: Suite) => void;
  onDeleted: () => void;
}) {
  if (state.status === 'loading') {
    return <Spin tip="Loading suites…" size="large" style={{ marginTop: 80 }} />;
  }
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        title="Failed to load suites"
        description={state.error}
        style={{ margin: 24 }}
      />
    );
  }
  const suites = state.data;
  if (suites.length === 0) {
    return <Empty description="No suites yet — create one to start authoring checks." />;
  }
  const selected = suites.find((s) => s.id === selectedId) ?? null;

  return (
    <Flex gap={24} align="flex-start">
      <Card size="small" style={{ width: 300, flexShrink: 0 }} styles={{ body: { padding: 0 } }}>
        <List
          dataSource={suites}
          renderItem={(suite) => (
            <List.Item
              onClick={() => onSelect(suite.id)}
              style={{
                cursor: 'pointer',
                padding: '12px 16px',
                background: suite.id === selectedId ? 'rgba(22,119,255,0.08)' : undefined,
              }}
            >
              <Typography.Text strong={suite.id === selectedId}>{suite.name}</Typography.Text>
            </List.Item>
          )}
        />
      </Card>
      <div style={{ flex: 1, minWidth: 0 }}>
        {selected ? (
          <SuiteDetail
            key={selected.id}
            suite={selected}
            connections={connections}
            onEdit={() => onEdit(selected)}
            onDeleted={onDeleted}
          />
        ) : (
          <Empty description="Select a suite to view its checks." style={{ marginTop: 64 }} />
        )}
      </div>
    </Flex>
  );
}

function SuiteDetail({
  suite,
  connections,
  onEdit,
  onDeleted,
}: {
  suite: Suite;
  connections: Connection[];
  onEdit: () => void;
  onDeleted: () => void;
}) {
  const { message, modal } = App.useApp();
  // Remounted (keyed by suite.id) when the selection changes → checks refetch.
  const { state, reload } = useAsyncData(() => listChecks(suite.id));
  const connection = connections.find((c) => c.id === suite.connection_id);
  // `checkDrawer.check === undefined` while open = create; a check = edit.
  const [checkDrawer, setCheckDrawer] = useState<{ open: boolean; check?: Check }>({ open: false });

  const onDelete = () => {
    modal.confirm({
      title: `Delete “${suite.name}”?`,
      content: 'This removes the suite and all of its checks.',
      okText: 'Delete',
      okType: 'danger',
      onOk: async () => {
        try {
          await deleteSuite(suite.id);
          message.success(`${suite.name} deleted`);
          onDeleted();
        } catch (err) {
          message.error(`Delete failed: ${err instanceof Error ? err.message : 'unknown error'}`);
          throw err; // keep the confirm modal open on failure
        }
      },
    });
  };

  return (
    <Flex vertical gap={16}>
      <Flex justify="space-between" align="flex-start" gap={12}>
        <Flex vertical gap={6}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {suite.name}
          </Typography.Title>
          {connection ? (
            <Flex gap={8} align="center">
              <Typography.Text type="secondary">
                {connection.name} · {CONNECTION_TYPE_LABELS[connection.type]}
              </Typography.Text>
              <Tag color={ENV_COLORS[connection.env]}>{envLabel(connection.env)}</Tag>
            </Flex>
          ) : (
            <Typography.Text type="secondary">Connection {suite.connection_id}</Typography.Text>
          )}
        </Flex>
        <Flex gap={8}>
          <Button onClick={onEdit}>Edit</Button>
          <Button danger onClick={onDelete}>
            Delete
          </Button>
        </Flex>
      </Flex>
      {suite.description && <Typography.Paragraph>{suite.description}</Typography.Paragraph>}
      <ChecksList
        suiteId={suite.id}
        state={state}
        onAdd={() => setCheckDrawer({ open: true })}
        onEdit={(check) => setCheckDrawer({ open: true, check })}
        onChanged={reload}
      />
      <CheckDrawer
        open={checkDrawer.open}
        suiteId={suite.id}
        check={checkDrawer.check}
        onClose={() => setCheckDrawer({ open: false })}
        onSaved={() => {
          setCheckDrawer({ open: false });
          reload();
        }}
      />
    </Flex>
  );
}

function ChecksList({
  suiteId,
  state,
  onAdd,
  onEdit,
  onChanged,
}: {
  suiteId: string;
  state: AsyncState<Check[]>;
  onAdd: () => void;
  onEdit: (check: Check) => void;
  onChanged: () => void;
}) {
  const { message, modal } = App.useApp();

  const onDelete = (check: Check) => {
    modal.confirm({
      title: `Delete “${check.name}”?`,
      okText: 'Delete',
      okType: 'danger',
      onOk: async () => {
        try {
          await deleteCheck(suiteId, check.id);
          message.success(`${check.name} deleted`);
          onChanged();
        } catch (err) {
          message.error(`Delete failed: ${err instanceof Error ? err.message : 'unknown error'}`);
          throw err; // keep the confirm modal open on failure
        }
      },
    });
  };

  if (state.status === 'loading') {
    return <Spin tip="Loading checks…" />;
  }
  if (state.status === 'error') {
    return <Alert type="error" showIcon title="Failed to load checks" description={state.error} />;
  }
  const checks = state.data;
  return (
    <Card
      size="small"
      title={`Checks (${checks.length})`}
      extra={
        <Button type="primary" size="small" onClick={onAdd}>
          Add check
        </Button>
      }
    >
      {checks.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="No checks yet — add one to start."
        />
      ) : (
        <List
          dataSource={checks}
          renderItem={(check) => (
            <List.Item
              actions={[
                <Button key="edit" type="link" size="small" onClick={() => onEdit(check)}>
                  Edit
                </Button>,
                <Button
                  key="delete"
                  type="link"
                  size="small"
                  danger
                  onClick={() => onDelete(check)}
                >
                  Delete
                </Button>,
              ]}
            >
              <Flex vertical gap={2}>
                <Typography.Text strong>{check.name}</Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {check.expectation_type}
                </Typography.Text>
              </Flex>
            </List.Item>
          )}
        />
      )}
    </Card>
  );
}
