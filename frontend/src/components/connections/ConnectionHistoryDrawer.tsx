import { Descriptions, Tag } from 'antd';

import {
  CONNECTION_TYPE_LABELS,
  type ConnectionVersion,
  ENV_COLORS,
  envLabel,
  listConnectionVersions,
} from '../../api/connections';
import { ConfigJson, HistoryDrawer } from '../HistoryDrawer';

/**
 * Connection version history (#654) — the connection twin of the check-history
 * drawer (#280), on the shared `HistoryDrawer` shell. Snapshots are
 * credential-free: only the editable, non-secret fields are versioned.
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
    <HistoryDrawer<ConnectionVersion>
      open={open}
      subject={connection}
      onClose={onClose}
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
    />
  );
}
