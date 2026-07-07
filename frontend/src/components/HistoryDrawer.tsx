import { Alert, Drawer, Empty, Flex, Spin, Tag, Typography } from 'antd';
import type { ReactNode } from 'react';
import SimpleList from './SimpleList';

import { formatTimestamp } from './results/resultsFormat';
import { useAsyncData } from '../hooks/useAsyncData';

/** The header fields every immutable version snapshot shares (check #280,
 *  connection #654) — the entity-specific detail rows come from `renderDetails`. */
export interface HistoryVersion {
  version_no: number;
  name: string;
  changed_by_name: string | null;
  created_at: string;
}

/**
 * Read-only version-history drawer shared by checks (#280) and connections
 * (#654) — "see previous config before overwriting". Each version is an
 * immutable snapshot the backend records on create and on every real edit;
 * newest first. v1 is view-only (no restore). Mounted only while open
 * (`destroyOnHidden`) so it refetches each time.
 */
export function HistoryDrawer<V extends HistoryVersion>({
  open,
  subject,
  fetchVersions,
  renderDetails,
  onClose,
}: {
  open: boolean;
  /** The entity whose history to show; null while none is selected/loaded. */
  subject: { name: string } | null;
  /** Fetches the subject's versions, newest first (close over the ids). */
  fetchVersions: () => Promise<V[]>;
  /** Entity-specific detail block (a `<Descriptions>`) under the shared header. */
  renderDetails: (version: V) => ReactNode;
  onClose: () => void;
}) {
  return (
    <Drawer
      title={subject ? `History — “${subject.name}”` : 'History'}
      open={open}
      onClose={onClose}
      size={520}
      destroyOnHidden
    >
      {subject && <HistoryBody fetchVersions={fetchVersions} renderDetails={renderDetails} />}
    </Drawer>
  );
}

function HistoryBody<V extends HistoryVersion>({
  fetchVersions,
  renderDetails,
}: {
  fetchVersions: () => Promise<V[]>;
  renderDetails: (version: V) => ReactNode;
}) {
  const { state } = useAsyncData(fetchVersions);

  if (state.status === 'loading') {
    return <Spin description="Loading history…" />;
  }
  if (state.status === 'error') {
    return <Alert type="error" showIcon title="Failed to load history" description={state.error} />;
  }
  if (state.data.length === 0) {
    // An entity created before versioning shipped has a live config but no
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
      // The first row is the newest snapshot — i.e. the entity's current saved state.
      renderItem={(version, index) => (
        <SimpleList.Item>
          <Flex vertical gap={8} style={{ width: '100%' }}>
            <Flex align="center" gap={8} wrap>
              <Tag color="blue">v{version.version_no}</Tag>
              {index === 0 && <Tag color="green">Current</Tag>}
              <Typography.Text strong>{version.name}</Typography.Text>
              <Typography.Text type="secondary" style={{ marginLeft: 'auto', fontSize: 12 }}>
                {version.changed_by_name ?? 'Unknown'} · {formatTimestamp(version.created_at)}
              </Typography.Text>
            </Flex>
            {renderDetails(version)}
          </Flex>
        </SimpleList.Item>
      )}
    />
  );
}

/** Shared pretty-printed config cell — multi-line values (e.g. custom SQL,
 *  ADR 0019) stay readable instead of collapsing to one escaped line. */
export function ConfigJson({ config }: { config: Record<string, unknown> }) {
  return (
    <Typography.Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      {JSON.stringify(config, null, 2)}
    </Typography.Text>
  );
}
