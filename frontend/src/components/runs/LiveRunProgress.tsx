import {
  App,
  Alert,
  Button,
  Drawer,
  Empty,
  Flex,
  List,
  Progress,
  Spin,
  Tag,
  Typography,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  cancelRun,
  getRunProgress,
  type ResultStatus,
  type RunProgress,
  type RunStatus,
} from '../../api/runs';
import { RESULT_STATUS_COLORS, RUN_STATUS_COLORS } from '../results/resultsFormat';

/** Run lifecycle states past which polling stops. */
const TERMINAL: readonly RunStatus[] = ['succeeded', 'failed', 'cancelled'];
const DEFAULT_POLL_MS = 1500;

function isTerminal(status: RunStatus): boolean {
  return TERMINAL.includes(status);
}

/** antd Progress bar status from the run lifecycle. */
function barStatus(status: RunStatus): 'success' | 'exception' | 'active' | 'normal' {
  if (status === 'succeeded') return 'success';
  if (status === 'failed' || status === 'cancelled') return 'exception';
  if (status === 'running') return 'active';
  return 'normal';
}

/**
 * Live run-progress drawer — opens on a queued run and polls
 * `GET /runs/{id}/progress` until the run is terminal, showing the run
 * lifecycle, a completed/total bar, and per-check status (a spinner while a
 * check is still pending). An editor can cancel an in-flight run.
 *
 * Mounted controlled by `runId`; pass `null` to keep it closed. The body is
 * keyed by `runId` so opening a different run remounts and restarts polling
 * rather than continuing the prior run's loop.
 */
export function LiveRunProgress({
  runId,
  suiteName,
  canManage,
  pollMs = DEFAULT_POLL_MS,
  onClose,
}: {
  runId: string | null;
  suiteName: string | null;
  canManage: boolean;
  /** Poll interval; overridable so tests can drive it deterministically. */
  pollMs?: number;
  onClose: () => void;
}) {
  return (
    <Drawer
      open={runId !== null}
      onClose={onClose}
      width={560}
      title={`Run progress${suiteName ? ` · ${suiteName}` : ''}`}
      destroyOnClose
    >
      {runId !== null && (
        <LiveRunProgressBody key={runId} runId={runId} canManage={canManage} pollMs={pollMs} />
      )}
    </Drawer>
  );
}

function LiveRunProgressBody({
  runId,
  canManage,
  pollMs,
}: {
  runId: string;
  canManage: boolean;
  pollMs: number;
}) {
  const { message } = App.useApp();
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Poll until terminal via a self-scheduling timeout (not setInterval, so a slow
  // request can't pile up overlapping fetches). `active` guards a late resolution
  // after unmount. Polling stops on a terminal status or a fetch error — the
  // latter surfaces an alert rather than hot-looping a broken endpoint.
  useEffect(() => {
    let active = true;
    const tick = async () => {
      try {
        const next = await getRunProgress(runId);
        if (!active) return;
        setProgress(next);
        setError(null);
        if (!isTerminal(next.status)) {
          timerRef.current = setTimeout(tick, pollMs);
        }
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    };
    void tick();
    return () => {
      active = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [runId, pollMs]);

  const onCancel = async () => {
    setCancelling(true);
    try {
      const run = await cancelRun(runId);
      // Reflect the terminal state immediately; the in-flight poll (if any) will
      // also resolve to it and stop.
      setProgress((p) => (p ? { ...p, status: run.status, finished_at: run.finished_at } : p));
      message.success('Run cancelled');
    } catch (err) {
      message.error(`Cancel failed: ${err instanceof Error ? err.message : 'unknown error'}`);
    } finally {
      setCancelling(false);
    }
  };

  if (!progress) {
    if (error) {
      return (
        <Alert type="error" showIcon message="Failed to load run progress" description={error} />
      );
    }
    return <Spin tip="Starting run…" size="large" />;
  }

  const { status, total_checks, completed_checks, checks } = progress;
  const terminal = isTerminal(status);
  const percent = total_checks > 0 ? Math.round((completed_checks / total_checks) * 100) : 0;

  return (
    <Flex vertical gap={16}>
      <Flex gap={12} align="center" wrap>
        <Tag color={RUN_STATUS_COLORS[status]}>{status}</Tag>
        <Typography.Text type="secondary">
          {completed_checks} / {total_checks} checks
        </Typography.Text>
        {canManage && !terminal && (
          <Button danger size="small" loading={cancelling} onClick={onCancel}>
            Cancel
          </Button>
        )}
      </Flex>

      <Progress percent={percent} status={barStatus(status)} />

      {/* A transient poll error while we still have prior progress to show. */}
      {error && (
        <Alert type="warning" showIcon message="Progress update failed" description={error} />
      )}

      {checks.length === 0 ? (
        <Empty description="This suite has no checks to run." />
      ) : (
        <List<(typeof checks)[number]>
          size="small"
          dataSource={checks}
          rowKey="check_id"
          renderItem={(c) => (
            <List.Item>
              <Typography.Text>{c.name}</Typography.Text>
              <CheckStatus status={c.status} />
            </List.Item>
          )}
        />
      )}

      {terminal && <Link to="/results">View full results →</Link>}
    </Flex>
  );
}

/** A pending check shows a spinner; a resolved one its severity tag. */
function CheckStatus({ status }: { status: ResultStatus | null }) {
  if (status === null) {
    return (
      <Flex gap={6} align="center">
        <Spin size="small" />
        <Typography.Text type="secondary">pending</Typography.Text>
      </Flex>
    );
  }
  return <Tag color={RESULT_STATUS_COLORS[status]}>{status}</Tag>;
}
