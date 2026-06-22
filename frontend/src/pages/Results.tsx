import { PlayCircleOutlined } from '@ant-design/icons';
import { Alert, Button, Empty, Flex, Select, Spin, Table, Tabs, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import {
  listPipelineRuns,
  listRuns,
  type PipelineRun,
  type Run,
  type RunStatus,
  RUN_STATUSES,
} from '../api/runs';
import { listSuites } from '../api/suites';
import { RunNowPanel } from '../components/runs/RunNowPanel';
import { useAsyncData } from '../hooks/useAsyncData';
import {
  formatDuration,
  formatTimestamp,
  pipelineStatusColor,
  RUN_STATUS_COLORS,
} from '../components/results/resultsFormat';

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
  const navigate = useNavigate();
  const { state } = useAsyncData(() => listRuns({ limit: LIST_LIMIT }));
  const { state: suitesState } = useAsyncData(listSuites);
  const [status, setStatus] = useState<RunStatus | 'all'>('all');

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
        onRow={(run) => ({
          onClick: () => navigate(`/results/${run.id}`),
          style: { cursor: 'pointer' },
        })}
      />
    </Flex>
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
