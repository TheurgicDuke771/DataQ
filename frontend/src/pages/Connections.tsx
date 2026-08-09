import { MoreOutlined } from '@ant-design/icons';
import {
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
  Tooltip,
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
import { formatTimestamp } from '../components/results/resultsFormat';
import { expiryLabel, expiryStatus } from '../utils/expiry';
import { ReauthModal } from '../components/connections/ReauthModal';
import { Page } from '../components/layout/Page';
import { type AsyncState, useAsyncData } from '../hooks/useAsyncData';
import { useConfirmDelete } from '../hooks/useConfirmDelete';
import { PageError } from '../components/feedback/PageError';

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
  const { state, reload } = useAsyncData(() => listConnections());
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
      <Flex justify="space-between" align="center" gap={12} wrap>
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
      <PageError
        error={state.error}
        kind={state.kind}
        httpStatus={state.httpStatus}
        requestId={state.requestId}
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

/**
 * "credential expires in 5d" / "credential expired", or nothing at all (#838).
 *
 * Renders only for a credential whose expiry is *readable and near* — an unknown
 * expiry produces no badge, because the alternative (a reassuring "valid" badge
 * on a credential we cannot actually read) is how a monitoring product lies.
 * A far-off expiry is also silent: a date on every card is noise that trains
 * people to ignore the one card that matters.
 */
function CredentialExpiryBadge({
  expiresAt,
  checkedAt,
}: {
  expiresAt?: string | null;
  checkedAt?: string | null;
}) {
  const status = expiryStatus(expiresAt);
  const label = expiryLabel(status);
  if (label && expiresAt) {
    return (
      <Tooltip title={`Credential expiry: ${formatTimestamp(expiresAt)}`}>
        <Badge status={status.kind === 'expired' ? 'error' : 'warning'} text={label} />
      </Tooltip>
    );
  }
  // No expiry to show. That means one of two very different things, and saying
  // nothing for both is what made an unchecked credential look safe (#1024):
  //   never checked      -> we do not know; say so
  //   checked, no expiry -> this credential type states none; stay silent, forever
  if (!checkedAt) {
    return (
      <Tooltip title="DataQ has not read this credential's expiry yet. It is checked when the credential is written and on a periodic sweep.">
        <Badge status="default" text="expiry unknown" />
      </Tooltip>
    );
  }
  return null;
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
  const confirmDelete = useConfirmDelete();

  const onDelete = () =>
    confirmDelete({
      label: connection.name,
      content: 'This removes the connection and its stored credential.',
      onDelete: async () => {
        await deleteConnection(connection.id);
        actions.onClearHealth(connection.id);
      },
      onDone: actions.onChanged,
    });

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
            {/* A failing poll is a fact about the connection now, not a log line (#828).
                Without this, an integration that has been dead for a week renders
                identically to a healthy one — which is how prod lineage rotted for six
                days behind an expired credential with nobody the wiser. The count is
                shown because "failing" and "failing since last Tuesday" are different
                problems. */}
            {(connection.consecutive_poll_failures ?? 0) > 0 && (
              <Tooltip
                title={
                  connection.last_poll_error
                    ? `Last error: ${connection.last_poll_error}`
                    : 'The scheduled poll for this connection is failing.'
                }
              >
                <Badge
                  status="error"
                  text={`poll failing (${connection.consecutive_poll_failures}×)`}
                />
              </Tooltip>
            )}
            {/* The same fact for a DATASOURCE connection (#954). Nothing polls one,
                so a dead credential used to be invisible here — it showed on the
                failing RUN, not on the connection that caused it, and diagnosing
                two dead prod Snowflake connections meant reading worker logs.
                Derived from runs, so it appears without anyone clicking Test. */}
            {/* The credential's own deadline, read from the credential (#838).
                #828's SAS expiry was knowable for its whole lifetime and nobody
                was told until prod lineage had been dark for six days — this is
                the warning that arrives before the outage rather than after it.
                Silent when the expiry is unknown: no badge is not a clean bill. */}
            <CredentialExpiryBadge
              expiresAt={connection.credential_expires_at}
              checkedAt={connection.credential_expiry_checked_at}
            />
            {(connection.consecutive_run_failures ?? 0) > 0 && (
              <Tooltip
                title={
                  connection.last_run_error
                    ? `Last run failed: ${connection.last_run_error}`
                    : 'Every recent suite run on this connection failed.'
                }
              >
                <Badge
                  status="error"
                  text={`runs failing (${connection.consecutive_run_failures}×)`}
                />
              </Tooltip>
            )}
            {/* Opted-in inventory sync (#1104) whose principal can't read the
                enumeration query — e.g. a UC PAT missing SELECT on
                system.information_schema — used to fail every daily tick with
                nothing visible here: toggle on, connection test green (the
                `SELECT 1` probe never exercises this query), zero assets ever
                appear. `inventory_sync_failing_since` is only set while the
                connection is opted in AND currently unhealthy. */}
            {Boolean(connection.config?.inventory_sync) &&
              connection.inventory_sync_failing_since && (
                <Tooltip
                  title={`Inventory sync failing since ${formatTimestamp(connection.inventory_sync_failing_since)}: ${connection.inventory_sync_last_error ?? 'unknown reason'}`}
                >
                  <Badge status="error" text="inventory sync failing" />
                </Tooltip>
              )}
            {/* A SUCCESSFUL sync that enumerates zero tables (#1242) — distinct
                from the error badge above, which only covers the sync itself
                failing to run. Snowflake's INFORMATION_SCHEMA is
                privilege-filtered, not access-denied: a role with no grants on
                the objects gets an empty result set, not an exception, so a
                sync that "succeeds" at zero rows used to look identical to a
                healthy one. Gated on the sync currently NOT failing, so
                `inventory_sync_last_table_count` (only ever stamped on a
                success) is describing the CURRENT state, not a stale reading
                left over from before the sync started erroring. */}
            {Boolean(connection.config?.inventory_sync) &&
              !connection.inventory_sync_failing_since &&
              connection.inventory_sync_last_table_count === 0 &&
              (connection.inventory_sync_zero_since ? (
                <Tooltip
                  title={`This connection previously synced tables, but the last sync — and every one since ${formatTimestamp(connection.inventory_sync_zero_since)} — found none. This usually means a grant was revoked or the database was dropped/renamed; it is not reported as a sync failure because the enumeration query itself ran without error.`}
                >
                  <Badge status="warning" text="tables dropped to 0" />
                </Tooltip>
              ) : (
                <Tooltip title="The last inventory sync ran successfully but found no tables. If this database is empty by design, no action is needed.">
                  <Badge status="default" text="0 tables found" />
                </Tooltip>
              ))}
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
