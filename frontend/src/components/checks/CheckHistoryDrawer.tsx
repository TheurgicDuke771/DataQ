import { Descriptions } from 'antd';

import { type CheckVersion, listCheckVersions } from '../../api/suites';
import { ConfigJson, HistoryDrawer } from '../HistoryDrawer';
import { EXPECTATION_BY_TYPE } from './expectationCatalog';

/**
 * Check version history (#280) — "see previous config before overwriting", on
 * the shared `HistoryDrawer` shell (also used by connections, #654).
 */
export function CheckHistoryDrawer({
  open,
  suiteId,
  check,
  onClose,
}: {
  open: boolean;
  suiteId: string;
  /** The check whose history to show; null while none is selected. */
  check: { id: string; name: string } | null;
  onClose: () => void;
}) {
  return (
    <HistoryDrawer<CheckVersion>
      open={open}
      subject={check}
      onClose={onClose}
      // The body only mounts with a subject, so the null branch never fetches.
      fetchVersions={() => (check ? listCheckVersions(suiteId, check.id) : Promise.resolve([]))}
      renderDetails={(version) => (
        <Descriptions size="small" column={1} bordered styles={{ label: { width: 120 } }}>
          <Descriptions.Item label="Expectation">
            {EXPECTATION_BY_TYPE[version.expectation_type]?.label ?? version.expectation_type}
          </Descriptions.Item>
          <Descriptions.Item label="Config">
            <ConfigJson config={version.config} />
          </Descriptions.Item>
          <Descriptions.Item label="Thresholds">{formatThresholds(version)}</Descriptions.Item>
        </Descriptions>
      )}
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
