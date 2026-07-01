import { App, Alert, Button, Drawer, Empty, Flex, Progress, Spin, Tag, Typography } from 'antd';
import SimpleList from '../SimpleList';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  cancelRun,
  getRunProgress,
  type ResultStatus,
  type RunProgress,
  type RunStatus,
} from '../../api/runs';
import { RESULT_STATUS_COLORS, RUN_BAR_STATUS, RUN_STATUS_COLORS } from '../results/resultsFormat';

/** Run lifecycle states past which polling stops. */
const TERMINAL: readonly RunStatus[] = ['succeeded', 'failed', 'cancelled'];
const DEFAULT_POLL_MS = 1500;

function isTerminal(status: RunStatus): boolean {
  return TERMINAL.includes(status);
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
      size={560}
      title={`Run progress${suiteName ? ` · ${suiteName}` : ''}`}
      destroyOnHidden
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
  // `stopped` latches once the run reaches a terminal state (poll-observed or
  // cancel-forced). It guards against a poll that was already in flight before a
  // cancel resolving afterwards and clobbering the terminal status back to
  // `running` (cancel is cooperative — the next poll can briefly still read the
  // pre-cancel state). Terminal is sticky.
  const stoppedRef = useRef(false);

  // Poll until terminal via a self-scheduling timeout (not setInterval, so a slow
  // request can't pile up overlapping fetches). `active` guards a late resolution
  // after unmount. A transient fetch error keeps polling (the live view
  // self-heals when the endpoint recovers) — only a terminal status stops it.
  useEffect(() => {
    let active = true;
    stoppedRef.current = false;
    const tick = async () => {
      try {
        const next = await getRunProgress(runId);
        if (!active || stoppedRef.current) return;
        setProgress(next);
        setError(null);
        if (isTerminal(next.status)) {
          stoppedRef.current = true;
        } else {
          timerRef.current = setTimeout(tick, pollMs);
        }
      } catch (err) {
        if (!active || stoppedRef.current) return;
        setError(err instanceof Error ? err.message : String(err));
        // Keep polling through a transient error rather than freezing the live
        // view; the cadence is bounded by pollMs, and a terminal status / unmount
        // still stops it.
        timerRef.current = setTimeout(tick, pollMs);
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
      // Stop polling and latch terminal so an in-flight pre-cancel poll can't
      // flip the status back to `running`.
      stoppedRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
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
        <Alert type="error" showIcon title="Failed to load run progress" description={error} />
      );
    }
    return <Spin description="Starting run…" size="large" />;
  }

  const { status, total_checks, completed_checks, counts, checks } = progress;
  const terminal = isTerminal(status);
  const percent = total_checks > 0 ? Math.round((completed_checks / total_checks) * 100) : 0;
  // Per-status histogram of resolved checks (#316) — show only non-zero buckets;
  // an all-pending run has none yet, so the row stays empty until results land.
  const tallies = Object.entries(counts).filter(([, n]) => n > 0) as [ResultStatus, number][];

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

      <Progress percent={percent} status={RUN_BAR_STATUS[status]} />

      {tallies.length > 0 && (
        <Flex gap={6} wrap>
          {tallies.map(([s, n]) => (
            <Tag key={s} color={RESULT_STATUS_COLORS[s]}>
              {s} · {n}
            </Tag>
          ))}
        </Flex>
      )}

      {/* A transient poll error while we still have prior progress to show. */}
      {error && (
        <Alert type="warning" showIcon title="Progress update failed" description={error} />
      )}

      {checks.length === 0 ? (
        <Empty description="This suite has no checks to run." />
      ) : (
        <SimpleList<(typeof checks)[number]>
          size="small"
          dataSource={checks}
          rowKey="check_id"
          renderItem={(c) => (
            <SimpleList.Item>
              <Typography.Text>{c.name}</Typography.Text>
              <CheckStatus status={c.status} terminal={terminal} />
            </SimpleList.Item>
          )}
        />
      )}

      {/* Always offer the persistent results surface — the drawer can be closed
          mid-run, and (unlike the old navigate-on-run) it's the only in-app path
          back to this run until the recent-runs table lands. */}
      <Link to="/results">View full results →</Link>
    </Flex>
  );
}

/**
 * A check's status cell: a resolved check shows its severity tag; a pending
 * check spins *while the run is live*, but on a terminal run (a check that never
 * produced a result — e.g. a cancelled run) it shows a neutral "not run" rather
 * than an eternal spinner.
 */
function CheckStatus({ status, terminal }: { status: ResultStatus | null; terminal: boolean }) {
  if (status === null) {
    if (terminal) {
      return <Typography.Text type="secondary">not run</Typography.Text>;
    }
    return (
      <Flex gap={6} align="center">
        <Spin size="small" />
        <Typography.Text type="secondary">pending</Typography.Text>
      </Flex>
    );
  }
  return <Tag color={RESULT_STATUS_COLORS[status]}>{status}</Tag>;
}
