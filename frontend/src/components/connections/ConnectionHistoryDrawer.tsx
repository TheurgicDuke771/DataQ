import { App, Descriptions, Tag } from 'antd';
import { useState } from 'react';

import {
  CONNECTION_TYPE_LABELS,
  type ConnectionVersion,
  ENV_COLORS,
  envLabel,
  listConnectionVersions,
  redactConnectionVersion,
} from '../../api/connections';
import { useCanMutateConnections } from '../../auth/useMe';
import { errorMessage } from '../../utils/errors';
import { ConfigJson, HistoryDrawer, RedactVersionButton } from '../HistoryDrawer';

/**
 * Connection version history (#654) — the connection twin of the check-history drawer (#280), on
 * the shared `HistoryDrawer` shell — plus per-version redaction for admins (#685).
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
  const { message } = App.useApp();
  // Redaction is a connection mutation (ADR 0033): hidden from non-admins here AND refused by
  // the route.
  const canRedact = useCanMutateConnections();
  const [redactingVersion, setRedactingVersion] = useState<number | null>(null);
  // Bumped after a redaction so the list refetches while the drawer stays open.
  const [refreshKey, setRefreshKey] = useState(0);

  const handleRedact = async (versionNo: number) => {
    if (!connection) return;
    setRedactingVersion(versionNo);
    try {
      await redactConnectionVersion(connection.id, versionNo);
      message.success(`Redacted v${versionNo}`);
      setRefreshKey((k) => k + 1);
    } catch (err) {
      message.error(`Redact failed: ${errorMessage(err)}`);
      // Re-throw so antd's Popconfirm keeps the confirm OPEN on failure.
      throw err;
    } finally {
      setRedactingVersion(null);
    }
  };

  return (
    <HistoryDrawer<ConnectionVersion>
      open={open}
      subject={connection}
      onClose={onClose}
      refreshKey={refreshKey}
      // The body only mounts with a subject, so the null branch never fetches.
      fetchVersions={() =>
        connection ? listConnectionVersions(connection.id) : Promise.resolve([])
      }
      renderDetails={(version) => (
        <Descriptions size="small" column={1} bordered styles={{ label: { width: 120 } }}>
          <Descriptions.Item label="Type">
            {/* Historical snapshots may carry values outside today's union
                (renamed/retired types or envs) — fall back to the raw value;
                an unknown env just renders an uncoloured tag. */}
            {CONNECTION_TYPE_LABELS[version.type] ?? version.type}
          </Descriptions.Item>
          <Descriptions.Item label="Environment">
            <Tag color={ENV_COLORS[version.env]}>{envLabel(version.env)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Config">
            <ConfigJson config={version.config} />
          </Descriptions.Item>
        </Descriptions>
      )}
      renderActions={
        canRedact
          ? (version, isCurrent) =>
              // The current version IS the connection's live config (the server refuses it too).
              !isCurrent &&
              !version.redacted && (
                <RedactVersionButton
                  versionNo={version.version_no}
                  loading={redactingVersion === version.version_no}
                  onConfirm={() => handleRedact(version.version_no)}
                />
              )
          : undefined
      }
    />
  );
}
