import { Alert, Descriptions, Drawer, Empty, Flex, List, Spin, Tag, Typography } from 'antd';

import { type CheckVersion, listCheckVersions } from '../../api/suites';
import { formatTimestamp } from '../results/resultsFormat';
import { useAsyncData } from '../../hooks/useAsyncData';
import { EXPECTATION_BY_TYPE } from './expectationCatalog';

/**
 * Read-only history of a check's saved configurations (#280) — "see previous
 * config before overwriting". Each version is an immutable snapshot the backend
 * records on create and on every real edit; newest first. v1 is view-only (no
 * restore). Mounted only while open (`destroyOnHidden`) so it refetches each time.
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
    <Drawer
      title={check ? `History — “${check.name}”` : 'History'}
      open={open}
      onClose={onClose}
      width={520}
      destroyOnHidden
    >
      {check && <CheckHistoryBody suiteId={suiteId} checkId={check.id} />}
    </Drawer>
  );
}

function CheckHistoryBody({ suiteId, checkId }: { suiteId: string; checkId: string }) {
  const { state } = useAsyncData(() => listCheckVersions(suiteId, checkId));

  if (state.status === 'loading') {
    return <Spin tip="Loading history…" />;
  }
  if (state.status === 'error') {
    return (
      <Alert type="error" showIcon message="Failed to load history" description={state.error} />
    );
  }
  if (state.data.length === 0) {
    // A check created before the history feature shipped has a live config but
    // no snapshots — say so rather than imply it's unconfigured.
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="No history yet — recording starts from the next save."
      />
    );
  }

  return (
    <List
      dataSource={state.data}
      // The first row is the newest snapshot — i.e. the check's current saved state.
      renderItem={(version, index) => <VersionItem version={version} current={index === 0} />}
    />
  );
}

function VersionItem({ version, current }: { version: CheckVersion; current: boolean }) {
  const label = EXPECTATION_BY_TYPE[version.expectation_type]?.label ?? version.expectation_type;
  return (
    <List.Item>
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
          <Descriptions.Item label="Expectation">{label}</Descriptions.Item>
          <Descriptions.Item label="Config">
            <Typography.Text code style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {/* Pretty-printed so a multi-line custom-SQL query (ADR 0019)
                  stays readable instead of collapsing to one escaped line. */}
              {JSON.stringify(version.config, null, 2)}
            </Typography.Text>
          </Descriptions.Item>
          <Descriptions.Item label="Thresholds">{formatThresholds(version)}</Descriptions.Item>
        </Descriptions>
      </Flex>
    </List.Item>
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
