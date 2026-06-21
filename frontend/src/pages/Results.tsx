import { PlayCircleOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Drawer,
  Empty,
  Flex,
  Select,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';

import {
  getRun,
  listPipelineRuns,
  listRuns,
  type PipelineRun,
  type Result,
  type ResultStatus,
  type Run,
  type RunStatus,
  RUN_STATUSES,
} from '../api/runs';
import { type Check, listChecks, listSuites } from '../api/suites';
import { RunNowPanel } from '../components/runs/RunNowPanel';
import { useAsyncData } from '../hooks/useAsyncData';
import {
  formatDuration,
  formatTimestamp,
  pipelineStatusColor,
  RESULT_STATUS_COLORS,
  RUN_STATUS_COLORS,
} from '../components/results/resultsFormat';
import { ScalarValue } from '../components/results/ScalarValue';

const LIST_LIMIT = 200;

export function Results() {
  const [runNowOpen, setRunNowOpen] = useState(false);
  return (
    <Flex vertical gap={24}>
      <Flex justify="space-between" align="center" gap={12}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Results
        </Typography.Title>
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => setRunNowOpen(true)}>
          Run now
        </Button>
      </Flex>
      <RunNowPanel open={runNowOpen} onClose={() => setRunNowOpen(false)} />
      <Tabs
        defaultActiveKey="runs"
        items={[
          { key: 'runs', label: 'Runs', children: <RunsTab /> },
          { key: 'pipelines', label: 'Pipeline runs', children: <PipelineRunsTab /> },
        ]}
      />
    </Flex>
  );
}

// ───────────────────────────── Runs tab ─────────────────────────────

function RunsTab() {
  // Fetch a page of runs + the accessible suites (for id→name), then filter by
  // status client-side — cheap at this volume and avoids a refetch per filter.
  const { state } = useAsyncData(() => listRuns({ limit: LIST_LIMIT }));
  const { state: suitesState } = useAsyncData(listSuites);
  const [status, setStatus] = useState<RunStatus | 'all'>('all');
  const [openRun, setOpenRun] = useState<Run | null>(null);

  const suiteNames = useMemo(() => {
    const map = new Map<string, string>();
    if (suitesState.status === 'ok') {
      for (const s of suitesState.data) map.set(s.id, s.name);
    }
    return map;
  }, [suitesState]);

  if (state.status === 'loading') return <Spin tip="Loading runs…" size="large" />;
  if (state.status === 'error') {
    return <Alert type="error" showIcon message="Failed to load runs" description={state.error} />;
  }

  const runs = status === 'all' ? state.data : state.data.filter((r) => r.status === status);

  const columns: ColumnsType<Run> = [
    {
      title: 'Suite',
      dataIndex: 'suite_id',
      render: (suiteId: string) =>
        suiteNames.get(suiteId) ?? <Typography.Text code>{suiteId.slice(0, 8)}</Typography.Text>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 120,
      render: (s: RunStatus) => <Tag color={RUN_STATUS_COLORS[s]}>{s}</Tag>,
    },
    { title: 'Triggered by', dataIndex: 'triggered_by', render: (t: string | null) => t ?? '—' },
    {
      title: 'Started',
      dataIndex: 'started_at',
      render: (t: string | null) => formatTimestamp(t),
    },
    {
      title: 'Duration',
      width: 110,
      render: (_: unknown, run: Run) => formatDuration(run.started_at, run.finished_at),
    },
  ];

  return (
    <Flex vertical gap={16}>
      <Flex gap={12} align="center">
        <Typography.Text type="secondary">Status</Typography.Text>
        <Select<RunStatus | 'all'>
          value={status}
          onChange={setStatus}
          style={{ width: 160 }}
          options={[
            { value: 'all', label: 'All' },
            ...RUN_STATUSES.map((s) => ({ value: s, label: s })),
          ]}
        />
      </Flex>
      <Table<Run>
        rowKey="id"
        columns={columns}
        dataSource={runs}
        pagination={false}
        locale={{ emptyText: <Empty description="No runs yet." /> }}
        onRow={(run) => ({ onClick: () => setOpenRun(run), style: { cursor: 'pointer' } })}
      />
      <RunDetailDrawer
        run={openRun}
        suiteName={openRun ? (suiteNames.get(openRun.suite_id) ?? null) : null}
        onClose={() => setOpenRun(null)}
      />
    </Flex>
  );
}

// ─────────────────────────── Run detail drawer ──────────────────────

function RunDetailDrawer({
  run,
  suiteName,
  onClose,
}: {
  run: Run | null;
  suiteName: string | null;
  onClose: () => void;
}) {
  return (
    <Drawer
      open={run !== null}
      onClose={onClose}
      width={640}
      title={run ? `Run · ${suiteName ?? run.suite_id.slice(0, 8)}` : 'Run'}
      destroyOnClose
    >
      {/* Keyed by run id so switching runs (without closing) remounts and
          refetches — useAsyncData only fetches on mount/reload, not on prop
          change, so without the key the drawer would show the prior run. */}
      {run && <RunDetailBody key={run.id} run={run} />}
    </Drawer>
  );
}

function RunDetailBody({ run }: { run: Run }) {
  // Remounted per run via the `key` at the call site → these fetch fresh.
  const { state } = useAsyncData(() => getRun(run.id));
  const { state: checksState } = useAsyncData(() => listChecks(run.suite_id));

  const checks = useMemo(() => {
    const map = new Map<string, Check>();
    if (checksState.status === 'ok') {
      for (const c of checksState.data) map.set(c.id, c);
    }
    return map;
  }, [checksState]);

  return (
    <Flex vertical gap={16}>
      <Flex gap={16} wrap>
        <Meta label="Status">
          <Tag color={RUN_STATUS_COLORS[run.status]}>{run.status}</Tag>
        </Meta>
        <Meta label="Triggered by">{run.triggered_by ?? '—'}</Meta>
        <Meta label="Started">{formatTimestamp(run.started_at)}</Meta>
        <Meta label="Duration">{formatDuration(run.started_at, run.finished_at)}</Meta>
      </Flex>

      {state.status === 'loading' && <Spin tip="Loading results…" />}
      {state.status === 'error' && (
        <Alert type="error" showIcon message="Failed to load results" description={state.error} />
      )}
      {state.status === 'ok' && <ResultsTable results={state.data.results} checks={checks} />}
    </Flex>
  );
}

function ResultsTable({ results, checks }: { results: Result[]; checks: Map<string, Check> }) {
  if (results.length === 0) {
    return <Empty description="No check results — the run did not complete." />;
  }
  const columns: ColumnsType<(typeof results)[number]> = [
    {
      title: 'Check',
      dataIndex: 'check_id',
      render: (id: string) =>
        checks.get(id)?.name ?? <Typography.Text code>{id.slice(0, 8)}</Typography.Text>,
    },
    {
      title: 'Expectation',
      dataIndex: 'check_id',
      render: (id: string) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {checks.get(id)?.expectation_type ?? '—'}
        </Typography.Text>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (s: ResultStatus) => <Tag color={RESULT_STATUS_COLORS[s]}>{s}</Tag>,
    },
    {
      title: 'Metric',
      dataIndex: 'metric_value',
      width: 90,
      render: (v: number | null) => (v === null ? '—' : v),
    },
    {
      title: 'Observed',
      dataIndex: 'observed_value',
      render: (v: Record<string, unknown> | null) => <ScalarValue value={v} />,
    },
  ];
  return (
    <Table rowKey="id" size="small" columns={columns} dataSource={results} pagination={false} />
  );
}

// ─────────────────────────── Pipeline runs tab ──────────────────────

function PipelineRunsTab() {
  const { state } = useAsyncData(() => listPipelineRuns({ limit: LIST_LIMIT }));
  const [provider, setProvider] = useState<'all' | 'adf' | 'airflow'>('all');

  if (state.status === 'loading') return <Spin tip="Loading pipeline runs…" size="large" />;
  if (state.status === 'error') {
    return (
      <Alert
        type="error"
        showIcon
        message="Failed to load pipeline runs"
        description={state.error}
      />
    );
  }

  const rows = provider === 'all' ? state.data : state.data.filter((p) => p.provider === provider);

  const columns: ColumnsType<PipelineRun> = [
    { title: 'Provider', dataIndex: 'provider', width: 110, render: (p: string) => <Tag>{p}</Tag> },
    { title: 'Pipeline / DAG', dataIndex: 'pipeline_or_dag_id' },
    { title: 'Env', dataIndex: 'env', width: 80, render: (e: string) => e.toUpperCase() },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => <Tag color={pipelineStatusColor(s)}>{s}</Tag>,
    },
    {
      title: 'Started',
      dataIndex: 'started_at',
      render: (t: string | null) => formatTimestamp(t),
    },
    {
      title: 'Failure reason',
      dataIndex: 'failure_reason',
      render: (r: string | null) => r ?? '—',
    },
  ];

  return (
    <Flex vertical gap={16}>
      <Flex gap={12} align="center">
        <Typography.Text type="secondary">Provider</Typography.Text>
        <Select<'all' | 'adf' | 'airflow'>
          value={provider}
          onChange={setProvider}
          style={{ width: 160 }}
          options={[
            { value: 'all', label: 'All' },
            { value: 'adf', label: 'ADF' },
            { value: 'airflow', label: 'Airflow' },
          ]}
        />
      </Flex>
      <Table<PipelineRun>
        rowKey="id"
        columns={columns}
        dataSource={rows}
        pagination={false}
        locale={{ emptyText: <Empty description="No pipeline runs monitored yet." /> }}
      />
    </Flex>
  );
}

// ───────────────────────────── shared ───────────────────────────────

function Meta({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Flex vertical gap={2}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {label}
      </Typography.Text>
      <span>{children}</span>
    </Flex>
  );
}
