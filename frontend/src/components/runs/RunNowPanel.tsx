import { PlayCircleOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Flex,
  Modal,
  Select,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { useMemo, useState } from 'react';

import {
  type Connection,
  CONNECTION_TYPE_LABELS,
  ENV_COLORS,
  envLabel,
  listConnections,
} from '../../api/connections';
import { type Run } from '../../api/runs';
import { canRunSuite, listSuites, type Suite } from '../../api/suites';
import { summarizeTarget } from '../suites/suiteTarget';
import { useAsyncData } from '../../hooks/useAsyncData';
import { useRunTrigger } from '../../hooks/useRunTrigger';
import { LiveRunProgress } from './LiveRunProgress';

/**
 * Run-now panel — a cross-suite run launcher (suite picker + env/datasource
 * readout). Distinct from the suite-detail Run button (which runs *one* suite in
 * context): here the user picks any suite they can run from the Results surface.
 * On trigger it hands off to the shared `LiveRunProgress` drawer, so the modal
 * closes and the run is watched check-by-check. Alerting is configured per suite
 * (Notifications panel), not per run.
 *
 * Self-contained (owns its own data fetch + progress drawer) so it can drop onto
 * a dedicated Execution page unchanged.
 */
export function RunNowPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  // The launcher hands the queued run to the live-progress drawer (closing the
  // modal first); `progress` carries the suite name for the drawer title.
  const [progress, setProgress] = useState<{ run: Run; suiteName: string } | null>(null);

  return (
    <>
      <Modal
        open={open}
        onCancel={onClose}
        title="Run now"
        footer={null}
        destroyOnHidden
        width={520}
      >
        {/* `destroyOnHidden` unmounts the body on close and antd defers the first
            mount until open, so each open refetches the suite / connection lists
            (a suite created since last open shows up) — no extra `{open && …}`
            guard needed (#326). */}
        <RunNowForm
          onCancel={onClose}
          onQueued={(run, suite) => {
            onClose();
            setProgress({ run, suiteName: suite.name });
          }}
        />
      </Modal>
      <LiveRunProgress
        runId={progress?.run.id ?? null}
        suiteName={progress?.suiteName ?? null}
        // Every runnable suite is edit+ (the picker filters to canRunSuite), and
        // cancel is the same edit capability — so the launched run is always
        // cancellable.
        canManage
        onClose={() => setProgress(null)}
      />
    </>
  );
}

function RunNowForm({
  onQueued,
  onCancel,
}: {
  onQueued: (run: Run, suite: Suite) => void;
  onCancel: () => void;
}) {
  const { state: suitesState } = useAsyncData(listSuites);
  const { state: connState } = useAsyncData(listConnections);
  const [suiteId, setSuiteId] = useState<string | null>(null);
  // Shared trigger logic (in-flight state + double-click guard + toasts).
  const { running, run } = useRunTrigger(onQueued);

  const runnable = useMemo(
    () => (suitesState.status === 'ok' ? suitesState.data.filter(canRunSuite) : []),
    [suitesState],
  );
  const connById = useMemo(() => {
    const map = new Map<string, Connection>();
    if (connState.status === 'ok') {
      for (const c of connState.data) map.set(c.id, c);
    }
    return map;
  }, [connState]);

  if (suitesState.status === 'loading') return <Spin description="Loading suites…" />;
  if (suitesState.status === 'error') {
    return (
      <Alert type="error" showIcon title="Failed to load suites" description={suitesState.error} />
    );
  }
  if (runnable.length === 0) {
    return (
      <Empty description="No runnable suites — you need edit access to a suite with a run target." />
    );
  }

  const suite = runnable.find((s) => s.id === suiteId) ?? null;
  const conn = suite ? connById.get(suite.connection_id) : undefined;
  const target = suite ? summarizeTarget(suite.target) : null;
  // A suite with no target isn't runnable yet (the backend would 422) — block the
  // Run button and say why, rather than launching a run that fails on dispatch.
  const runnableNow = suite !== null && target !== null;

  return (
    <Flex vertical gap={20}>
      <Flex vertical gap={6}>
        <Typography.Text type="secondary">Suite</Typography.Text>
        <Select<string>
          showSearch
          optionFilterProp="label"
          placeholder="Select a suite to run"
          value={suiteId ?? undefined}
          onChange={setSuiteId}
          options={runnable.map((s) => ({ value: s.id, label: s.name }))}
        />
      </Flex>

      {suite && (
        <Descriptions
          size="small"
          column={1}
          bordered
          items={[
            {
              key: 'env',
              label: 'Environment',
              children: conn ? (
                <Tag color={ENV_COLORS[conn.env]} style={{ marginInlineEnd: 0 }}>
                  {envLabel(conn.env)}
                </Tag>
              ) : (
                '—'
              ),
            },
            {
              key: 'datasource',
              label: 'Datasource',
              children: conn ? (
                <>
                  {conn.name}{' '}
                  <Typography.Text type="secondary">
                    · {CONNECTION_TYPE_LABELS[conn.type]}
                  </Typography.Text>
                </>
              ) : (
                '—'
              ),
            },
            {
              key: 'target',
              label: 'Target',
              children: target ? (
                <Typography.Text code>{target}</Typography.Text>
              ) : (
                <Typography.Text type="warning">No run target set</Typography.Text>
              ),
            },
          ]}
        />
      )}

      {suite && !runnableNow && (
        <Alert
          type="warning"
          showIcon
          title="This suite has no run target"
          description="Set a run target (edit the suite) before running it."
        />
      )}

      {/* Alerting is configured per suite (the ResultPublisher notification config
          + per-suite Teams/Slack/email webhooks), not per run — see a suite's
          Notifications panel. No per-run notification control here. */}
      <Flex gap={8} justify="flex-end">
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          disabled={!runnableNow}
          loading={running}
          onClick={() => suite && run(suite)}
        >
          Run
        </Button>
      </Flex>
    </Flex>
  );
}
