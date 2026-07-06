import { Alert, Descriptions, Drawer, Empty, Flex, Spin, Tag, Typography } from 'antd';
import SimpleList from '../SimpleList';

import {
  CONNECTION_TYPE_LABELS,
  type ConnectionVersion,
  ENV_COLORS,
  envLabel,
  listConnectionVersions,
} from '../../api/connections';
import { formatTimestamp } from '../results/resultsFormat';
import { useAsyncData } from '../../hooks/useAsyncData';

/**
 * Read-only history of a connection's saved configurations (#654) — the
 * connection twin of the check-history drawer (#280), same "see previous config
 * before overwriting" purpose and the same UX shape. Each version is an
 * immutable, credential-free snapshot the backend records on create and on
 * every real edit; newest first. v1 is view-only (no restore). Mounted only
 * while open (`destroyOnHidden`) so it refetches each time.
 */
export function ConnectionHistoryDrawer({
  open,
  connection,
  onClose,
}: {
  open: boolean;
  /** The connection whose history to show; null while none is loaded. */
  connection: { id: string; name: string } | null;
  onClose: () => void;
}) {
  return (
    <Drawer
      title={connection ? `History — “${connection.name}”` : 'History'}
      open={open}
      onClose={onClose}
      size={520}
      destroyOnHidden
    >
      {connection && <ConnectionHistoryBody connectionId={connection.id} />}
    </Drawer>
  );
}

function ConnectionHistoryBody({ connectionId }: { connectionId: string }) {
  const { state } = useAsyncData(() => listConnectionVersions(connectionId));

  if (state.status === 'loading') {
    return <Spin description="Loading history…" />;
  }
  if (state.status === 'error') {
    return <Alert type="error" showIcon title="Failed to load history" description={state.error} />;
  }
  if (state.data.length === 0) {
    // A connection created before versioning shipped has a live config but no
    // snapshots — say so rather than imply it's unconfigured.
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="No history yet — recording starts from the next save."
      />
    );
  }

  return (
    <SimpleList
      dataSource={state.data}
      // The first row is the newest snapshot — i.e. the connection's current saved state.
      renderItem={(version, index) => <VersionItem version={version} current={index === 0} />}
    />
  );
}

function VersionItem({ version, current }: { version: ConnectionVersion; current: boolean }) {
  return (
    <SimpleList.Item>
      <Flex vertical gap={8} style={{ width: '100%' }}>
        <Flex align="center" gap={8} wrap>
          <Tag color="blue">v{version.version_no}</Tag>
          {current && <Tag color="green">Current</Tag>}
          <Typography.Text strong>{version.name}</Typography.Text>
          <Typography.Text type="secondary" style={{ marginLeft: 'auto', fontSize: 12 }}>
            {version.changed_by_name ?? 'Unknown'} · {formatTimestamp(version.created_at)}
          </Typography.Text>
        </Flex>
        <Descriptions size="small" column={1} bordered styles={{ label: { width: 120 } }}>
          <Descriptions.Item label="Type">
            {CONNECTION_TYPE_LABELS[version.type] ?? version.type}
          </Descriptions.Item>
          <Descriptions.Item label="Environment">
            <Tag color={ENV_COLORS[version.env]}>{envLabel(version.env)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Config">
            <Typography.Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {JSON.stringify(version.config, null, 2)}
            </Typography.Text>
          </Descriptions.Item>
        </Descriptions>
      </Flex>
    </SimpleList.Item>
  );
}
