import { App, Button, Descriptions, Popconfirm } from 'antd';
import { useState } from 'react';

import { type CheckVersion, listCheckVersions, restoreCheckVersion } from '../../api/suites';
import { errorMessage } from '../../utils/errors';
import { ConfigJson, HistoryDrawer } from '../HistoryDrawer';
import { EXPECTATION_BY_TYPE } from './expectationCatalog';

const ENGINE_LABEL: Record<string, string> = {
  gx: 'Great Expectations (gx)',
  dmf: 'Snowflake DMF (native)',
};

/**
 * Check version history (#280) — "see previous config before overwriting" — plus restore (#283),
 * on the shared `HistoryDrawer` shell (also used read-only by connections, #654).
 */
export function CheckHistoryDrawer({
  open,
  suiteId,
  check,
  canRestore = false,
  onRestored,
  onClose,
}: {
  open: boolean;
  suiteId: string;
  /** The check whose history to show; null while none is selected. */
  check: { id: string; name: string } | null;
  /**
   * Whether the caller may restore a version — mirrors the editor's edit gate (`canRunSuite`); a
   * viewer sees history exactly as read-only as v1 did.
   */
  canRestore?: boolean;
  /**
   * Called after a successful restore so the editor reloads the check (the live config just
   * changed underneath it).
   */
  onRestored?: () => void;
  onClose: () => void;
}) {
  const { message } = App.useApp();
  const [restoringVersion, setRestoringVersion] = useState<number | null>(null);
  // Bumped after a successful restore to force the version list to refetch
  // (the drawer stays open so the new row is visible immediately).
  const [refreshKey, setRefreshKey] = useState(0);

  const handleRestore = async (versionNo: number) => {
    if (!check) return;
    setRestoringVersion(versionNo);
    try {
      await restoreCheckVersion(suiteId, check.id, versionNo);
      message.success(`Restored v${versionNo}`);
      setRefreshKey((k) => k + 1);
      onRestored?.();
    } catch (err) {
      message.error(`Restore failed: ${errorMessage(err)}`);
      // Re-throw so antd's Popconfirm ActionButton keeps the confirm OPEN on failure.
      throw err;
    } finally {
      setRestoringVersion(null);
    }
  };

  return (
    <HistoryDrawer<CheckVersion>
      open={open}
      subject={check}
      onClose={onClose}
      refreshKey={refreshKey}
      // The body only mounts with a subject, so the null branch never fetches.
      fetchVersions={() => (check ? listCheckVersions(suiteId, check.id) : Promise.resolve([]))}
      renderDetails={(version) => (
        <Descriptions size="small" column={1} bordered styles={{ label: { width: 120 } }}>
          <Descriptions.Item label="Expectation">
            {EXPECTATION_BY_TYPE[version.expectation_type]?.label ?? version.expectation_type}
          </Descriptions.Item>
          <Descriptions.Item label="Engine">
            {ENGINE_LABEL[version.engine ?? 'gx'] ?? version.engine}
          </Descriptions.Item>
          <Descriptions.Item label="Config">
            <ConfigJson config={version.config} />
          </Descriptions.Item>
          <Descriptions.Item label="Thresholds">{formatThresholds(version)}</Descriptions.Item>
        </Descriptions>
      )}
      renderActions={
        canRestore
          ? (version, isCurrent) =>
              !isCurrent && (
                <Popconfirm
                  title={`Restore v${version.version_no}?`}
                  description="Creates a new version with this snapshot's config; nothing is deleted."
                  okText="Restore"
                  onConfirm={() => handleRestore(version.version_no)}
                >
                  <Button size="small" loading={restoringVersion === version.version_no}>
                    Restore this version
                  </Button>
                </Popconfirm>
              )
          : undefined
      }
    />
  );
}

/** Compact threshold line, or an em dash when the check is plain pass/fail. Labels
 *  mirror the editor's `Warn ≥ / Fail ≥ / Critical ≥` fields (SeverityThresholdFields). */
function formatThresholds(version: CheckVersion): string {
  const parts: string[] = [];
  if (version.warn_threshold !== null) parts.push(`Warn ≥ ${version.warn_threshold}`);
  if (version.fail_threshold !== null) parts.push(`Fail ≥ ${version.fail_threshold}`);
  if (version.critical_threshold !== null) parts.push(`Critical ≥ ${version.critical_threshold}`);
  return parts.length > 0 ? parts.join(' · ') : '—';
}
