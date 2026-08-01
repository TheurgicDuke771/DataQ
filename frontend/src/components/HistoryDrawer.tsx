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
 * Version-history drawer shared by checks (#280, #283) and connections (#654)
 * — "see previous config before overwriting", plus an optional per-row action
 * slot (`renderActions`) for check restore. Each version is an immutable
 * snapshot the backend records on create and on every real edit; newest
 * first. Connections omit `renderActions` and stay view-only, exactly v1's
 * original behavior. Mounted only while open (`destroyOnHidden`) so it
 * refetches each time; bump `refreshKey` to force an extra refetch without
 * closing (e.g. right after a restore mints a new version).
 */
export function HistoryDrawer<V extends HistoryVersion>({
  open,
  subject,
  fetchVersions,
  renderDetails,
  renderActions,
  refreshKey,
  onClose,
}: {
  open: boolean;
  /** The entity whose history to show; null while none is selected/loaded. */
  subject: { name: string } | null;
  /** Fetches the subject's versions, newest first (close over the ids). */
  fetchVersions: () => Promise<V[]>;
  /** Entity-specific detail block (a `<Descriptions>`) under the shared header. */
  renderDetails: (version: V) => ReactNode;
  /** Optional per-row action(s) (e.g. "Restore this version", #283), rendered
   *  under the details. `isCurrent` is true for the newest row (index 0) so
   *  the caller can hide/disable an action there. Omit entirely for a
   *  read-only drawer (the connections history stays this way). */
  renderActions?: (version: V, isCurrent: boolean) => ReactNode;
  /** Change this value to force the version list to refetch while the drawer
   *  stays open. */
  refreshKey?: number | string;
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
      {subject && (
        <HistoryBody
          key={refreshKey}
          fetchVersions={fetchVersions}
          renderDetails={renderDetails}
          renderActions={renderActions}
        />
      )}
    </Drawer>
  );
}

function HistoryBody<V extends HistoryVersion>({
  fetchVersions,
  renderDetails,
  renderActions,
}: {
  fetchVersions: () => Promise<V[]>;
  renderDetails: (version: V) => ReactNode;
  renderActions?: (version: V, isCurrent: boolean) => ReactNode;
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
            {renderActions && <Flex justify="end">{renderActions(version, index === 0)}</Flex>}
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
