import { PlayCircleOutlined } from '@ant-design/icons';
import { App, Alert, Button, Card, Empty, Flex, Spin, Tag, Tooltip, Typography } from 'antd';
import SimpleList from '../components/SimpleList';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  CONNECTION_KIND,
  CONNECTION_TYPE_LABELS,
  type Connection,
  ENV_COLORS,
  envLabel,
  listConnections,
} from '../api/connections';
import {
  canManageSuite,
  canRunSuite,
  type Check,
  deleteCheck,
  deleteSuite,
  exportSuite,
  listChecks,
  listSuites,
  type Suite,
} from '../api/suites';
import { ConnectionTypeAvatar } from '../components/connections/connectionVisuals';
import { Page } from '../components/layout/Page';
import { LiveRunProgress } from '../components/runs/LiveRunProgress';
import { ImportSuiteDrawer } from '../components/suites/ImportSuiteDrawer';
import { NotificationsPanel } from '../components/suites/NotificationsPanel';
import { SchedulesPanel } from '../components/suites/SchedulesPanel';
import { SharePanel } from '../components/suites/SharePanel';
import { TriggersPanel } from '../components/suites/TriggersPanel';
import { BRAND } from '../theme';
import { downloadJson, toFilenameStem } from '../utils/download';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';
import { useRunTrigger } from '../hooks/useRunTrigger';

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
  const [importOpen, setImportOpen] = useState(false);

  const connections = connState.status === 'ok' ? connState.data : [];
  // A suite binds to a datasource only — creating/importing needs at least one
  // (orchestration providers don't count; CLAUDE.md §4, #242).
  const hasDatasource = connections.some((c) => CONNECTION_KIND[c.type] === 'datasource');

  return (
    <Page>
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
            onClick={() => navigate('/suites/new')}
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
        onEdit={(suite) => navigate(`/suites/${suite.id}/edit`)}
        onDeleted={() => {
          navigate('/suites');
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
    </Page>
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
    return <Spin description="Loading suites…" size="large" style={{ marginTop: 80 }} />;
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
        <SimpleList
          dataSource={suites}
          renderItem={(suite) => {
            const conn = connections.find((c) => c.id === suite.connection_id);
            const isSelected = suite.id === selectedId;
            return (
              <SimpleList.Item
                onClick={() => onSelect(suite.id)}
                className="dq-suite-row"
                style={{
                  cursor: 'pointer',
                  // Longhand (not the `padding` shorthand) so it overrides the
                  // shim's own `paddingBlock` deterministically, not by style-key
                  // serialization order.
                  paddingBlock: 12,
                  paddingInline: 16,
                  background: isSelected ? BRAND.selectedBg : undefined,
                }}
              >
                <SuiteIdentity suite={suite} conn={conn} size={34} selected={isSelected} />
              </SimpleList.Item>
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
        gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
        gap: 16,
      }}
    >
      {suites.map((suite) => (
        <SuiteBrowseCard
          key={suite.id}
          suite={suite}
          conn={connections.find((c) => c.id === suite.connection_id)}
          onSelect={() => onSelect(suite.id)}
        />
      ))}
    </div>
  );
}

/**
 * A suite's browse-grid card — the vertical layout the Connections cards use
 * (avatar top-left + env tag top-right, then name, connection · type, and an
 * optional 2-line description) so the two list pages read the same on first open.
 */
function SuiteBrowseCard({
  suite,
  conn,
  onSelect,
}: {
  suite: Suite;
  conn: Connection | undefined;
  onSelect: () => void;
}) {
  return (
    <Card
      className="dq-card--interactive"
      style={{ cursor: 'pointer' }}
      styles={{ body: { padding: 20 } }}
      onClick={onSelect}
    >
      <Flex vertical gap={14}>
        <Flex justify="space-between" align="flex-start" style={{ minHeight: 44 }}>
          {conn ? <ConnectionTypeAvatar type={conn.type} size={44} /> : <span />}
          {conn && (
            <Tag color={ENV_COLORS[conn.env]} style={{ marginInlineEnd: 0 }}>
              {envLabel(conn.env)}
            </Tag>
          )}
        </Flex>
        <Flex vertical gap={2} style={{ minWidth: 0 }}>
          <Typography.Text strong ellipsis style={{ fontSize: 15 }}>
            {suite.name}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 13 }} ellipsis>
            {conn ? `${conn.name} · ${CONNECTION_TYPE_LABELS[conn.type]}` : 'No connection'}
          </Typography.Text>
        </Flex>
        {suite.description && (
          <Typography.Paragraph
            type="secondary"
            ellipsis={{ rows: 2 }}
            style={{ margin: 0, fontSize: 13 }}
          >
            {suite.description}
          </Typography.Paragraph>
        )}
      </Flex>
    </Card>
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

  const [exporting, setExporting] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  // The live-progress drawer opens on the run id returned by a manual trigger.
  const [progressRunId, setProgressRunId] = useState<string | null>(null);
  // Managing shares (and deleting) needs admin; the read stamps the caller's level.
  const canManage = canManageSuite(suite);
  // Triggering a run is edit-gated (matches the backend); a null target isn't runnable.
  const canRun = canRunSuite(suite);

  // Open the live-progress drawer on the queued run rather than bouncing to
  // /results — the user watches it execute check-by-check (and can cancel).
  const { running, run } = useRunTrigger((queued) => setProgressRunId(queued.id));

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
                onClick={() => run(suite)}
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
        onEdit={(check) => navigate(`/suites/${suite.id}/checks/${check.id}/edit`)}
        onChanged={reload}
      />
      {/* Triggers + schedules are edit-gated (same as runs): a pipeline/DAG bound
          here runs the suite on its success; a schedule runs it on a cron cadence.
          canRun is exactly the edit-level capability. */}
      <TriggersPanel suiteId={suite.id} canManage={canRun} />
      <SchedulesPanel suiteId={suite.id} canManage={canRun} />
      <NotificationsPanel suiteId={suite.id} canManage={canRun} />
      <SharePanel
        open={shareOpen}
        suiteId={suite.id}
        ownerId={suite.created_by}
        canManage={canManage}
        onClose={() => setShareOpen(false)}
      />
      <LiveRunProgress
        runId={progressRunId}
        suiteName={suite.name}
        canManage={canRun}
        onClose={() => setProgressRunId(null)}
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
    return <Spin description="Loading checks…" />;
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
        <SimpleList
          dataSource={checks}
          renderItem={(check) => (
            <SimpleList.Item
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
            </SimpleList.Item>
          )}
        />
      )}
    </Card>
  );
}
