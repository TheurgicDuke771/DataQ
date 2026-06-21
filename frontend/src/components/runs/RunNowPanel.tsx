import { PlayCircleOutlined } from '@ant-design/icons';
import {
  App,
  Alert,
  Button,
  Descriptions,
  Empty,
  Flex,
  Modal,
  Select,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { useMemo, useRef, useState } from 'react';

import {
  type Connection,
  CONNECTION_TYPE_LABELS,
  ENV_COLORS,
  envLabel,
  listConnections,
} from '../../api/connections';
import { type Run, runSuite } from '../../api/runs';
import { listSuites, type Suite, targetString } from '../../api/suites';
import { useAsyncData } from '../../hooks/useAsyncData';
import { LiveRunProgress } from './LiveRunProgress';

/**
 * Permission levels that may trigger a run — the same edit-capability ladder the
 * suite-detail Run button and the backend `POST /suites/{id}/run` enforce. A
 * suite the caller can only `view` never appears in the picker.
 */
const RUNNABLE: ReadonlyArray<NonNullable<Suite['my_permission']>> = ['owner', 'admin', 'edit'];

function canRun(suite: Suite): boolean {
  return suite.my_permission != null && RUNNABLE.includes(suite.my_permission);
}

/**
 * Collapse the datasource-shaped run target (#215) to a one-line summary for the
 * picker's read-only target display: flat files show their `path`; SQL / Unity
 * Catalog show the dotted `catalog.schema.table` (only the parts present). Mirrors
 * the suite-target shapes in `suiteTarget.ts`; returns `null` for a targetless
 * (not-yet-runnable) suite.
 */
function summarizeTarget(target: Record<string, unknown> | null): string | null {
  if (!target) return null;
  const path = targetString(target, 'path');
  if (path) return path;
  const parts = [
    targetString(target, 'catalog'),
    targetString(target, 'schema'),
    targetString(target, 'table'),
  ].filter((p): p is string => Boolean(p));
  return parts.length > 0 ? parts.join('.') : null;
}

/**
 * Run-now panel — a cross-suite run launcher (suite picker + env/datasource
 * readout + a Week-6 notification-target placeholder). Distinct from the
 * suite-detail Run button (which runs *one* suite in context): here the user
 * picks any suite they can run from the Results surface. On trigger it hands off
 * to the shared `LiveRunProgress` drawer, so the modal closes and the run is
 * watched check-by-check.
 *
 * Self-contained (owns its own data fetch + progress drawer) so the Week-6 UI
 * rework can drop it onto the dedicated Execution page unchanged.
 */
export function RunNowPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  // The live-progress drawer opens on the queued run; the modal closes first so
  // the two aren't stacked. Carries the suite name for the drawer title.
  const [progress, setProgress] = useState<{ run: Run; suiteName: string } | null>(null);

  return (
    <>
      <Modal
        open={open}
        onCancel={onClose}
        title="Run now"
        footer={null}
        destroyOnClose
        width={520}
      >
        {/* Mount the body only while open so each open refetches the suite /
            connection lists — a suite created since last open shows up. */}
        {open && (
          <RunNowForm
            onCancel={onClose}
            onQueued={(run, suiteName) => {
              onClose();
              setProgress({ run, suiteName });
            }}
          />
        )}
      </Modal>
      <LiveRunProgress
        runId={progress?.run.id ?? null}
        suiteName={progress?.suiteName ?? null}
        // Every runnable suite is edit+ (the picker filters to RUNNABLE), and cancel
        // is the same edit capability — so the launched run is always cancellable.
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
  onQueued: (run: Run, suiteName: string) => void;
  onCancel: () => void;
}) {
  const { message } = App.useApp();
  const { state: suitesState } = useAsyncData(listSuites);
  const { state: connState } = useAsyncData(listConnections);
  const [suiteId, setSuiteId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // Ref guard (not just `running`) so a double-click can't dispatch two runs in
  // the tick before the button disables — same pattern as the suite-detail Run.
  const runningRef = useRef(false);

  const runnable = useMemo(
    () => (suitesState.status === 'ok' ? suitesState.data.filter(canRun) : []),
    [suitesState],
  );
  const connById = useMemo(() => {
    const map = new Map<string, Connection>();
    if (connState.status === 'ok') {
      for (const c of connState.data) map.set(c.id, c);
    }
    return map;
  }, [connState]);

  if (suitesState.status === 'loading') return <Spin tip="Loading suites…" />;
  if (suitesState.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        message="Failed to load suites"
        description={suitesState.error}
      />
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

  const onRun = async () => {
    if (!suite || runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    try {
      const run = await runSuite(suite.id);
      message.success(`${suite.name}: run queued`);
      onQueued(run, suite.name);
    } catch (err) {
      message.error(`Run failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  };

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
          message="This suite has no run target"
          description="Set a run target (edit the suite) before running it."
        />
      )}

      {/* Notification target — the per-run alert destination. No notification
          backend exists yet (Teams/ResultPublisher alert routing is Week 6), so
          this ships as a clearly-labelled disabled placeholder rather than a
          control that does nothing. */}
      <Flex vertical gap={6}>
        <Typography.Text type="secondary">Notify on completion</Typography.Text>
        <Tooltip title="Alert routing arrives in Week 6">
          <Select disabled placeholder="Teams channel (coming in Week 6)" />
        </Tooltip>
      </Flex>

      <Flex gap={8} justify="flex-end">
        <Button onClick={onCancel}>Cancel</Button>
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          disabled={!runnableNow}
          loading={running}
          onClick={onRun}
        >
          Run
        </Button>
      </Flex>
    </Flex>
  );
}
