import { PlayCircleOutlined } from '@ant-design/icons';
import { App, Alert, Button, Card, Empty, Flex, List, Spin, Tag, Tooltip, Typography } from 'antd';
import { useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { runSuite } from '../api/runs';

import {
  CONNECTION_KIND,
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
  exportSuite,
  listChecks,
  listSuites,
  type Suite,
} from '../api/suites';
import { CheckDrawer } from '../components/checks/CheckDrawer';
import { ConnectionTypeAvatar } from '../components/connections/connectionVisuals';
import { ImportSuiteDrawer } from '../components/suites/ImportSuiteDrawer';
import { SharePanel } from '../components/suites/SharePanel';
import { SuiteDrawer } from '../components/suites/SuiteDrawer';
import { TriggersPanel } from '../components/suites/TriggersPanel';
import { BRAND } from '../theme';
import { downloadJson, toFilenameStem } from '../utils/download';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';

/**
 * A suite's identity block — datasource avatar + name + connection/env — shared
 * by the browse-grid card and the master list row so the two never drift. The
 * name renders as plain text (never an `<h4>`), so only the detail panel owns
 * the suite heading. `description` is opt-in (the grid card shows it; the row
 * doesn't).
 */
function SuiteIdentity({
  suite,
  conn,
  size = 40,
  selected = false,
  showDescription = false,
}: {
  suite: Suite;
  conn: Connection | undefined;
  size?: number;
  selected?: boolean;
  showDescription?: boolean;
}) {
  return (
    <Flex
      gap={12}
      align={showDescription ? 'flex-start' : 'center'}
      style={{ width: '100%', minWidth: 0 }}
    >
      {conn && <ConnectionTypeAvatar type={conn.type} size={size} />}
      <Flex vertical gap={2} style={{ minWidth: 0, flex: 1 }}>
        <Typography.Text strong ellipsis style={selected ? { color: BRAND.primary } : undefined}>
          {suite.name}
        </Typography.Text>
        {conn ? (
          <Flex gap={6} align="center">
            <Typography.Text type="secondary" style={{ fontSize: 12 }} ellipsis>
              {conn.name}
            </Typography.Text>
            <Tag color={ENV_COLORS[conn.env]} style={{ marginInlineEnd: 0 }}>
              {envLabel(conn.env)}
            </Tag>
          </Flex>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            No connection
          </Typography.Text>
        )}
        {showDescription && suite.description && (
          <Typography.Paragraph
            type="secondary"
            ellipsis={{ rows: 2 }}
            style={{ fontSize: 12, margin: 0, marginTop: 2 }}
          >
            {suite.description}
          </Typography.Paragraph>
        )}
      </Flex>
    </Flex>
  );
}

export function Suites() {
  const navigate = useNavigate();
  // The selected suite is the route (`/suites/:suiteId`) so it deep-links and
  // survives the round-trip to the check editor; `/suites` selects nothing.
  const { suiteId } = useParams<{ suiteId: string }>();
  const selectedId = suiteId ?? null;
  const { state, reload } = useAsyncData(listSuites);
  const { state: connState } = useAsyncData(listConnections);
  // `drawer.suite === undefined` while open = create mode; a suite = edit.
  const [drawer, setDrawer] = useState<{ open: boolean; suite?: Suite }>({ open: false });
  const [importOpen, setImportOpen] = useState(false);

  const connections = connState.status === 'ok' ? connState.data : [];
  // A suite binds to a datasource only — creating/importing needs at least one
  // (orchestration providers don't count; CLAUDE.md §4, #242).
  const hasDatasource = connections.some((c) => CONNECTION_KIND[c.type] === 'datasource');

  return (
    <Flex vertical gap={24}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Suites
        </Typography.Title>
        <Flex gap={8}>
          <Button
            loading={connState.status === 'loading'}
            disabled={!hasDatasource}
            onClick={() => setImportOpen(true)}
          >
            Import
          </Button>
          <Button
            type="primary"
            loading={connState.status === 'loading'}
            disabled={!hasDatasource}
            onClick={() => setDrawer({ open: true })}
          >
            New suite
          </Button>
        </Flex>
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
        onSelect={(id) => navigate(`/suites/${id}`)}
        onEdit={(suite) => setDrawer({ open: true, suite })}
        onDeleted={() => {
          navigate('/suites');
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
      <ImportSuiteDrawer
        open={importOpen}
        connections={connections}
        onClose={() => setImportOpen(false)}
        onImported={(suite) => {
          setImportOpen(false);
          reload();
          navigate(`/suites/${suite.id}`);
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

  // Nothing selected → a full-width grid of suite cards that fills the page,
  // rather than a narrow list beside a big empty panel. Picking one switches to
  // the focused master-detail (the list stays, for quick switching).
  if (!selected) {
    return <SuiteGrid suites={suites} connections={connections} onSelect={onSelect} />;
  }

  return (
    <Flex gap={24} align="flex-start">
      <Card size="small" style={{ width: 320, flexShrink: 0 }} styles={{ body: { padding: 0 } }}>
        <List
          dataSource={suites}
          renderItem={(suite) => {
            const conn = connections.find((c) => c.id === suite.connection_id);
            const isSelected = suite.id === selectedId;
            return (
              <List.Item
                onClick={() => onSelect(suite.id)}
                className="dq-suite-row"
                style={{
                  cursor: 'pointer',
                  padding: '12px 16px',
                  background: isSelected ? BRAND.selectedBg : undefined,
                }}
              >
                <SuiteIdentity suite={suite} conn={conn} size={34} selected={isSelected} />
              </List.Item>
            );
          }}
        />
      </Card>
      <div style={{ flex: 1, minWidth: 0 }}>
        <SuiteDetail
          key={selected.id}
          suite={selected}
          connections={connections}
          onEdit={() => onEdit(selected)}
          onDeleted={onDeleted}
        />
      </div>
    </Flex>
  );
}

/** Browse view: suite cards in a responsive grid that fills the page width. */
function SuiteGrid({
  suites,
  connections,
  onSelect,
}: {
  suites: Suite[];
  connections: Connection[];
  onSelect: (id: string) => void;
}) {
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
        gap: 16,
      }}
    >
      {suites.map((suite) => {
        const conn = connections.find((c) => c.id === suite.connection_id);
        return (
          <Card
            key={suite.id}
            size="small"
            className="dq-card--interactive"
            style={{ cursor: 'pointer' }}
            onClick={() => onSelect(suite.id)}
          >
            <SuiteIdentity suite={suite} conn={conn} showDescription />
          </Card>
        );
      })}
    </div>
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
  const navigate = useNavigate();
  // Remounted (keyed by suite.id) when the selection changes → checks refetch.
  const { state, reload } = useAsyncData(() => listChecks(suite.id));
  const connection = connections.find((c) => c.id === suite.connection_id);
  // The edit drawer (create is the dedicated /checks/new page) → open iff editing.
  const [editingCheck, setEditingCheck] = useState<Check | null>(null);

  const [exporting, setExporting] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [running, setRunning] = useState(false);
  // Managing shares (and deleting) needs admin; the read stamps the caller's level.
  const canManage = suite.my_permission === 'owner' || suite.my_permission === 'admin';
  // Triggering a run is edit-gated (matches the backend); a null target isn't runnable.
  const canRun = canManage || suite.my_permission === 'edit';

  // Ref guard (not just `running` state) so a synchronous double-click can't
  // dispatch two runs in the render-tick before `loading` disables the button.
  const runningRef = useRef(false);
  const onRun = async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    try {
      await runSuite(suite.id);
      message.success(`${suite.name}: run queued`);
      navigate('/results');
    } catch (err) {
      message.error(`Run failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  };

  const onExport = async () => {
    setExporting(true);
    try {
      const doc = await exportSuite(suite.id);
      downloadJson(`${toFilenameStem(suite.name)}.json`, doc);
    } catch (err) {
      message.error(`Export failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setExporting(false);
    }
  };

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
          {canRun &&
            (suite.target ? (
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={running}
                onClick={onRun}
              >
                Run
              </Button>
            ) : (
              // No target yet → not runnable; show why rather than a 422 on click.
              // The <span> is required: a disabled antd Button has pointer-events:
              // none, so the Tooltip must hover the wrapper, not the button.
              <Tooltip title="Set a run target (Edit) before running this suite">
                <span style={{ cursor: 'not-allowed' }}>
                  <Button type="primary" icon={<PlayCircleOutlined />} disabled>
                    Run
                  </Button>
                </span>
              </Tooltip>
            ))}
          <Button onClick={() => setShareOpen(true)}>Share</Button>
          <Button loading={exporting} onClick={onExport}>
            Export
          </Button>
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
        onAdd={() => navigate(`/suites/${suite.id}/checks/new`)}
        onEdit={(check) => setEditingCheck(check)}
        onChanged={reload}
      />
      {/* Triggers are edit-gated (same as runs): a pipeline/DAG bound here runs
          the suite on its success. canRun is exactly the edit-level capability. */}
      <TriggersPanel suiteId={suite.id} canManage={canRun} />
      <CheckDrawer
        open={editingCheck !== null}
        suiteId={suite.id}
        check={editingCheck ?? undefined}
        target={suite.target}
        connectionType={connection?.type}
        onClose={() => setEditingCheck(null)}
        onSaved={() => {
          setEditingCheck(null);
          reload();
        }}
      />
      <SharePanel
        open={shareOpen}
        suiteId={suite.id}
        ownerId={suite.created_by}
        canManage={canManage}
        onClose={() => setShareOpen(false)}
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
