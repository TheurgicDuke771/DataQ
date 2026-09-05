import {
  AlertOutlined,
  AppstoreOutlined,
  PlayCircleOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import { Alert, Button, Col, Flex, Row, Tag, Typography } from 'antd';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import {
  type AdminHealth,
  type SecretSweepReport,
  getAdminHealth,
  getAdminOverview,
  getSecretSweep,
  runSecretSweep,
} from '../../api/admin';
import { type TriggerEnvNearMiss, listEnvNearMisses } from '../../api/triggerBindings';
import { MetricCard } from '../../components/dashboard/MetricCard';
import { formatTimestamp } from '../../components/results/resultsFormat';
import { type AsyncState, useAsyncData } from '../../hooks/useAsyncData';
import { fetchFailure } from '../../utils/errors';
import {
  type OverviewSignal,
  healthSignals,
  nearMissSignals,
  orderSignals,
  sourceErrorSignal,
  sweepSignals,
} from './overviewSignals';
import { Section } from './parts';
import { awaitSweepRun } from './sweepPolling';
import { STATUS_TAG, useAuditChainVerify } from './useAuditChainVerify';

/** The workspace's morning page: four counts, everything that needs attention, and a health
 *  checklist. A signal whose source errored or has observed nothing renders as unknown / not
 *  monitored — never as healthy, and never silently absent. */
export function AdminOverview() {
  const overview = useAsyncData(getAdminOverview);
  const health = useAsyncData(getAdminHealth);
  const sweep = useAsyncData(getSecretSweep);
  const nearMisses = useAsyncData(() => listEnvNearMisses());
  const chain = useAuditChainVerify();
  const [freshSweep, setFreshSweep] = useState<SecretSweepReport | null>(null);

  const sweepReport = freshSweep ?? (sweep.state.status === 'ok' ? sweep.state.data : null);
  const counts = overview.state.status === 'ok' ? overview.state.data : null;
  const countError = overview.state.status === 'error' ? overview.state.error : null;

  return (
    <Flex vertical gap={16}>
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Members"
            value={counts?.members.total ?? null}
            loading={overview.state.status === 'loading'}
            icon={<TeamOutlined />}
            footnote={
              countError
                ? `Could not load: ${countError}`
                : counts
                  ? counts.members.pending_first_signin === null
                    ? 'Pending first sign-in is not tracked yet'
                    : `${counts.members.pending_first_signin} pending first sign-in`
                  : undefined
            }
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Suites"
            value={counts?.suites.total ?? null}
            loading={overview.state.status === 'loading'}
            icon={<AppstoreOutlined />}
            footnote={
              countError
                ? `Could not load: ${countError}`
                : counts
                  ? `across ${counts.suites.connections} connection(s)`
                  : undefined
            }
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Open incidents"
            value={counts?.incidents.open ?? null}
            loading={overview.state.status === 'loading'}
            icon={<AlertOutlined />}
            footnote={
              countError
                ? `Could not load: ${countError}`
                : counts
                  ? `${counts.incidents.acknowledged} acknowledged (still open)`
                  : undefined
            }
          />
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <MetricCard
            label="Runs today"
            value={counts?.runs_today.total ?? null}
            loading={overview.state.status === 'loading'}
            icon={<PlayCircleOutlined />}
            footnote={
              countError
                ? `Could not load: ${countError}`
                : counts
                  ? `${counts.runs_today.succeeded} succeeded · ${counts.runs_today.failed} failed · ${counts.runs_today.running} running (UTC day)`
                  : undefined
            }
          />
        </Col>
      </Row>

      <NeedsAttention
        health={health.state}
        sweep={sweepReport}
        sweepState={sweep.state}
        nearMisses={nearMisses.state}
      />

      <Section title="Workspace health">
        <ChainItem chain={chain} />
        <SchedulerItem state={health.state} />
        <SecretStoreItem
          state={sweep.state}
          report={sweepReport}
          onRan={(report) => setFreshSweep(report)}
        />
        <PollingItem state={health.state} />
      </Section>
    </Flex>
  );
}

function NeedsAttention({
  health,
  sweep,
  sweepState,
  nearMisses,
}: {
  health: AsyncState<AdminHealth>;
  sweep: SecretSweepReport | null;
  sweepState: AsyncState<SecretSweepReport>;
  nearMisses: AsyncState<TriggerEnvNearMiss[]>;
}) {
  const loading =
    health.status === 'loading' ||
    sweepState.status === 'loading' ||
    nearMisses.status === 'loading';
  const signals: OverviewSignal[] = [];

  if (health.status === 'ok') signals.push(...healthSignals(health.data));
  if (health.status === 'error')
    signals.push(sourceErrorSignal('health', 'Workspace health', health.error, '/admin/overview'));

  if (sweep) signals.push(...sweepSignals(sweep));
  if (sweepState.status === 'error')
    signals.push(
      sourceErrorSignal(
        'sweep',
        'The orphan-secret sweep report',
        sweepState.error,
        '#secret-store',
      ),
    );

  if (nearMisses.status === 'ok') signals.push(...nearMissSignals(nearMisses.data));
  if (nearMisses.status === 'error')
    signals.push(
      sourceErrorSignal('near-misses', 'Trigger env mismatches', nearMisses.error, '/suites'),
    );

  const ordered = orderSignals(signals);

  return (
    <Section title="Needs attention">
      {loading && <Typography.Text type="secondary">Loading signals…</Typography.Text>}
      {!loading && ordered.length === 0 && (
        <Typography.Text type="secondary">
          Nothing needs attention among the signals below. Only what the health checklist lists is
          watched — anything not listed there is unmonitored, not clear.
        </Typography.Text>
      )}
      <Flex vertical gap={12}>
        {ordered.map((signal) => (
          <SignalRow key={signal.key} signal={signal} />
        ))}
      </Flex>
    </Section>
  );
}

function SignalRow({ signal }: { signal: OverviewSignal }) {
  return (
    <Flex justify="space-between" align="flex-start" gap={12} wrap>
      <Flex vertical gap={2} style={{ flex: '1 1 320px' }}>
        <Flex align="center" gap={8} wrap>
          <Tag color={signal.tone === 'attention' ? 'red' : 'default'}>
            {signal.tone === 'attention' ? 'Needs action' : 'Not monitored'}
          </Tag>
          <Typography.Text strong>{signal.title}</Typography.Text>
        </Flex>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {signal.detail}
        </Typography.Text>
      </Flex>
      {signal.to.startsWith('#') ? (
        <Typography.Link href={signal.to}>{signal.verb}</Typography.Link>
      ) : (
        <Link to={signal.to}>{signal.verb}</Link>
      )}
    </Flex>
  );
}

/** One row of the health checklist: a title, a status tag, detail, and its verb. */
function HealthItem({
  id,
  title,
  tag,
  detail,
  action,
}: {
  id?: string;
  title: string;
  tag: { color: string; label: string };
  detail: ReactNode;
  action?: ReactNode;
}) {
  return (
    <Flex id={id} justify="space-between" align="flex-start" gap={12} wrap>
      <Flex vertical gap={2} style={{ flex: '1 1 320px' }}>
        <Flex align="center" gap={8} wrap>
          <Typography.Text strong>{title}</Typography.Text>
          <Tag color={tag.color}>{tag.label}</Tag>
        </Flex>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {detail}
        </Typography.Text>
      </Flex>
      {action}
    </Flex>
  );
}

const NOT_CHECKED = { color: 'default', label: 'Not verified this session' };

function ChainItem({ chain }: { chain: ReturnType<typeof useAuditChainVerify> }) {
  const { state, verify } = chain;
  const tag =
    state.status === 'done'
      ? STATUS_TAG[state.result.status]
      : state.status === 'failed'
        ? { color: 'orange', label: 'Not verified — the check failed' }
        : NOT_CHECKED;
  return (
    <HealthItem
      id="audit-chain"
      title="Audit chain"
      tag={tag}
      detail={
        state.status === 'failed'
          ? `${state.failure.message} — this says nothing about whether the chain is intact, only that the check did not complete.`
          : state.status === 'done'
            ? `${state.result.verified_count} event(s) hashed; checked ${formatTimestamp(state.checkedAt)}.`
            : 'Verification reads the whole hashed set, so it never runs on page load. Until you ask, the chain state is unknown.'
      }
      action={
        <Button onClick={verify} loading={state.status === 'running'}>
          Verify now
        </Button>
      }
    />
  );
}

const BEAT_TAG = {
  alive: { color: 'green', label: 'Beat alive' },
  stale: { color: 'red', label: 'Beat stale' },
  not_monitored: { color: 'default', label: 'Not monitored' },
};

function SchedulerItem({ state }: { state: AsyncState<AdminHealth> }) {
  if (state.status !== 'ok') {
    return (
      <HealthItem
        id="scheduler"
        title="Scheduler & worker"
        tag={
          state.status === 'loading'
            ? { color: 'default', label: 'Loading' }
            : { color: 'orange', label: 'Unknown — could not load' }
        }
        detail={
          state.status === 'error'
            ? `${state.error} — the heartbeat and queue depths are unknown, not healthy.`
            : 'Reading the beat heartbeat and broker queue depths…'
        }
      />
    );
  }
  const { beat, queues, queues_error: queuesError } = state.data;
  return (
    <HealthItem
      id="scheduler"
      title="Scheduler & worker"
      tag={BEAT_TAG[beat.status]}
      detail={
        <>
          {beat.status === 'not_monitored'
            ? 'The heartbeat task has never recorded a tick, so nothing is known about beat.'
            : `Last beat tick ${formatTimestamp(beat.last_tick_at)}.`}{' '}
          {queues === null
            ? `Queue depth unknown — the broker could not be reached, so it is not zero. ${queuesError ?? ''}`
            : `Queue depth: ${queues.map((q) => `${q.name} ${q.depth}`).join(' · ')}.`}
        </>
      }
    />
  );
}

function SecretStoreItem({
  state,
  report,
  onRan,
}: {
  state: AsyncState<SecretSweepReport>;
  report: SecretSweepReport | null;
  onRan: (report: SecretSweepReport) => void;
}) {
  const [runState, setRunState] = useState<'idle' | 'running' | 'queued'>('idle');
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunState('running');
    setError(null);
    try {
      await runSecretSweep();
    } catch (err) {
      setRunState('idle');
      setError(fetchFailure(err).message);
      return;
    }
    const fresh = await awaitSweepRun(report?.ran_at ?? null);
    if (fresh) {
      onRan(fresh);
      setRunState('idle');
    } else {
      // The enqueue succeeded; the worker just hasn't recorded a report yet. Saying so beats
      // showing the previous run's numbers as if they were this run's.
      setRunState('queued');
    }
  };

  const tag =
    state.status === 'error'
      ? { color: 'orange', label: 'Unknown — could not load' }
      : !report
        ? { color: 'default', label: 'Loading' }
        : report.status === 'never_run'
          ? { color: 'default', label: 'Never run' }
          : report.status === 'skipped' || report.orphan_count === null
            ? { color: 'default', label: 'Not enumerated' }
            : report.orphan_count > 0
              ? { color: 'red', label: `${report.orphan_count} orphan(s)` }
              : { color: 'green', label: 'No orphans' };

  return (
    <HealthItem
      id="secret-store"
      title="Secret store"
      tag={tag}
      detail={
        <Flex vertical gap={4}>
          <span>{sweepDetail(state, report)}</span>
          {runState === 'queued' && (
            <Alert
              type="info"
              showIcon
              title="Sweep queued"
              description="The run was accepted but no new report has landed yet. Refresh this page later to see it — the numbers above are still the previous run's."
            />
          )}
          {error && (
            <Alert type="error" showIcon title="Could not start the sweep" description={error} />
          )}
        </Flex>
      }
      action={
        <Button onClick={run} loading={runState === 'running'}>
          Run sweep
        </Button>
      }
    />
  );
}

function sweepDetail(
  state: AsyncState<SecretSweepReport>,
  report: SecretSweepReport | null,
): string {
  if (state.status === 'error')
    return `${state.error} — the number of unowned secrets is unknown, not zero.`;
  if (!report) return 'Reading the last sweep report…';
  if (report.status === 'never_run')
    return 'No sweep report has ever been recorded, so the number of unowned secrets is unknown — not zero.';
  if (report.status === 'skipped' || report.orphan_count === null)
    return `The last run recorded no count, so nothing is known about unowned secrets. ${report.error ?? ''}`.trim();
  return `${report.orphan_count} unowned secret(s) of ${report.scanned ?? '—'} scanned in ${report.store ?? 'the store'}, recorded ${formatTimestamp(report.ran_at)}. ${report.unknown_age_count ?? 0} could not be dated and ${report.too_young_count ?? 0} were too young to judge.`;
}

function PollingItem({ state }: { state: AsyncState<AdminHealth> }) {
  const action = <Link to="/admin/integrations">Details</Link>;
  if (state.status !== 'ok') {
    return (
      <HealthItem
        title="Orchestration polling"
        tag={
          state.status === 'loading'
            ? { color: 'default', label: 'Loading' }
            : { color: 'orange', label: 'Unknown — could not load' }
        }
        detail={
          state.status === 'error'
            ? `${state.error} — poll staleness is unknown, not on cadence.`
            : 'Reading per-connection poll staleness…'
        }
        action={action}
      />
    );
  }
  const rows = state.data.polling;
  const bad = rows.filter((r) => r.status === 'stalled' || r.status === 'failing').length;
  const unknown = rows.filter((r) => r.status === 'unknown').length;
  const tag =
    rows.length === 0
      ? { color: 'default', label: 'Nothing to poll' }
      : bad > 0
        ? { color: 'red', label: `${bad} unhealthy` }
        : unknown > 0
          ? { color: 'default', label: `${unknown} never polled` }
          : { color: 'green', label: 'On cadence' };
  return (
    <HealthItem
      title="Orchestration polling"
      tag={tag}
      detail={
        rows.length === 0
          ? 'No orchestration connections are configured, so no pipeline completions reach DataQ by polling at all.'
          : `${rows.length} connection(s): ${bad} unhealthy, ${unknown} never polled (never polled is unknown, not healthy).`
      }
      action={action}
    />
  );
}
